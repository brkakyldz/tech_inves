"""Persists a run and its report into the `runs` / `reports` /
`report_sections` / `report_highlights` DB tables (Faz 5,
reports/research/REPORTS_AND_PIPELINE_INTEGRATION_PLAN.md §6 step 1).

There is no publication state: ADR 0010 §5 deleted the publish gate, so a
saved report is visible to the reader immediately. The verifier still runs and
its verdict is still stored on the row -- ADR 0010 §6 renders a blocked report
with its violations rather than withholding it.

Everything here is keyed on `run_id` (ADR 0010 §2). `week_of` is gone, and
with it the "does this week already have a report?" lookup that existed only
to feed `pipeline/run.py`'s week-premised re-run guard.
"""

from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from techinves.api._time import now_naive_utc
from techinves.db.models import (
    ReportHighlightRow,
    ReportRow,
    ReportSectionRow,
    RunRow,
)
from techinves.db.session import get_sessionmaker

logger = logging.getLogger(__name__)

EXCERPT_MAX_CHARS = 500

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.*)$", re.MULTILINE)
_TICKER_HEADING_RE = re.compile(r"^([A-Z]{1,6})\b")


def slug_for_run(run_id: str) -> str:
    """The report slug for a run (plan §9 Q4, decided).

    Run-derived and nothing else: `front-end/app/reports/[slug]` routes on
    whatever this returns, and nothing external links to these URLs (there
    are no subscribers to have bookmarked them), so the simplest run-keyed
    scheme wins. The `run-` prefix keeps the slug readable as a URL segment
    when a run id is a bare timestamp.
    """
    return f"run-{run_id}"


def excerpt_of(body_markdown: str, *, max_chars: int = EXCERPT_MAX_CHARS) -> str:
    """First non-heading, non-table paragraph, truncated. Drops markdown
    heading lines entirely (not just their `#` prefix) so a title-only first
    "paragraph" doesn't become the whole excerpt, and drops table rows
    (`|...|` and `|---|...`) so a leading score table -- the writer's own
    output convention, see REPORT_SPEC.md §5.1 -- doesn't become the
    excerpt either."""
    skip_lines = [
        line
        for line in body_markdown.strip().splitlines()
        if not line.lstrip().startswith("#") and not line.lstrip().startswith("|")
    ]
    text = "\n".join(skip_lines).strip()
    first_para = next((p.strip() for p in text.split("\n\n") if p.strip()), "")
    if len(first_para) <= max_chars:
        return first_para
    return first_para[:max_chars].rsplit(" ", 1)[0] + "…"


def split_into_sections(
    body_markdown: str, *, title: str, researched_tickers: list[str]
) -> list[dict]:
    """Splits the writer's markdown into typed sections (REPORT_SPEC.md §2):
    one `section_type="company"` row per deep-dive's `##`/`###` heading, one
    `section_type="macro"` row per other top-level heading (Full Watchlist,
    Sector & Macro, Coverage Notes, disclaimers, ...), and a leading
    `section_type="macro"`/topic="overview" row for any content before the
    first heading (title line, opening disclaimer).

    R8/R9/R10 (ADR 0006 §3): `researched_tickers` is this run's fan-out
    input (`state["highlight_tickers"]`) -- since the pre-fan-out selection
    in `pipeline.research.highlight_selection.select_highlight_tickers()`
    now decides *both* which tickers get the full research branch and which
    tickers get badged, `researched_tickers` and the `highlight_tickers`
    argument below are, by construction, the same 3-4-ticker list passed by
    `pipeline/run.py`. (Historically this was the full watchlist and a
    separately-computed top-3 that could disagree -- see
    `pipeline/verifier/rules.py`'s `find_deep_dive_sections`, which predates
    that split and already scanned against the full set for the same reason
    this function does.)

    Heading matching is best-effort: a heading is treated as a company
    section only if its first word is an exact, case-sensitive match for one
    of `researched_tickers` (e.g. "### NVDA -- NVIDIA Corp."). Anything else
    -- including a heading that happens to start with an unrelated
    all-caps word -- falls back to a macro/topic section, so a parsing miss
    degrades to over-inclusive macro content rather than mis-tagging a
    ticker.
    """
    highlight_set = set(researched_tickers)
    matches = list(_HEADING_RE.finditer(body_markdown))

    sections: list[dict] = []
    order_index = 0

    lead = body_markdown[: matches[0].start()].strip() if matches else body_markdown.strip()
    if lead:
        sections.append(
            {
                "section_type": "macro",
                "ticker": None,
                "topic": "overview",
                "title": title,
                "body_markdown": lead,
                "order_index": order_index,
            }
        )
        order_index += 1

    for i, m in enumerate(matches):
        heading_text = m.group(2).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body_markdown)
        chunk = body_markdown[start:end].strip()

        ticker_match = _TICKER_HEADING_RE.match(heading_text)
        ticker = ticker_match.group(1) if ticker_match else None
        if ticker in highlight_set:
            sections.append(
                {
                    "section_type": "company",
                    "ticker": ticker,
                    "topic": None,
                    "title": heading_text,
                    "body_markdown": chunk,
                    "order_index": order_index,
                }
            )
        else:
            sections.append(
                {
                    "section_type": "macro",
                    "ticker": None,
                    "topic": heading_text[:100],
                    "title": heading_text,
                    "body_markdown": chunk,
                    "order_index": order_index,
                }
            )
        order_index += 1

    if not sections:
        sections.append(
            {
                "section_type": "macro",
                "ticker": None,
                "topic": "overview",
                "title": title,
                "body_markdown": body_markdown,
                "order_index": 0,
            }
        )
    return sections


def _badgeable_highlights(highlight_tickers: list[str], sections: list[dict]) -> list[str]:
    """The highlight tickers that actually got a deep-dive section, in
    selection order.

    ADR 0006 §3.4 says badges and deep-dives "agree by construction"
    because both come from the same pre-fan-out list. That holds for the
    *input*, not the output: the writer is asked for 3-4 deep-dives from a
    4-ticker selection (REPORT_SPEC.md §10 accepts either count), so a
    selected ticker can legitimately end up with no section -- observed on
    the 2026-08-17 run, where SNOW was badged but only WDAY/META/MSFT were
    written up. A badge pointing at a section that isn't in the report is a
    broken promise to the reader, and nothing else catches it: the
    completeness check only counts sections, and never compares them to the
    badge list.

    Deliberately a no-op when *no* section is typed `company`: that means
    section typing broke outright, and emptying the badge list would turn a
    visible inconsistency into a silently empty badge row.
    """
    section_tickers = {s["ticker"] for s in sections if s["section_type"] == "company"}
    if not section_tickers:
        return list(highlight_tickers)

    kept = [t for t in highlight_tickers if t in section_tickers]
    dropped = [t for t in highlight_tickers if t not in section_tickers]
    if dropped:
        logger.warning(
            "highlight ticker(s) selected but never given a deep-dive section, "
            "so not badged: %s (badged: %s)",
            dropped,
            kept,
        )
    return kept


async def _start_run_async(
    run_id: str, *, trigger_type: str, ticker: str | None
) -> None:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        existing = (
            await session.execute(select(RunRow).where(RunRow.run_id == run_id))
        ).scalar_one_or_none()
        now = now_naive_utc()
        if existing is None:
            session.add(
                RunRow(
                    run_id=run_id,
                    trigger_type=trigger_type,
                    ticker=ticker,
                    status="running",
                    created_at=now,
                    started_at=now,
                )
            )
        else:
            # A retry under the same explicit label reuses the row, the same
            # way `save_run_summary` upserts on `run_id`.
            existing.trigger_type = trigger_type
            existing.ticker = ticker
            existing.status = "running"
            existing.started_at = now
            existing.finished_at = None
            existing.error = None
        try:
            await session.commit()
        except IntegrityError as exc:
            # `uq_runs_active_trigger` -- ADR 0010 §4's in-flight lock. Only
            # reachable on the *insert* branch, i.e. a fresh run id started
            # while another run of the same trigger type is in flight; the
            # background executor always takes the update branch, since it
            # created the row itself before calling in here. Translated so a
            # CLI user sees which run is holding the lock instead of a raw
            # constraint traceback.
            await session.rollback()
            holder = (
                await session.execute(
                    select(RunRow.run_id).where(
                        RunRow.trigger_type == trigger_type,
                        RunRow.status.in_(("queued", "running")),
                    )
                )
            ).scalars().first()
            if holder is None:
                raise
            raise RunLockHeld(
                f"a '{trigger_type}' run is already in flight (run_id={holder}); "
                "ADR 0010 §4 refuses a second one rather than queueing it"
            ) from exc


class RunLockHeld(RuntimeError):
    """Raised by `start_run()` when ADR 0010 §4's in-flight lock is held.

    The mirror of `techinves.runs.service.RunRefused` for callers that go
    straight to the pipeline (the CLI) rather than through the background
    executor. Same index, same rule, different entry point.
    """


def start_run(run_id: str, *, trigger_type: str = "report", ticker: str | None = None) -> None:
    """Create (or reopen) this run's row before any work happens.

    Two reasons it has to come first rather than being written with the
    summary at the end: `score_history`, `reports` and `covered_events` are
    keyed onto `runs.run_id`, so the parent row must exist before any of them
    is written; and ADR 0010 §3 wants a run to be observable *while* it runs,
    which starts with there being a row to observe.

    Under `techinves.runs`' background executor the row already exists (the
    executor created it, and holds the in-flight lock through it), so this
    reopens rather than inserts. Called directly from the CLI it inserts, and
    can therefore collide with the lock -- see `RunLockHeld`.
    """
    asyncio.run(_start_run_async(run_id, trigger_type=trigger_type, ticker=ticker))


async def _fail_run_async(run_id: str, *, error: str) -> None:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        existing = (
            await session.execute(select(RunRow).where(RunRow.run_id == run_id))
        ).scalar_one_or_none()
        if existing is None:
            # Nothing to release. `start_run()` is what creates the row and
            # takes the lock, so a missing row means the lock was never taken.
            return
        existing.status = "failed"
        existing.finished_at = now_naive_utc()
        existing.error = error[:MAX_RUN_ERROR_CHARS]
        await session.commit()


MAX_RUN_ERROR_CHARS = 2000


def fail_run(run_id: str, *, error: str) -> None:
    """Land `status="failed"` on a run row whose work raised.

    ADR 0010 §4's in-flight lock is `uq_runs_active_trigger`, a partial unique
    index over non-terminal rows -- so a row that never reaches a terminal
    status holds the lock forever. `techinves.runs`' background executor has
    its own try/except for this; the CLI (`pipeline/run.py`) did not, so a
    crashed CLI run locked out the "Generate report" button until someone
    restarted the API (only `techinves.runs.reconcile`, which runs at API
    *startup*, cleared it). Best-effort by design: this runs on an exception
    path, and a failure to record the failure must not replace the original
    traceback -- see `pipeline/run.py`'s caller.
    """
    asyncio.run(_fail_run_async(run_id, error=error))


async def _save_draft_async(
    *,
    run_id: str,
    title: str,
    body_markdown: str,
    highlight_tickers: list[str],
    researched_tickers: list[str] | None = None,
    verifier_verdict: str | None = None,
    is_partial: bool = False,
    section_scores: list[dict] | None = None,
    verifier_violations: list[dict] | None = None,
) -> str:
    slug = slug_for_run(run_id)
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        # Idempotent for a retry of the same run id: an existing report for
        # this slug is overwritten in place. There is no longer a published
        # state to protect -- ADR 0010 §5 removed the approval gate, so no
        # row is "already approved content" that a re-run must not replace.
        existing = (
            await session.execute(select(ReportRow).where(ReportRow.slug == slug))
        ).scalar_one_or_none()

        if existing is not None:
            report = existing
            report.title = title
            report.summary = excerpt_of(body_markdown)
            report.verifier_verdict = verifier_verdict
            report.is_partial = is_partial
            report.section_scores = section_scores
            report.verifier_violations = verifier_violations
            await session.execute(
                delete(ReportSectionRow).where(ReportSectionRow.report_id == report.id)
            )
            await session.execute(
                delete(ReportHighlightRow).where(ReportHighlightRow.report_id == report.id)
            )
        else:
            report = ReportRow(
                slug=slug,
                run_id=run_id,
                title=title,
                summary=excerpt_of(body_markdown),
                created_at=now_naive_utc(),
                verifier_verdict=verifier_verdict,
                is_partial=is_partial,
                section_scores=section_scores,
                verifier_violations=verifier_violations,
            )
            session.add(report)
        await session.flush()

        sections = split_into_sections(
            body_markdown,
            title=title,
            researched_tickers=researched_tickers if researched_tickers is not None else highlight_tickers,
        )
        for section in sections:
            session.add(ReportSectionRow(report_id=report.id, **section))

        for rank, ticker in enumerate(_badgeable_highlights(highlight_tickers, sections)):
            session.add(ReportHighlightRow(report_id=report.id, ticker=ticker, rank=rank))

        await session.commit()
    return slug


def save_draft_report(
    *,
    run_id: str,
    title: str,
    body_markdown: str,
    highlight_tickers: list[str],
    researched_tickers: list[str] | None = None,
    verifier_verdict: str | None = None,
    is_partial: bool = False,
    section_scores: list[dict] | None = None,
    verifier_violations: list[dict] | None = None,
) -> str:
    """Sync wrapper -- `pipeline/run.py`'s `run_pipeline()` is sync. Returns
    the new report's slug. `verifier_verdict`/`is_partial` are stored on the
    row and read by the reader-facing warning banner (ADR 0010 §6), which
    replaced the publish gate that used to consume them.
    `section_scores` (R2) is the verifier's LLM consistency layer output --
    previously computed every run and discarded once logged.
    `verifier_violations` (Faz 5.3) is the rule layer's *classified* output
    (`pipeline.schemas.VerifierViolation` dumps), which was in the same
    position: computed every run by `classify_violations`, logged, and lost.
    ADR 0010 §6 requires the banner to *name* the violations, and the verdict
    alone names nothing -- so this is what makes a blocked report readable as
    blocked-for-these-reasons rather than merely blocked. `None` means "no
    verifier report was available", which is a different thing from `[]`
    ("the verifier ran and found nothing"); the reader-facing banner keeps
    those apart.

    `highlight_tickers` and `researched_tickers` (R8/R9/R10, ADR 0006 §3.4)
    are expected to be the *same* list as of this revision: `pipeline/run.py`
    computes it once via
    `pipeline.research.highlight_selection.select_highlight_tickers()` and
    passes it through for both -- `researched_tickers` for section typing,
    `highlight_tickers` for `report_highlights` ranking -- so site badges and
    deep-dive sections agree by construction, with no reconciliation step.
    (The previous design computed these independently -- a full-watchlist
    fan-out for `researched_tickers` and `pick_highlight_tickers()`'s
    event-count-then-score tie-break for `highlight_tickers`, which could
    and did disagree; both are retired.) `researched_tickers` stays an
    optional, separately-passed argument rather than being derived from
    `highlight_tickers` internally so this function doesn't have to assume
    its caller always keeps them equal."""
    return asyncio.run(
        _save_draft_async(
            run_id=run_id,
            title=title,
            body_markdown=body_markdown,
            highlight_tickers=highlight_tickers,
            researched_tickers=researched_tickers,
            verifier_verdict=verifier_verdict,
            is_partial=is_partial,
            section_scores=section_scores,
            verifier_violations=verifier_violations,
        )
    )


async def _save_run_summary_async(summary, run_id: str | None = None) -> None:
    """R1: land this run's measurements and terminal status on its `runs`
    row. Upserts on `run_id` for the same reason `_save_draft_async` upserts
    on slug -- a retry under the same run id should overwrite, not duplicate.

    `start_run()` will normally have created the row already; the insert
    branch stays for callers (and tests) that persist a summary without one.

    **Which id it upserts on** (Faz 3, carried over from Faz 2). `run_id`, if
    given, is the caller's own authoritative id -- the one that created the
    row and, under the background executor, holds the in-flight lock through
    it. `summary.run_id` is whatever the graph state echoed back, which is
    equal to it only by construction. They are the same today; the
    disagreement is worth a warning rather than a silent write to the wrong
    row, because that write would strand the real row non-terminal and get it
    reconciled as abandoned.
    """
    if run_id is not None and summary.run_id != run_id:
        logger.warning(
            "run summary carries run_id=%s but the run is %s; persisting onto %s "
            "(the caller's id is authoritative)",
            summary.run_id,
            run_id,
            run_id,
        )
    run_id = run_id or summary.run_id

    session_maker = get_sessionmaker()
    async with session_maker() as session:
        existing = (
            await session.execute(select(RunRow).where(RunRow.run_id == run_id))
        ).scalar_one_or_none()

        fields = dict(
            # `block` is a completed run that reached a verdict, not a
            # crashed one -- the failure states Faz 3 reconciles are process
            # deaths, not verifier rejections.
            status="succeeded",
            finished_at=now_naive_utc(),
            duration_seconds=summary.duration_seconds,
            company_branches=summary.company_branches,
            macro_branches=summary.macro_branches,
            findings_count=summary.findings_count,
            failure_count=summary.failure_count,
            verdict=summary.verdict,
            verdict_reason=summary.verdict_reason,
            total_tokens=summary.total_tokens,
            total_cost_usd=summary.total_cost_usd,
            branch_yields=[b.model_dump(mode="json") for b in summary.branch_yields],
        )
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
        else:
            now = now_naive_utc()
            session.add(
                RunRow(
                    run_id=run_id,
                    trigger_type="report",
                    created_at=now,
                    started_at=now,
                    **fields,
                )
            )
        await session.commit()


def save_run_summary(summary, run_id: str | None = None) -> None:
    """Sync wrapper, mirroring `save_draft_report` -- `pipeline/run.py` is
    sync. `summary` is a `pipeline.observability.RunSummary`; `run_id`, when
    given, is the authoritative id to persist onto (see the async body)."""
    asyncio.run(_save_run_summary_async(summary, run_id))


async def _trailing_findings_counts_async(
    *, limit: int, exclude_run_id: str | None = None
) -> list[int]:
    """R3: most recent persisted runs' findings_count, for the yield-floor
    trailing median. Ordered by id (insertion order) rather than by any
    timestamp, so it stays stable when two runs land in the same second.

    Restricted to runs that actually reached a verdict. `runs` now also holds
    `scores` rows (the cheap watchlist refresh, which never touches the LLM)
    and rows for runs still in flight; both carry `findings_count == 0` and
    would drag the trailing median toward zero, quietly disabling the floor.

    `exclude_run_id` drops the run being measured from its own baseline.
    Runs upsert on run_id, so a retry under the same label leaves the previous
    attempt as a row -- and the floor check then compares the run against
    itself. On 2026-08-17 that produced "findings=19 is below 50% of the
    trailing median (162, n=1)", where the 162 was the same run's earlier,
    checkpoint-inflated attempt: a comparison with no information in it.
    """
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        stmt = select(RunRow.findings_count).where(RunRow.verdict.is_not(None))
        if exclude_run_id is not None:
            stmt = stmt.where(RunRow.run_id != exclude_run_id)
        rows = (
            await session.execute(stmt.order_by(RunRow.id.desc()).limit(limit))
        ).scalars().all()
        return list(rows)


def trailing_findings_counts(*, limit: int, exclude_run_id: str | None = None) -> list[int]:
    return asyncio.run(
        _trailing_findings_counts_async(limit=limit, exclude_run_id=exclude_run_id)
    )
