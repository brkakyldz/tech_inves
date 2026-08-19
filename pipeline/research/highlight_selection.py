"""Pre-fan-out highlight selection (ADR 0006 §3).

Runs *before* `build_graph()`/`graph.invoke()` (`pipeline/run.py`): one
cheap, search-only Tavily call per watchlist ticker, ranked by how much
*fresh* material that call returns (`_rank_key`), clamped to the verifier's
3-4 completeness bound
(`pipeline.verifier.rules.COMPLETENESS_MIN_HIGHLIGHTS`/`_MAX_HIGHLIGHTS`).
Only the selected tickers get the full (search + LLM-extraction) company
research branch and a synthesis deep-dive -- the rest of the watchlist still
gets a scoring-table row (scoring is free/deterministic, ADR 0001) but no
qualitative research spend.

This replaces both prior mechanisms (the writer's free-form deep-dive choice
and `pipeline.storage.report_store.pick_highlight_tickers()`'s event-count +
composite-score tie-break, retired by R10) with **one** deterministic,
pre-fan-out selection, so site badges and deep-dive sections agree by
construction -- see `pipeline/run.py` and `pipeline/storage/report_store.py`.

No score input of any kind feeds this module -- not `composite_score`, not
as a fallback, not as a tie-breaker. ADR 0006 §3 rejects that explicitly.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Callable

from urllib.parse import urlparse

from pipeline.config import (
    HIGHLIGHT_PROBE_MAX_RESULTS,
    RESEARCH_MAX_RETRIES,
    RESEARCH_RETRY_BACKOFF_SECONDS,
    TRUSTED_DOMAINS,
    get_exa_api_key,
    get_tavily_api_key,
    load_watchlist_company_names,
    load_watchlist_tickers,
)
from pipeline.research.agent import _search_window
from pipeline.research.exa_client import FallbackSearcher, LiveExaSearcher
from pipeline.research.tavily_client import (
    TavilySearcher,
    TavilySearchResult,
    build_search_payload,
    parse_search_response,
)
from pipeline.schemas import CoveredEvent
from pipeline.storage.covered_events_store import covered_source_urls
from pipeline.verifier.rules import COMPLETENESS_MAX_HIGHLIGHTS, COMPLETENESS_MIN_HIGHLIGHTS

logger = logging.getLogger(__name__)


class CheapTavilySearcher:
    """Search-only probe for highlight pre-selection (ADR 0006 §3.1).

    Same Tavily backend and `TRUSTED_DOMAINS` allowlist as the full research
    branch's `LiveTavilySearcher` (`pipeline/research/tavily_client.py`), but
    deliberately cheaper: `search_depth="basic"` (never "advanced") and no
    attached LLM-extraction step anywhere in this module -- this call exists
    only to see *which URLs* a query returns, never to read their contents.
    Implements the same `TavilySearcher` Protocol so it slots into the same
    retry wrapper (`_probe_search` below) the rest of the pipeline already
    uses that protocol for.

    `max_results` defaults to `HIGHLIGHT_PROBE_MAX_RESULTS` rather than a
    literal: it is the ceiling the ranking signal saturates against, and
    Tavily bills per request, not per result.
    """

    def __init__(self, max_results: int = HIGHLIGHT_PROBE_MAX_RESULTS) -> None:
        from langchain_tavily import TavilySearch

        self._tool = TavilySearch(
            tavily_api_key=get_tavily_api_key(),
            max_results=max_results,
            topic="news",
            search_depth="basic",
            include_domains=TRUSTED_DOMAINS,
        )

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        return parse_search_response(
            self._tool.invoke(
                build_search_payload(query, start_date=start_date, end_date=end_date)
            )
        )


def _build_query(ticker: str, company_name: str | None) -> str:
    """Event-shaped, deliberately mirroring the *second* of the two queries
    the full research branch issues for a company
    (`pipeline.research.agent._build_queries`).

    The probe used to ask `{ticker} {name} news this week` -- broad news
    volume. The branch it predicts asks for earnings/acquisition/product
    events, and its LLM extraction step then keeps only material that is an
    *event*. Ranking on general news volume and then extracting events means
    the probe and the branch are measuring two different things, which is
    how ADBE and NOW came top-4 on 2026-08-19 and returned zero findings
    each: both are widely written about, neither did anything that week.

    "this week" is deliberately gone. The window is already pinned by
    `start_date`/`end_date`; repeating it as query text only biases
    retrieval toward documents that happen to contain the phrase.

    The ticker stays first in the string: it is the probe's only stable
    handle on which ticker a query belongs to.
    """
    subject = f"{ticker} {company_name}" if company_name else ticker
    return f"{subject} earnings OR acquisition OR product launch OR partnership"


def _result_urls(results: list[TavilySearchResult]) -> list[str]:
    """Distinct, order-preserving URLs from one probe response.

    `len(results)` counted duplicates: the same wire story syndicated to two
    of `TRUSTED_DOMAINS` scored a ticker twice for one event.
    """
    seen: dict[str, None] = {}
    for r in results:
        url = r.get("url") if isinstance(r, dict) else None
        if url:
            seen.setdefault(url, None)
    return list(seen)


def _domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _probe_search(
    searcher: TavilySearcher,
    query: str,
    *,
    start_date: str | None,
    end_date: str | None,
    max_retries: int,
    backoff_seconds: float,
    sleep_fn: Callable[[float], None],
) -> list[str] | None:
    """One ticker's retried search-only probe, returning its distinct result
    URLs or `None` once every attempt has failed (this ticker's degraded
    case).

    Returns URLs rather than a bare count so the caller can rank on
    properties of the retrieved set (freshness against `covered_events`,
    breadth across domains) instead of on a single integer that saturates at
    the provider's `max_results`. Nothing here reads a result's *content* --
    the probe stays search-only and LLM-free (ADR 0006 §3.1).

    Mirrors `pipeline.research.agent.run_research_branch`'s exponential
    backoff loop (`backoff_seconds * 2**attempt`) so a single flaky
    ticker doesn't burn a different retry policy than the rest of the
    pipeline, but scoped to a single search call -- there's no LLM step or
    refusal-detection concern here to isolate around.
    """
    last_error: Exception | None = None
    attempt = 1
    for attempt in range(1, max_retries + 2):
        try:
            return _result_urls(
                searcher.search(query, start_date=start_date, end_date=end_date)
            )
        except Exception as exc:  # noqa: BLE001 - isolate this ticker's probe
            last_error = exc
            logger.warning(
                "highlight pre-selection: search failed (query=%r attempt=%d): %s",
                query,
                attempt,
                exc,
            )
            if attempt <= max_retries:
                sleep_fn(backoff_seconds * (2 ** (attempt - 1)))
    logger.warning(
        "highlight pre-selection: giving up on query=%r after %d attempt(s): %s",
        query,
        attempt,
        last_error,
    )
    return None


def _rank_key(urls: list[str], covered_urls: set[str]) -> tuple[int, int, int]:
    """This ticker's ranking signal, highest-first, as a lexicographic tuple.

    A tuple rather than a weighted scalar on purpose: there is no principled
    exchange rate between "one more article" and "one more outlet", and
    inventing weights would put two tunable magic numbers in the middle of a
    selection ADR 0006 §3 asks to stay explainable. Each component only
    breaks ties in the one before it.

      1. `fresh` -- distinct URLs not already cited by an event inside the
         covered-events trailing window. This is the component that makes the
         probe predict *extraction* yield rather than news volume: material
         the extraction step will recognise as already covered should not
         earn a ticker an expensive research branch.
      2. `domains` -- how many distinct outlets carry the fresh material. Six
         outlets independently covering a company is a stronger event signal
         than six pieces from one; it is also the component that keeps
         discriminating after `fresh` has saturated the provider's result cap.
      3. `total` -- the original raw distinct-URL count, as a last resort
         before falling back to watchlist file order.

    No score input of any kind participates (ADR 0006 §3): `covered_urls` is
    de-duplication state and `domains` is a property of the retrieved set.
    """
    fresh = [u for u in urls if u not in covered_urls]
    return len(fresh), len({_domain_of(u) for u in fresh}), len(urls)


def select_highlight_tickers(
    *,
    searcher: TavilySearcher | None = None,
    as_of: date | None = None,
    tickers: list[str] | None = None,
    company_names: dict[str, str] | None = None,
    covered_events: list[CoveredEvent] | None = None,
    limit: int = COMPLETENESS_MAX_HIGHLIGHTS,
    min_limit: int = COMPLETENESS_MIN_HIGHLIGHTS,
    max_retries: int = RESEARCH_MAX_RETRIES,
    backoff_seconds: float = RESEARCH_RETRY_BACKOFF_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> list[str]:
    """ADR 0006 §3: rank the watchlist by a cheap search-only probe and
    return the top `limit` (default `COMPLETENESS_MAX_HIGHLIGHTS`, i.e. 3-4
    in practice since a 43-ticker watchlist always clears `min_limit`). One
    query per ticker (`_build_query`), pinned to `as_of`'s rolling window via
    the same `pipeline.research.agent._search_window` helper the full research
    branch uses, restricted to `TRUSTED_DOMAINS`.

    The ranking signal is `_rank_key` -- fresh URLs, then distinct outlets,
    then raw URLs -- not `len(results)`. **Why it changed (2026-08-19):** run
    `20260819T112959-a883d9` selected MSFT, ADBE, CRM, NOW, which is exactly
    positions 1-4 of `data/watchlist.yaml`. That is not a ranking, it is a
    total tie resolved by the tie-break below, and the two branches it bought
    (ADBE, NOW) returned zero findings each. A count capped at the provider's
    `max_results` cannot discriminate between tickers that all reach the cap,
    and over a 7-day window across large caps most of them do.

    `covered_events` (optional) is the run's de-duplication state, used to
    discount results the extraction step will already recognise as covered.
    Omitted, the probe ranks on unfiltered freshness, which is exactly the
    old behaviour -- the discount is additive, never required. It is
    de-duplication state, not a score, so it stays inside ADR 0006 §3; see
    `pipeline.storage.covered_events_store.covered_source_urls`.

    No score input of any kind participates in ranking or tie-breaking --
    ADR 0006 §3 rejects a score-based fallback/tie-break explicitly.

    Tie-break (this module's own decision, undocumented by the ADR itself):
    tickers with equal rank keys keep the order they appear in
    `data/watchlist.yaml` (i.e. `tickers`'s input order, from
    `pipeline.config.load_watchlist_tickers()` when not overridden). That
    order is fixed by the YAML file, not by dict/set iteration, so the same
    week's Tavily data always yields the same selection on a rerun. When that
    tie-break is what actually decided the cut, it is now logged as a
    warning: an unranked selection and a ranked one used to be
    indistinguishable after the fact, which is why the failure above took a
    live run to notice.

    Degraded case: a ticker whose search fails after `max_retries` retries
    is not dropped from consideration or allowed to crash the run -- it's
    treated as zero results (same rank as "genuinely no news this week") and
    logged. A ticker with zero results (real or degraded) can still be
    selected if too few tickers returned anything -- selection always returns
    exactly `min(limit, len(tickers))` tickers, never short, so a provider
    outage degrades this run's *ranking quality*, never its *availability*.
    """
    tickers = list(tickers) if tickers is not None else load_watchlist_tickers()
    company_names = (
        company_names if company_names is not None else load_watchlist_company_names()
    )
    if searcher is None:
        # Same Exa fallback the research branches get (pipeline/run.py):
        # without it this probe is single-provider, and a Tavily outage
        # scores every ticker 0, which silently collapses the ranking to
        # watchlist file order -- a degenerate selection that looks
        # identical to "no ticker had news this week".
        searcher = CheapTavilySearcher()
        if get_exa_api_key():
            searcher = FallbackSearcher(searcher, LiveExaSearcher())
    start_date, end_date = _search_window(as_of)
    covered_urls = covered_source_urls(covered_events or [])

    keys: dict[str, tuple[int, int, int]] = {}
    failed: list[str] = []
    for ticker in tickers:
        query = _build_query(ticker, company_names.get(ticker))
        urls = _probe_search(
            searcher,
            query,
            start_date=start_date,
            end_date=end_date,
            max_retries=max_retries,
            backoff_seconds=backoff_seconds,
            sleep_fn=sleep_fn,
        )
        if urls is None:
            failed.append(ticker)
            urls = []
        keys[ticker] = _rank_key(urls, covered_urls)

    if failed:
        logger.warning(
            "highlight pre-selection: %d/%d ticker search(es) failed after retries "
            "and were scored 0 (still eligible for selection): %s",
            len(failed),
            len(tickers),
            failed,
        )

    order_index = {ticker: i for i, ticker in enumerate(tickers)}
    ranked = sorted(tickers, key=lambda t: (tuple(-c for c in keys[t]), order_index[t]))
    n = min(limit, len(ranked))
    selected = ranked[:n]

    # The probe left no trace of what it ranked on, so a selection that had
    # collapsed to watchlist file order looked identical to one the data
    # actually produced -- and did, for a full live run, until the selected
    # tickers turned out to be positions 1-4 of the YAML. Both lines below
    # exist to make that self-evident from the log next time.
    logger.info(
        "highlight pre-selection: ranked %d ticker(s) by (fresh, domains, total); "
        "selected=%s covered_urls=%d ranking=%s",
        len(tickers),
        selected,
        len(covered_urls),
        [(t, keys[t]) for t in ranked[: max(n * 2, n + 2)]],
    )
    if tickers and all(keys[t] == keys[tickers[0]] for t in tickers) and len(tickers) > n:
        logger.warning(
            "highlight pre-selection: every ticker produced an identical rank key "
            "%s, so the selection is watchlist file order, not a ranking -- the "
            "probe discriminated nothing this run (selected=%s)",
            keys[tickers[0]],
            selected,
        )
    elif n < len(ranked) and keys[ranked[n - 1]] == keys[ranked[n]]:
        logger.warning(
            "highlight pre-selection: the cut between rank %d and %d was a tie "
            "(%s), so watchlist file order decided which of them got a research "
            "branch (selected=%s)",
            n,
            n + 1,
            keys[ranked[n - 1]],
            selected,
        )
    return selected
