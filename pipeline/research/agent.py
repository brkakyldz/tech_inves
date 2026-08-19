"""Research fan-out node: one Send() branch = one ticker or one macro topic.

Shared logic for both scope="company" and scope="macro" branches
(ARCHITECTURE_PROPOSAL.md §2.2b) -- only the prompt template and query
differ.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from pipeline.config import (
    RESEARCH_LOOKBACK_DAYS,
    RESEARCH_MAX_RETRIES,
    RESEARCH_RETRY_BACKOFF_SECONDS,
    domain_tier,
    load_watchlist_company_names,
)
from pipeline.research.federal_register import is_regulation_topic
from pipeline.research.prompts import (
    COMPANY_USER_PROMPT_TEMPLATE,
    MACRO_USER_PROMPT_TEMPLATE,
    RESEARCH_SYSTEM_PROMPT,
    format_search_results,
)
from pipeline.research.tavily_client import TavilySearcher
from pipeline.schemas import FailureNote, Finding, ResearchBranchInput

logger = logging.getLogger(__name__)

MAX_RETRIES = RESEARCH_MAX_RETRIES

# R11: substrings that mark a structured-output failure as unwinnable -- a
# content-policy refusal or a schema the model flatly won't satisfy -- as
# opposed to a transient error (timeout, rate limit, connection reset) that
# backoff-and-retry can plausibly fix. Matched case-insensitively against
# str(exc); deliberately conservative (few, high-precision markers) since a
# false positive here means the branch gives up early on something a retry
# would have fixed.
_REFUSAL_MARKERS = (
    "content_filter",
    "content filter",
    "i cannot assist",
    "i can't assist",
    "safety system",
)


def _is_refusal(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(marker in message for marker in _REFUSAL_MARKERS)


class FindingsBatch(BaseModel):
    """Structured-output container: with_structured_output needs a single
    model, not a bare list[Finding]."""

    findings: list[Finding] = Field(default_factory=list)


def _search_window(
    as_of: date | None, *, lookback_days: int = RESEARCH_LOOKBACK_DAYS
) -> tuple[str | None, str | None]:
    """R7: an explicit rolling [start, end] window ending on `as_of`, or
    `(None, None)` when no `as_of` was supplied (e.g. a test branch built
    without one) -- callers pass neither kwarg to the searcher in that case,
    which is the same as the old unpinned behaviour.

    `lookback_days` counts *both* endpoints, so the default of 7
    (`PIPELINE_RESEARCH_LOOKBACK_DAYS`, `pipeline/config.py`) reproduces the
    previously hardcoded `timedelta(days=6)` exactly. Values below 1 are
    clamped to 1 (a same-day window) rather than producing an inverted
    range that a provider would either reject or silently reinterpret.
    """
    if as_of is None:
        return None, None
    start = as_of - timedelta(days=max(lookback_days, 1) - 1)
    return start.isoformat(), as_of.isoformat()


def _build_query(branch: ResearchBranchInput) -> str:
    if branch.scope == "company":
        return f"{branch.ticker} stock news this week"
    return f"{branch.topic} technology sector impact"


def _build_queries(branch: ResearchBranchInput, company_names: dict[str, str] | None) -> list[str]:
    """R17: multi-query fan-out per branch. `_build_query`'s bare
    "{ticker} stock news this week" retrieves stock-price commentary, not
    company events -- a second query keyed on the ticker's resolved company
    name (`pipeline.config.load_watchlist_company_names`,
    data/WATCHLIST.md) targets actual news coverage instead.
    Falls back to just the base query if the ticker isn't in the map."""
    if branch.scope == "company":
        queries = [_build_query(branch)]
        company_name = (company_names or {}).get(branch.ticker or "")
        if company_name:
            queries.append(f"{company_name} earnings OR acquisition OR product launch this week")
        return queries
    return [_build_query(branch), f"{branch.topic} latest developments this week"]


def _build_prompt(branch: ResearchBranchInput, search_results: list[dict]) -> str:
    formatted = format_search_results(search_results)
    if branch.scope == "company":
        return COMPANY_USER_PROMPT_TEMPLATE.format(
            ticker=branch.ticker,
            covered_events_context=branch.covered_events_context or "(none)",
            search_results=formatted,
        )
    return MACRO_USER_PROMPT_TEMPLATE.format(
        topic=branch.topic,
        covered_events_context=branch.covered_events_context or "(none)",
        search_results=formatted,
    )


def run_research_branch(
    branch: ResearchBranchInput,
    *,
    searcher: TavilySearcher,
    llm: BaseChatModel,
    edgar_searcher: TavilySearcher | None = None,
    ir_feed_searcher: TavilySearcher | None = None,
    edgar_submissions_searcher: TavilySearcher | None = None,
    regulation_searcher: TavilySearcher | None = None,
    company_names: dict[str, str] | None = None,
    max_retries: int = RESEARCH_MAX_RETRIES,
    backoff_seconds: float = RESEARCH_RETRY_BACKOFF_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[list[Finding], list[FailureNote], set[str]]:
    """Run one research branch. Never raises -- failures become a FailureNote
    so a single branch's failure doesn't take down the fan-out
    (ARCHITECTURE_PROPOSAL.md §5.1).

    Retries use exponential backoff (backoff_seconds * 2**attempt) rather
    than LangGraph's node-level RetryPolicy: that policy re-raises after
    exhausting attempts, which would propagate out of the graph and abort
    the whole fan-out instead of isolating this one branch as a FailureNote.
    `sleep_fn` is injectable so tests don't actually wait.

    `edgar_searcher` (R16) is an additive per-company leg, not a
    replacement for `searcher` -- a company branch that has one queries it
    for primary filings (8-K, 10-Q, 10-K) alongside the main search, merges
    the results (de-duplicated by URL), and treats a failed/empty EDGAR leg
    as simply zero extra results, never a branch failure. Never invoked for
    scope="macro" (EDGAR full-text search is entity-scoped).

    `ir_feed_searcher` and `edgar_submissions_searcher` (ADR 0010 §8 items
    2-3, Faz 7b) are two more additive per-company legs, merged the same
    way as `edgar_searcher`: queried with the bare ticker, de-duplicated by
    URL against everything already collected, and a failed/empty/unmapped
    leg degrades to zero extra results rather than failing the branch.

    `regulation_searcher` (ADR 0010 §8 item 4, Faz 7b -- the Federal
    Register API) is the macro-scope counterpart: invoked only when
    `branch.scope == "macro"` and the topic matches
    `pipeline.research.federal_register.is_regulation_topic`, queried with
    the topic string, merged the same way.

    `company_names` (R17, ticker -> name) drives a second, differently
    -worded query for company branches; loaded once from
    data/WATCHLIST.md via `pipeline.config.load_watchlist_company_names`
    when not injected (tests inject an explicit dict instead of touching disk).
    """

    if company_names is None and branch.scope == "company":
        company_names = load_watchlist_company_names()
    queries = _build_queries(branch, company_names)
    start_date, end_date = _search_window(branch.as_of)
    search_kwargs = {} if start_date is None else {"start_date": start_date, "end_date": end_date}
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 2):
        try:
            results: list = []
            seen_urls: set = set()
            for q in queries:
                for r in searcher.search(q, **search_kwargs):
                    url = r.get("url") if isinstance(r, dict) else None
                    if url is not None and url in seen_urls:
                        continue
                    if url is not None:
                        seen_urls.add(url)
                    results.append(r)
            seen = {r.get("url") for r in results if isinstance(r, dict)}

            def _merge(extra_results) -> None:
                for r in extra_results:
                    if isinstance(r, dict) and r.get("url") not in seen:
                        results.append(r)
                        seen.add(r.get("url"))

            if branch.scope == "company":
                if edgar_searcher is not None:
                    _merge(edgar_searcher.search(f"{branch.ticker}", **search_kwargs))
                if ir_feed_searcher is not None:
                    _merge(ir_feed_searcher.search(f"{branch.ticker}", **search_kwargs))
                if edgar_submissions_searcher is not None:
                    _merge(edgar_submissions_searcher.search(f"{branch.ticker}", **search_kwargs))
            elif (
                branch.scope == "macro"
                and regulation_searcher is not None
                and is_regulation_topic(branch.topic)
            ):
                _merge(regulation_searcher.search(branch.topic or "", **search_kwargs))
            # R4: retrieved_urls is grounded in what Tavily/EDGAR actually
            # returned, not in what the LLM later claims as source_urls --
            # the model can name any URL it likes in structured output, and
            # building "retrieved" from its own claims made that
            # unfalsifiable.
            retrieved_urls = {r.get("url") for r in results if isinstance(r, dict) and r.get("url")}
            structured_llm = llm.with_structured_output(FindingsBatch)
            prompt = _build_prompt(branch, results)
            batch = structured_llm.invoke(
                [
                    ("system", RESEARCH_SYSTEM_PROMPT),
                    ("user", prompt),
                ]
            )
            findings = batch.findings if batch else []
            for f in findings:
                f.scope = branch.scope
                if branch.scope == "company":
                    f.ticker = branch.ticker
                    f.topic = None
                else:
                    f.ticker = None
                    f.topic = branch.topic
                kept = [u for u in f.source_urls if u in retrieved_urls]
                dropped = set(f.source_urls) - set(kept)
                if dropped:
                    logger.warning(
                        "research branch: dropping ungrounded citation(s) (never "
                        "retrieved) scope=%s ticker=%s topic=%s finding=%r urls=%s",
                        branch.scope,
                        branch.ticker,
                        branch.topic,
                        f.event_title,
                        sorted(dropped),
                    )
                f.source_urls = kept
                f.source_tier = min((domain_tier(u) for u in kept), default=None)
            return findings, [], retrieved_urls
        except Exception as exc:  # noqa: BLE001 - deliberate: isolate branch failure
            last_error = exc
            logger.warning(
                "research branch failed (scope=%s ticker=%s topic=%s attempt=%d): %s",
                branch.scope,
                branch.ticker,
                branch.topic,
                attempt,
                exc,
            )
            if _is_refusal(exc):
                # R11: a refusal won't succeed on retry -- backoff and a
                # second attempt just burns quota on something guaranteed to
                # fail the same way again.
                logger.warning(
                    "research branch: refusal detected, degrading immediately "
                    "without retry (scope=%s ticker=%s topic=%s)",
                    branch.scope,
                    branch.ticker,
                    branch.topic,
                )
                break
            if attempt <= max_retries:
                sleep_fn(backoff_seconds * (2 ** (attempt - 1)))

    failure = FailureNote(
        scope=branch.scope,
        ticker=branch.ticker,
        topic=branch.topic,
        reason=f"research failed after {attempt} attempt(s): {last_error}",
    )
    return [], [failure], set()
