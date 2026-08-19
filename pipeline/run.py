"""CLI entrypoint: `python -m pipeline.run [--tickers T1 T2 ...] [--label LABEL]`.

Wires together the pieces `build_graph()`/`graph.invoke()` alone don't
provide for an actual run: `load_watchlist_tickers()`, `default_run_config()`,
the `covered_events` store, and `observability.run_with_summary()`. Fails
fast on a missing API key instead of surfacing an opaque error deep inside a
research branch.

The unit of work is a **run**, not a week (ADR 0010 §2). `--week` is gone;
`--label` optionally names the run, and omitted, a timestamped id is minted.
Two runs on the same afternoon are normal for a tool with a button.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from pipeline.checkpointer import build_checkpointer, checkpoint_config
from pipeline.config import (
    ConfigError,
    JUDGE_MODEL,
    MACRO_TOPICS,
    WRITER_MODEL,
    YIELD_FLOOR_TRAILING_RUNS,
    get_exa_api_key,
    get_openai_api_key,
    get_sec_edgar_user_agent,
    get_tavily_api_key,
    load_scoring_eligible_tickers,
    load_watchlist_tickers,
)
from pipeline.data.edgar_submissions import EdgarSubmissionsSearcher
from pipeline.data.fred_client import LiveFredClient
from pipeline.data.scores_repository import load_scores_and_financials
from pipeline.macro_spine import build_macro_spine
from pipeline.graph import build_graph, default_run_config
from pipeline.observability import RunSummary, run_with_summary
from pipeline.research.edgar_search import EdgarFullTextSearcher
from pipeline.research.exa_client import FallbackSearcher, LiveExaSearcher
from pipeline.research.federal_register import FederalRegisterSearcher
from pipeline.research.highlight_selection import select_highlight_tickers
from pipeline.research.ir_feeds import IRFeedSearcher
from pipeline.research.tavily_client import LiveTavilySearcher, TavilySearcher
from pipeline.storage.covered_events_db_store import load_covered_events_db, save_covered_events_db
from pipeline.storage.covered_events_store import (
    load_covered_events,
    save_covered_events,
    update_covered_events,
)
from pipeline.storage.report_store import (
    fail_run,
    save_draft_report,
    save_run_summary,
    start_run,
    trailing_findings_counts,
)
from pipeline.synthesis.render import (
    apply_degraded_publish_banner,
    fence_bare_score_blocks,
    resolve_placeholders_with_stats,
)

logger = logging.getLogger(__name__)

# Verdicts that cause a report to be persisted -- every verdict the verifier
# can reach, `block` included.
#
# `block` was excluded here until Faz 5.3. ADR 0010 §6 reverses that: the sole
# reader is the person who pressed the button and can see the verdict, so
# showing the draft *plus its violations* is strictly more information than
# showing nothing. What is emphatically NOT reversed is the verifier itself --
# it still runs every check, still classifies by severity, and still returns
# `block`; the change is only what happens to the draft afterwards.
#
# Two things stay true of a blocked run and are load-bearing for the retry
# path: its `covered_events` are still not marked covered (see below), and the
# report row it writes carries `verifier_verdict="block"` plus the full
# violation list, which is what the reader-facing banner renders. A blocked
# report that reached the store *without* those two columns populated would be
# indistinguishable from a clean one, which is the exact failure ADR 0010 §6's
# Consequences section names.
REPORTED_VERDICTS = ("pass", "pass_with_flags", "degraded_publish", "block")

# NOTE (Faz 2.5, closed in Faz 3.3): there is deliberately no re-run guard here.
# `_guard_rerun`/`WeekAlreadyReportedError`/`--force` refused a second run for
# an already-reported *week*, and that premise died with week identity (ADR
# 0010 §2): two runs on the same afternoon are the normal case for a tool with
# a button, and each now gets its own run id, its own report row and its own
# slug, so neither overwrites the other. What genuinely still needs guarding
# -- a second run starting while one is *in flight* (ADR 0010 §4) -- now
# lives one layer up, in `techinves.runs.service`, as a unique partial index
# on `runs(trigger_type)`. It is deliberately NOT here: this function is the
# work, not the gate, and a guard inside it could only ever refuse after the
# row it would have to inspect already existed. Calling `run_pipeline()`
# directly (the CLI, a test) therefore takes no lock -- which is correct, and
# is why a CLI run is not gated on a UI run's lock. What the CLI *does* hit
# is the index itself, via `start_run()`, which turns the violation into a
# readable refusal rather than a raw IntegrityError.


def make_run_id(label: str | None = None) -> str:
    """This run's identity (ADR 0010 §2).

    An explicit `label` *is* the run id, so a deliberate retry under the same
    label upserts the run row and its report rather than accumulating
    near-duplicates. Omitted, a sortable timestamp plus a short random suffix
    keeps two runs started in the same second apart.
    """
    if label:
        return label
    return f"{datetime.now():%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"


def run_pipeline(
    *,
    tickers: list[str],
    run_id: str | None = None,
    trigger_type: str = "report",
    ticker: str | None = None,
    as_of: date | None = None,
    covered_events_path: Path | None = None,
    llm: BaseChatModel | None = None,
    judge_llm: BaseChatModel | None = None,
    searcher: TavilySearcher | None = None,
    edgar_searcher: TavilySearcher | None = None,
    ir_feed_searcher: TavilySearcher | None = None,
    edgar_submissions_searcher: TavilySearcher | None = None,
    regulation_searcher: TavilySearcher | None = None,
    highlight_searcher: TavilySearcher | None = None,
    highlight_tickers: list[str] | None = None,
    graph: Any | None = None,
    scores: dict[str, dict] | None = None,
    financials: dict[str, dict] | None = None,
    macro_spine: list | None = None,
) -> RunSummary:
    """Run one pipeline pass end to end: load covered_events, invoke the
    graph, merge this run's findings back into covered_events, save.

    `run_id` is this unit of work's identity (ADR 0010 §2) and keys the run
    row, the report row and its slug, and the covered-events window. Omitted,
    one is minted by `make_run_id()`. `as_of` is *only* the date the research
    window ends on and the date the report is headed with -- it keys nothing,
    and defaults to today.

    `graph` (and, for building one, `llm`/`searcher`) are injectable so
    tests can pass fakes without touching real API keys or the network.
    `scores`/`financials` are injectable the same way; when omitted they're
    loaded from the techinves DB (each company's current score, whichever
    `scores` run produced it) instead of `pipeline/fixtures/mock_data.py`.

    `covered_events_path` selects the store: given explicitly (e.g. by
    tests, or for local runs without a DB), the JSON-file store is used;
    left as `None` (the production default -- GitHub Actions checks out a
    clean tree every run, so a file-based store would silently reset) uses
    the `covered_events` DB table instead.

    `tickers` is this run's scoring/reporting universe (the CLI's
    `--tickers` override, or the full watchlist by default) -- it still
    drives scores/financials loading, the Full Watchlist table, and
    `is_partial`. `highlight_tickers` (R9, ADR 0006 §3) is the much smaller
    3-4-ticker subset that actually gets the expensive (search + LLM
    extraction) company research branch and a synthesis deep-dive; when not
    injected, it's computed here via `select_highlight_tickers()` --
    *before* `build_graph()`/`graph.invoke()` -- from `tickers`, so the
    selection reflects whatever universe this run was actually asked to
    cover. `highlight_searcher` is injectable the same way `searcher` is,
    so tests can supply a fake instead of a live, API-key-requiring Tavily
    call; omitted, `select_highlight_tickers()` builds its own cheap
    search-only searcher.
    """
    run_id = run_id or make_run_id()
    as_of = as_of or date.today()

    # The run row is the parent of everything this function writes
    # (score reads, the report row, covered_events), so it is created before
    # any work happens rather than with the summary at the end.
    #
    # `trigger_type`/`ticker` are passed through rather than hard-coded:
    # ADR 0010 §1's single-company action is this same function narrowed to
    # one ticker (`trigger_type="company"`), and hard-coding "report" here
    # would relabel every company run as a full-watchlist one on the row the
    # UI reads.
    if covered_events_path is None:
        start_run(run_id, trigger_type=trigger_type, ticker=ticker)

    try:
        # Loaded before highlight selection (not at graph-input time, where it
        # used to sit): the probe discounts URLs the extraction step will
        # recognise as already covered (highlight_selection.py, 2026-08-19),
        # so it needs the same de-dup state the graph gets.
        if covered_events_path is not None:
            covered_events = load_covered_events(path=covered_events_path)
        else:
            covered_events = load_covered_events_db()

        if highlight_tickers is None:
            highlight_tickers = select_highlight_tickers(
                searcher=highlight_searcher,
                as_of=as_of,
                tickers=tickers,
                covered_events=covered_events,
            )

        if graph is None:
            llm = llm or ChatOpenAI(model=WRITER_MODEL, temperature=0)
            judge_llm = judge_llm or ChatOpenAI(model=JUDGE_MODEL, temperature=0)
            if searcher is None:
                # R15: Exa fallback is opt-in -- EXA_API_KEY unset means
                # Tavily-only, matching pre-R15 behaviour exactly.
                searcher = LiveTavilySearcher()
                if get_exa_api_key():
                    searcher = FallbackSearcher(searcher, LiveExaSearcher())
            if edgar_searcher is None:
                # R16: opt-in the same way -- no configured contact User-Agent
                # means no EDGAR leg, matching pre-R16 behaviour exactly.
                user_agent = get_sec_edgar_user_agent()
                if user_agent:
                    edgar_searcher = EdgarFullTextSearcher(user_agent=user_agent)
            if edgar_submissions_searcher is None:
                # Faz 7b item 3: same opt-in knob as R16 above (both legs hit
                # *.sec.gov and must carry SEC's required contact User-Agent) --
                # reuses techinves.data.edgar_client.EdgarClient rather than a
                # second hand-rolled throttle/retry/cache implementation.
                user_agent = get_sec_edgar_user_agent()
                if user_agent:
                    from techinves.data.edgar_client import EdgarClient

                    edgar_submissions_searcher = EdgarSubmissionsSearcher(
                        client=EdgarClient(user_agent=user_agent)
                    )
            if ir_feed_searcher is None:
                # Faz 7b item 2: keyless and off-domain from SEC, so always on --
                # unmapped tickers (see ir_feeds.TICKER_IR_FEEDS) simply return
                # zero extra results, same degrade-to-empty contract as the
                # other legs.
                ir_feed_searcher = IRFeedSearcher()
            if regulation_searcher is None:
                # Faz 7b item 4: keyless and public-domain, always on -- gated
                # per-branch by run_research_branch's is_regulation_topic()
                # check, not here.
                regulation_searcher = FederalRegisterSearcher()
            # R23: a mid-run crash resumes from its last completed step instead
            # of re-buying every research branch. Only for the graph this
            # function builds itself -- an injected `graph` (tests) compiles
            # its own checkpointer, or deliberately none.
            graph = build_graph(
                searcher=searcher,
                llm=llm,
                judge_llm=judge_llm,
                edgar_searcher=edgar_searcher,
                ir_feed_searcher=ir_feed_searcher,
                edgar_submissions_searcher=edgar_submissions_searcher,
                regulation_searcher=regulation_searcher,
                checkpointer=build_checkpointer(),
            )

        if scores is None or financials is None:
            db_scores, db_financials = load_scores_and_financials(tickers)
            scores = db_scores if scores is None else scores
            financials = db_financials if financials is None else financials

        if macro_spine is None:
            # R28: opt-in via FRED_API_KEY, same pattern as R15/R16 -- unset
            # means no spine table, matching pre-R28 behaviour exactly.
            fred_api_key = os.environ.get("FRED_API_KEY")
            macro_spine = build_macro_spine(LiveFredClient(fred_api_key)) if fred_api_key else []

        # R3: trailing history only exists in DB mode -- file-store/test mode
        # (`covered_events_path is not None`) has no persisted runs to compare
        # against, so the floor check is simply skipped there (run_with_summary
        # treats an empty list as "not enough history").
        trailing_counts = (
            trailing_findings_counts(
                limit=YIELD_FLOOR_TRAILING_RUNS,
                # This run's own earlier attempt is not a baseline for it.
                exclude_run_id=run_id,
            )
            if covered_events_path is None
            else []
        )

        # R23: thread_id ties this invoke to run_id's checkpoint lineage --
        # harmless on a checkpointer-less graph (e.g. every test's injected
        # fake), meaningful on the real one build_graph() compiles above.
        invoke_config = {**default_run_config(), "configurable": checkpoint_config(run_id)}

        initial_state = {
            "run_id": run_id,
            "as_of": as_of,
            "highlight_tickers": highlight_tickers,
            "macro_topics": MACRO_TOPICS,
            "covered_events": covered_events,
            "scores": scores,
            "financials": financials,
            "macro_spine": macro_spine,
            # R31: the verifier's completeness check (REPORT_SPEC.md §10 item 1)
            # must measure against the real watchlist, never this run's own
            # `tickers`/`highlight_tickers` -- see pipeline/verifier/node.py.
            "scoring_eligible_tickers": load_scoring_eligible_tickers(),
        }

        # R23 fix: LangGraph resumes a thread only when invoked with `None`
        # (see tests/pipeline/test_checkpointer.py) -- invoking with the full
        # initial_state again, as before, always restarted from START *and*
        # re-fed the reducer-accumulated channels (research_findings,
        # branch_yields, ...) back in, duplicating them on top of what the
        # crashed attempt had already checkpointed. Only resume (invoke None)
        # when this thread has pending, uncompleted steps from a prior crash;
        # a brand-new thread_id (the normal case) or one that already ran to
        # completion still gets a normal full-state invoke.
        invoke_config, state_input = _resolve_thread(graph, run_id, invoke_config, initial_state)

        result, summary = run_with_summary(
            graph,
            state_input,
            config=invoke_config,
            trailing_findings_counts=trailing_counts,
        )

        # A blocked run produced no usable report, so its events were never
        # actually covered: persisting them would mark this run's findings as
        # already-processed and permanently exclude it from future runs. Leave
        # covered_events untouched so the next run re-researches the same window.
        blocked = summary.verdict == "block"
        if blocked:
            logger.warning(
                "verifier verdict=block; covered_events left unchanged so a future "
                "run retries this run's events (run_id=%s)",
                run_id,
            )
        else:
            updated_events = update_covered_events(
                covered_events, result.get("research_findings", []), run_id=run_id
            )
            if covered_events_path is not None:
                save_covered_events(updated_events, path=covered_events_path)
            else:
                save_covered_events_db(updated_events)

        # Persist the report for every verdict the verifier can reach, `block`
        # included (ADR 0010 §6, Faz 5.3 -- see REPORTED_VERDICTS above). Skipped
        # in file-store/test mode (`covered_events_path is not None`) the same way
        # DB-backed covered_events is -- tests inject a fake graph/scores and
        # shouldn't need a real DB to pass.
        if covered_events_path is None and summary.verdict in REPORTED_VERDICTS:
            draft_report = result.get("draft_report")
            verifier_report = result.get("verifier_report")
            if draft_report:
                resolved, resolution_stats = resolve_placeholders_with_stats(
                    draft_report, scores, financials
                )
                if summary.verdict == "degraded_publish":
                    gap_messages = (
                        [v.message for v in verifier_report.violations if v.severity == "structural_hard"]
                        if verifier_report
                        else []
                    )
                    resolved = apply_degraded_publish_banner(resolved, gap_messages)
                    logger.warning(
                        "verifier verdict=degraded_publish; publishing with reduced-coverage "
                        "banner (run_id=%s, gaps=%s)",
                        run_id,
                        gap_messages,
                    )
                # R5: a degenerate "data unavailable for this run" report can pass
                # the verifier (it's well-formed prose) -- this is the one place
                # left that can catch it, since resolve_placeholders always
                # substitutes every well-formed placeholder by construction.
                if resolution_stats.below_threshold:
                    logger.warning(
                        "placeholder resolution rate %.0f%% below threshold "
                        "(resolved=%d unavailable=%d) for run_id=%s",
                        resolution_stats.resolution_rate * 100,
                        resolution_stats.resolved,
                        resolution_stats.unavailable,
                        run_id,
                    )
                if resolution_stats.unknown_field:
                    logger.warning(
                        "placeholder field names never valid for any ticker (likely "
                        "typo, not missing data): %s",
                        resolution_stats.unknown_field,
                    )
                # REPORT_SPEC.md §5.1/§9: the deterministic score block must be
                # fenced. The prompt (pipeline/synthesis/prompts.py) asks the
                # writer LLM to fence it, but that's compliance, not a guarantee
                # -- production reports have shipped with it unfenced, which
                # collapses to one unreadable line once ReactMarkdown renders it
                # on the site. Run this last, right before persistence, so it
                # normalizes the exact text getting saved regardless of which
                # branches above ran (placeholder resolution never touches the
                # static COMPOSITE SCORE/DATA COVERAGE marker lines this scans
                # for, and the degraded-publish banner is inserted at the top of
                # the document, never inside a score block, so ordering against
                # either of those doesn't change the result -- this is simply
                # the safest place to guarantee it never gets skipped by a
                # later `continue`/early-return added above it in the future).
                # Deliberately not enforced as a verifier check: the verifier
                # runs on the pre-normalization draft (see
                # pipeline/verifier/rules.py's module docstring), so a fence
                # requirement there would hard-block every run. The invariant is
                # guaranteed by construction here instead.
                resolved = fence_bare_score_blocks(resolved)
                is_partial = set(tickers) != set(load_watchlist_tickers())
                section_scores = (
                    [s.model_dump(mode="json") for s in verifier_report.section_scores]
                    if verifier_report
                    else None
                )
                # Faz 5.3: the classified violations are what ADR 0010 §6's banner
                # *names*. `None` (no verifier report at all) is kept distinct
                # from `[]` (the verifier ran and found nothing) all the way to
                # the UI, because the banner treats an unknown verifier state as
                # something to warn about rather than as a clean pass.
                verifier_violations = (
                    [v.model_dump(mode="json") for v in verifier_report.violations]
                    if verifier_report
                    else None
                )
                slug = save_draft_report(
                    run_id=run_id,
                    # ADR 0010 §2: the unit of work is a run, not a week. The
                    # title said "Weekly" long after the schedule that made it
                    # one was deleted -- and two runs on the same afternoon are
                    # normal now, so it was also simply false.
                    title=f"TechInves Sector Report — {as_of.isoformat()}",
                    body_markdown=resolved,
                    # R10 / ADR 0006 §3.4: reuse the same pre-fan-out selection
                    # for both site badges (highlight_tickers) and deep-dive
                    # section typing (researched_tickers) -- they're the same
                    # list by construction now, not reconciled after the fact.
                    # `pick_highlight_tickers()` (the old event-count +
                    # composite-score tie-break) is retired.
                    highlight_tickers=highlight_tickers,
                    researched_tickers=highlight_tickers,
                    verifier_verdict=summary.verdict,
                    is_partial=is_partial,
                    section_scores=section_scores,
                    verifier_violations=verifier_violations,
                )
                if blocked:
                    logger.warning(
                        "verifier verdict=block; report saved and rendered WITH its "
                        "violations rather than withheld (ADR 0010 §6): slug=%s "
                        "violations=%d",
                        slug,
                        len(verifier_violations or []),
                    )
                else:
                    logger.info("draft report saved: slug=%s verdict=%s", slug, summary.verdict)
        if covered_events_path is None and blocked:
            # Still dumped to disk. The store now holds the *normalized* draft
            # (placeholders resolved, score blocks fenced); this artifact is the
            # writer's raw output, which is what a "why did the verifier see a
            # number leak here" investigation actually needs to read.
            _dump_blocked_draft(result.get("draft_report"), run_id=run_id)

        # R1: persist the run row regardless of verdict (including block) --
        # unlike covered_events/draft-report persistence, a blocked run's
        # measurement data is exactly what's needed to diagnose *why* it blocked.
        #
        # **Written here, at the true end of the work**, not right after
        # `run_with_summary` returns. `save_run_summary` lands
        # `status="succeeded"` (pipeline/storage/report_store.py's
        # `_save_run_summary_async`), which is the terminal status that
        # *releases* ADR 0010 §4's in-flight lock. Called at the top of this
        # persistence tail, it declared the run finished while ~130 lines of
        # real work were still ahead of it -- covered-events persistence,
        # placeholder resolution, score-block fencing, the report write -- so a
        # second trigger was accepted mid-work and could interleave with those
        # writes on the same tables. The lock now spans the whole unit of work,
        # which is what §4 says it is for.
        #
        # `run_id=run_id` is the explicit coupling Faz 2 carried into Faz 3.
        # `save_run_summary` used to upsert on `summary.run_id` -- the id the
        # graph *state echoed back* -- which is equal to this function's `run_id`
        # only by construction. Now that the background executor creates the row,
        # holds the in-flight lock through it and lands its terminal status, the
        # executor's id is authoritative: a graph that returned a different id
        # would otherwise write the summary onto some other row and leave the
        # real one to be reconciled as abandoned.
        if covered_events_path is None:
            save_run_summary(summary, run_id=run_id)

        return summary
    except BaseException as exc:
        # ADR 0010 §4: `start_run()` above took the in-flight lock via
        # `uq_runs_active_trigger`, a partial unique index over
        # *non-terminal* run rows -- so a run that raises without landing a
        # terminal status holds that lock indefinitely. The background
        # executor (`techinves.runs`) already guards its own path; the CLI
        # did not, so a crashed CLI run locked out the UI's "Generate
        # report" button until the API was restarted (only
        # `techinves.runs.reconcile`, which runs at API *startup*, cleared
        # it). `BaseException` rather than `Exception` deliberately: a
        # KeyboardInterrupt on a long CLI run is the single most likely way
        # a run dies, and it leaks the lock exactly the same way.
        if covered_events_path is None:
            try:
                fail_run(run_id, error=f"{type(exc).__name__}: {exc}")
            except Exception:  # noqa: BLE001 - never mask the original failure
                logger.exception("could not mark run_id=%s failed", run_id)
        raise


MAX_CHECKPOINT_ATTEMPTS = 50


def _resolve_thread(graph, run_id: str, invoke_config: dict, initial_state: dict):
    """Pick the checkpoint thread this invoke should use, and whether to
    resume it (`None`) or start it fresh (`initial_state`).

    Three cases, per thread, in order:

    * **No checkpoint yet** -- brand-new thread. Full invoke.
    * **Pending steps** (`snapshot.next` non-empty) -- a prior attempt
      crashed mid-run. Resume with `None`, which is the only input LangGraph
      treats as "continue"; this is R23's original purpose and is unchanged.
    * **Completed** (checkpoint exists, nothing pending) -- a prior attempt
      ran to a *terminal verdict*. This is the case R23 got wrong: `block`
      is a normal terminal outcome, not a crash, so re-running the same
      week landed a full invoke back onto a finished thread, where the
      additive reducers (`research_findings`, `branch_yields`, ... -- see
      pipeline/schemas.py) appended this attempt's output on top of the
      previous one's. Observed on 2026-08-17: findings 33 -> 81 -> 162
      across three attempts, the third contributing nothing of its own, and
      the inflated set manufacturing a "5 deep-dive sections" completeness
      violation that no single run's research would have produced. Retrying
      a blocked week is the first thing anyone does, so this silently
      corrupted exactly the recovery path.

    A completed thread is therefore never reused: the attempt moves to a
    fresh `run_id#retryN` lineage, which keeps the old checkpoint intact for
    inspection while guaranteeing the new attempt starts from clean state.
    """
    if not hasattr(graph, "get_state"):
        return invoke_config, initial_state

    for attempt in range(MAX_CHECKPOINT_ATTEMPTS):
        thread_id = run_id if attempt == 0 else f"{run_id}#retry{attempt}"
        config = {**invoke_config, "configurable": checkpoint_config(thread_id)}
        try:
            snapshot = graph.get_state(config)
        except Exception as exc:  # noqa: BLE001 - checkpointer-less or unreachable
            # Two very different situations raise here and the exception
            # cannot tell them apart: a graph compiled without a checkpointer
            # (fine -- there is no thread to be dirty) and a checkpointer that
            # is momentarily unreachable (a sqlite lock, a Postgres blip, a
            # `PostgresSaver` connection reset -- note the connection is
            # deliberately never closed, pipeline/checkpointer.py). Returning
            # the *original* config, as this used to, resolved the ambiguity in
            # favour of the dangerous reading: if thread_id=run_id happened to
            # hold a completed checkpoint, a full invoke landed straight back
            # onto it. Degrade to running with no checkpoint thread instead --
            # the same fail-safe the lineage-exhaustion path below takes. The
            # cost is losing resume for this attempt; the alternative is
            # silently accumulating another run's findings onto this one.
            logger.warning(
                "checkpointer read failed for thread_id=%s (%s); running without a "
                "checkpoint thread rather than risking a reused one",
                thread_id,
                exc,
            )
            return {k: v for k, v in invoke_config.items() if k != "configurable"}, initial_state

        if snapshot is None or not getattr(snapshot, "values", None):
            if attempt:
                logger.warning(
                    "run_id=%s already has a completed checkpoint; starting a clean "
                    "lineage on thread_id=%s so this attempt's findings are its own",
                    run_id,
                    thread_id,
                )
            return config, initial_state

        if snapshot.next:
            logger.warning(
                "resuming checkpointed thread_id=%s from its last completed step "
                "(pending steps: %s)",
                thread_id,
                snapshot.next,
            )
            return config, None

    logger.warning(
        "run_id=%s exhausted %d checkpoint lineages; running without a checkpoint "
        "thread rather than accumulating onto a completed one",
        run_id,
        MAX_CHECKPOINT_ATTEMPTS,
    )
    return {k: v for k, v in invoke_config.items() if k != "configurable"}, initial_state


BLOCKED_DRAFT_DIR = Path("data") / "blocked_drafts"


def _dump_blocked_draft(draft_report: str | None, *, run_id: str) -> None:
    """Write a blocked run's *raw* draft to disk so the block is diagnosable.

    Since Faz 5.3 a blocked draft IS persisted to the `reports` table and
    rendered with its violations (ADR 0010 §6), so this is no longer the only
    way to see what the writer produced. It is kept because the two artifacts
    are not the same text: the stored report has been through placeholder
    resolution and score-block fencing, while the verifier ran on the raw
    draft written here. When the question is "why did the verifier see a
    number leak in this sentence", the raw draft is the one that answers it.
    Debug output, not a report.
    """
    if not draft_report:
        return
    try:
        BLOCKED_DRAFT_DIR.mkdir(parents=True, exist_ok=True)
        path = BLOCKED_DRAFT_DIR / f"{run_id}-blocked.md"
        path.write_text(draft_report, encoding="utf-8")
        logger.warning("blocked draft written for inspection: %s", path)
    except OSError as exc:  # never let debug output fail the run
        logger.warning("could not write blocked draft: %s", exc)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the research/synthesis/verifier pipeline once."
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        help="Specific tickers to research (default: the full watchlist)",
    )
    parser.add_argument(
        "--label",
        default=None,
        help=(
            "Optional label for this run; becomes its run id, so re-running "
            "with the same label overwrites that run instead of adding "
            "another. Omitted, a timestamped id is generated."
        ),
    )
    parser.add_argument(
        "--covered-events-path",
        default=None,
        help="Override the covered_events.json path (default: data/covered_events.json)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # Parse first: `--help`/`--version` must short-circuit (argparse raises
    # SystemExit(0) from here) without tripping over API-key validation,
    # which only the paths that actually make API calls need.
    args = _parse_args(argv)

    # Faz 6 (ADR 0010 §7-8): the CLI refuses with the same missing-key
    # message `POST /v1/runs` would give a `report`/`company` trigger for
    # -- `techinves.runs.keys` is the one source of truth both read, so a
    # widened required-key set never needs updating in two places. FMP is
    # checked here even though `run_pipeline` doesn't call the FMP API
    # directly (scores/financials come from the DB, already populated by a
    # `scores` run) -- the check names the key the on-demand *trigger* would
    # have refused this run for, keeping the CLI and the HTTP endpoint's
    # refusal reasons in agreement rather than the CLI silently accepting
    # what the endpoint would have blocked.
    from techinves.runs.keys import missing_required_key

    missing = missing_required_key("report")
    if missing is not None:
        print(f"Config error: {missing} is not set", file=sys.stderr)
        return 1

    try:
        get_openai_api_key()
        get_tavily_api_key()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 1

    tickers = args.tickers or load_watchlist_tickers()
    covered_events_path = (
        Path(args.covered_events_path) if args.covered_events_path else None
    )

    # No re-run refusal here any more -- see the note above `make_run_id`.
    # Exit code 3 went with it; 0/1/2 keep their meanings.
    summary = run_pipeline(
        tickers=tickers,
        run_id=make_run_id(args.label),
        covered_events_path=covered_events_path,
    )

    print(summary.as_log_line())
    print(f"verdict: {summary.verdict}")
    return 0 if summary.verdict != "block" else 2


if __name__ == "__main__":
    raise SystemExit(main())
