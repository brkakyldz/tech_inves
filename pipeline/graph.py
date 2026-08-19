"""Assembles the research -> synthesis -> verifier LangGraph
(ARCHITECTURE_PROPOSAL.md §2.1/§2.2). Fan-out over company tickers
(`state.highlight_tickers`) and macro topics (`state.macro_topics`) uses the
`Send` API; fan-in is automatic via the `additive_with_reset`/
`union_with_reset` reducers declared on `ReportState` (pipeline/schemas.py) --
additive within a run, cleared by the `RESET` sentinel `init_node` emits, so
re-invoking a checkpointed thread starts from empty rather than accumulating.

`highlight_tickers` is graph input, not computed here -- the (d.5)
"which tickers moved the most" selection node is out of scope for this
package. In practice it's fed either a handful of tickers or the entire
watchlist (`pipeline.config.load_watchlist_tickers()`); either way, use
`default_run_config()` as the invoke/stream `config` so branch concurrency
stays bounded regardless of list size.

Out of scope here (see pipeline/__init__.py): the (d.5) highlight_tickers
selection node, the scoring/financial-data nodes, render/approval/send/
covered_events-update nodes. `highlight_tickers`/`macro_topics`/`scores`/
`financials` are supplied as graph input.
"""

from __future__ import annotations

import logging
import time
from functools import partial
from typing import Any

from langchain_core.callbacks.usage import UsageMetadataCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from pipeline.config import RESEARCH_CONCURRENCY, estimate_cost_usd, load_watchlist_company_names
from pipeline.research.agent import run_research_branch
from pipeline.research.tavily_client import TavilySearcher
from pipeline.schemas import RESET, BranchYield, ReportState, ResearchBranchInput
from pipeline.synthesis.node import synthesis_node
from pipeline.verifier.node import verifier_node

logger = logging.getLogger(__name__)


def init_node(state: ReportState) -> dict:
    """Clears the four fan-in accumulators, so a run starts from an empty
    set of findings whatever the thread it lands on already holds.

    It used to re-emit each channel's *current* value, which the additive
    reducers then appended to itself -- a no-op on a fresh thread, a silent
    doubling on a checkpointed one that already ran. `RESET` (see
    `pipeline/schemas.py`'s `additive_with_reset`) makes the clear explicit
    and, because `init` runs only on a full invoke from START and never on a
    `None` resume, it never discards a crashed attempt's partial work.
    """
    return {
        "research_findings": RESET,
        "retrieved_urls": RESET,
        "failures": RESET,
        "branch_yields": RESET,
    }


def _covered_events_context(state: ReportState, *, ticker: str | None, topic: str | None) -> str:
    events = state.get("covered_events", [])
    matches = [
        e for e in events if (e.company == ticker and ticker) or (e.topic == topic and topic)
    ]
    if not matches:
        return ""
    return "\n".join(f"- {e.event_title} (first covered {e.first_covered_run})" for e in matches)


def fan_out_research(state: ReportState) -> list[Send]:
    """Router: builds one Send per company ticker and one per macro topic,
    all targeting the shared `research_branch` node (§2.2b)."""

    as_of = state.get("as_of")
    sends: list[Send] = []
    for ticker in state.get("highlight_tickers", []):
        branch = ResearchBranchInput(
            scope="company",
            ticker=ticker,
            covered_events_context=_covered_events_context(state, ticker=ticker, topic=None),
            as_of=as_of,
        )
        sends.append(Send("research_branch", {"branch": branch}))
    for topic in state.get("macro_topics", []):
        branch = ResearchBranchInput(
            scope="macro",
            topic=topic,
            covered_events_context=_covered_events_context(state, ticker=None, topic=topic),
            as_of=as_of,
        )
        sends.append(Send("research_branch", {"branch": branch}))
    return sends


def research_branch_node(
    payload: dict,
    *,
    searcher: TavilySearcher,
    llm: BaseChatModel,
    edgar_searcher: TavilySearcher | None = None,
    ir_feed_searcher: TavilySearcher | None = None,
    edgar_submissions_searcher: TavilySearcher | None = None,
    regulation_searcher: TavilySearcher | None = None,
    company_names: dict[str, str] | None = None,
) -> dict:
    branch: ResearchBranchInput = payload["branch"]
    start = time.monotonic()

    # R1: a fresh handler per branch, bound onto this branch's own llm
    # runnable, so concurrent Send() branches don't share (and race on) one
    # accumulator -- with_config's callbacks propagate through
    # llm.with_structured_output(...).invoke(...) inside run_research_branch
    # without that function needing to know about token accounting at all.
    usage_handler = UsageMetadataCallbackHandler()
    # Fakes/test doubles for `llm` are plain objects, not real Runnables --
    # they won't have with_config. Falling back to the bare llm there just
    # means usage_handler stays empty (tokens=0), which is correct: no real
    # callback machinery ran.
    branch_llm = llm.with_config(callbacks=[usage_handler]) if hasattr(llm, "with_config") else llm

    findings, failures, urls = run_research_branch(
        branch,
        searcher=searcher,
        llm=branch_llm,
        edgar_searcher=edgar_searcher,
        ir_feed_searcher=ir_feed_searcher,
        edgar_submissions_searcher=edgar_submissions_searcher,
        regulation_searcher=regulation_searcher,
        company_names=company_names,
    )
    duration = time.monotonic() - start

    tokens = sum(u.get("total_tokens", 0) for u in usage_handler.usage_metadata.values())
    input_tokens = sum(u.get("input_tokens", 0) for u in usage_handler.usage_metadata.values())
    output_tokens = sum(u.get("output_tokens", 0) for u in usage_handler.usage_metadata.values())
    cost_usd = estimate_cost_usd(input_tokens=input_tokens, output_tokens=output_tokens)

    logger.info(
        "research branch done: scope=%s ticker=%s topic=%s duration=%.2fs "
        "findings=%d failed=%s tokens=%d cost_usd=%.4f",
        branch.scope,
        branch.ticker,
        branch.topic,
        duration,
        len(findings),
        bool(failures),
        tokens,
        cost_usd,
    )
    branch_yield = BranchYield(
        scope=branch.scope,
        ticker=branch.ticker,
        topic=branch.topic,
        findings_count=len(findings),
        degraded=bool(failures),
        tokens=tokens,
        cost_usd=cost_usd,
        duration_seconds=duration,
    )
    return {
        "research_findings": findings,
        "failures": failures,
        "retrieved_urls": urls,
        "branch_yields": [branch_yield],
    }


def _with_llm_usage(
    node_fn, state: ReportState, *, llm: BaseChatModel, llm_kwarg: str, tokens_key: str, cost_key: str
) -> dict:
    """Runs a single-invocation node (synthesis/verifier) with a usage
    handler bound onto its llm, the same way `research_branch_node` does for
    fan-out branches, and folds the resulting tokens/cost into the returned
    partial state update under `tokens_key`/`cost_key`.

    Without this, `pipeline/observability.py`'s `total_tokens`/`total_cost_usd`
    covered only research branches -- the synthesis call (typically the
    single largest generation in a run) and the verifier's LLM judge call
    were silently excluded from the persisted run cost."""
    usage_handler = UsageMetadataCallbackHandler()
    scoped_llm = llm.with_config(callbacks=[usage_handler]) if hasattr(llm, "with_config") else llm
    result = node_fn(state, **{llm_kwarg: scoped_llm})
    input_tokens = sum(u.get("input_tokens", 0) for u in usage_handler.usage_metadata.values())
    output_tokens = sum(u.get("output_tokens", 0) for u in usage_handler.usage_metadata.values())
    tokens = sum(u.get("total_tokens", 0) for u in usage_handler.usage_metadata.values())
    cost_usd = estimate_cost_usd(input_tokens=input_tokens, output_tokens=output_tokens)
    return {**result, tokens_key: tokens, cost_key: cost_usd}


def build_graph(
    *,
    searcher: TavilySearcher,
    llm: BaseChatModel,
    judge_llm: BaseChatModel | None = None,
    edgar_searcher: TavilySearcher | None = None,
    ir_feed_searcher: TavilySearcher | None = None,
    edgar_submissions_searcher: TavilySearcher | None = None,
    regulation_searcher: TavilySearcher | None = None,
    company_names: dict[str, str] | None = None,
    checkpointer: Any | None = None,
):
    """`llm` (the writer) drives research extraction and synthesis;
    `judge_llm` (R10) drives the verifier's LLM consistency layer alone.
    Defaults to `llm` when omitted, so a single-model caller (tests, or an
    operator who hasn't set PIPELINE_JUDGE_MODEL) is unaffected.
    `edgar_searcher` (R16), `ir_feed_searcher`, `edgar_submissions_searcher`
    and `regulation_searcher` (ADR 0010 §8 items 2-4, Faz 7b) are optional
    additive research legs -- see
    `pipeline.research.agent.run_research_branch`. `company_names` (R17,
    ticker -> name) is loaded once here rather than per-branch when omitted,
    to avoid re-reading WATCHLIST.md dozens of times per run. `checkpointer`
    (R23, a `BaseCheckpointSaver` -- see pipeline/checkpointer.py) is opt-in;
    omitted means every `.invoke()` runs stateless, exactly as before R23.
    """
    if company_names is None:
        company_names = load_watchlist_company_names()
    graph = StateGraph(ReportState)

    graph.add_node("init", init_node)
    graph.add_node(
        "research_branch",
        partial(
            research_branch_node,
            searcher=searcher,
            llm=llm,
            edgar_searcher=edgar_searcher,
            ir_feed_searcher=ir_feed_searcher,
            edgar_submissions_searcher=edgar_submissions_searcher,
            regulation_searcher=regulation_searcher,
            company_names=company_names,
        ),
    )
    graph.add_node(
        "synthesis",
        partial(
            _with_llm_usage,
            synthesis_node,
            llm=llm,
            llm_kwarg="llm",
            tokens_key="synthesis_tokens",
            cost_key="synthesis_cost_usd",
        ),
    )
    graph.add_node(
        "verifier",
        partial(
            _with_llm_usage,
            verifier_node,
            llm=judge_llm or llm,
            llm_kwarg="llm",
            tokens_key="verifier_tokens",
            cost_key="verifier_cost_usd",
        ),
    )

    graph.add_edge(START, "init")
    graph.add_conditional_edges("init", fan_out_research, ["research_branch"])
    graph.add_edge("research_branch", "synthesis")
    graph.add_edge("synthesis", "verifier")
    graph.add_edge("verifier", END)

    return graph.compile(checkpointer=checkpointer)


def default_run_config() -> dict:
    """Caps concurrent research branches at RESEARCH_CONCURRENCY
    (ARCHITECTURE_PROPOSAL.md §1.4 / §2.2b) -- matters once `highlight_tickers`
    is the whole watchlist (dozens of branches) instead of a handful. Pass as
    the `config` kwarg to graph.invoke()/.stream()."""
    return {"max_concurrency": RESEARCH_CONCURRENCY}
