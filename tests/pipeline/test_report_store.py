from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from pipeline.observability import summarize_run
from pipeline.schemas import BranchYield, VerifierReport
from pipeline.storage import report_store
from techinves.db.models import (
    Base,
    CohortRow,
    CompanyRow,
    ReportRow,
    ReportSectionRow,
    RunRow,
)
from techinves.db.session import get_sessionmaker

SAMPLE_BODY = """# TechInves Weekly -- Week of Aug 10, 2026

*This report is a screening and ranking tool... This is not investment advice.*

## This Week's Highlights

### NVDA -- NVIDIA Corp.

Data-center demand commentary dominated NVDA's news flow this week ([reuters.com](https://reuters.com)).

Forward P/E stands at {{NVDA.forward_pe}}.

```
COMPOSITE SCORE: {{NVDA.composite_score}}
```

### AMD -- Advanced Micro Devices

AMD announced a new product line ([cnbc.com](https://cnbc.com)).

## Full Watchlist -- Score Summary

| Ticker | Composite |
|---|---|
| NVDA | {{NVDA.composite_score}} |

## Sector & Macro

Rates held steady this week ([reuters.com](https://reuters.com)).

## This Week's Coverage Notes

All 42 tickers had usable data this week.
"""


def test_split_into_sections_tags_highlight_tickers_and_macro():
    sections = report_store.split_into_sections(
        SAMPLE_BODY, title="TechInves Weekly", researched_tickers=["NVDA", "AMD"]
    )

    company_sections = {s["ticker"]: s for s in sections if s["section_type"] == "company"}
    assert set(company_sections) == {"NVDA", "AMD"}
    assert "Data-center demand" in company_sections["NVDA"]["body_markdown"]
    assert "new product line" in company_sections["AMD"]["body_markdown"]

    macro_sections = [s for s in sections if s["section_type"] == "macro"]
    assert any(s["topic"] == "overview" for s in macro_sections)
    assert any("Full Watchlist" in (s["topic"] or "") for s in macro_sections)
    assert any("Sector & Macro" in (s["topic"] or "") for s in macro_sections)
    assert any("Coverage Notes" in (s["topic"] or "") for s in macro_sections)

    # order_index is monotonic and matches document order
    indices = [s["order_index"] for s in sections]
    assert indices == sorted(indices)


def test_split_into_sections_no_headings_falls_back_to_single_macro_section():
    sections = report_store.split_into_sections(
        "Just a plain paragraph, no headings.", title="T", researched_tickers=[]
    )
    assert len(sections) == 1
    assert sections[0]["section_type"] == "macro"
    assert sections[0]["topic"] == "overview"


def test_excerpt_of_skips_headings_and_table_rows():
    body = """# Title

| Ticker | Composite |
|---|---|
| NVDA | 84 |

This is the real first paragraph of prose."""
    excerpt = report_store.excerpt_of(body)
    assert "|" not in excerpt
    assert "real first paragraph" in excerpt


@pytest_asyncio.fixture
async def empty_session_maker():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = get_sessionmaker(engine)
    async with session_maker() as session:
        cohort = CohortRow(code="A", label="Software & Internet", weight_profile={})
        session.add(cohort)
        await session.flush()
        session.add_all(
            [
                CompanyRow(ticker="NVDA", name="NVIDIA Corp.", cohort_id=cohort.id),
                CompanyRow(ticker="AMD", name="Advanced Micro Devices", cohort_id=cohort.id),
            ]
        )
        await session.commit()

    yield session_maker
    await engine.dispose()


async def test_save_draft_report_creates_typed_sections(monkeypatch, empty_session_maker):
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    slug = await report_store._save_draft_async(
        run_id="run-a",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA", "AMD"],
    )

    async with empty_session_maker() as session:
        sections = (
            await session.execute(
                select(ReportSectionRow).join(ReportSectionRow.report).where(
                    ReportSectionRow.report.has(slug=slug)
                )
            )
        ).scalars().all()

    assert len(sections) > 1
    section_types = {s.section_type for s in sections}
    assert section_types == {"company", "macro"}
    tickers = {s.ticker for s in sections if s.section_type == "company"}
    assert tickers == {"NVDA", "AMD"}


async def test_save_draft_report_persists_verdict_and_partial_flag(
    monkeypatch, empty_session_maker
):
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    slug = await report_store._save_draft_async(
        run_id="run-a",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA", "AMD"],
        verifier_verdict="pass_with_flags",
        is_partial=True,
    )

    async with empty_session_maker() as session:
        report = (
            await session.execute(select(ReportRow).where(ReportRow.slug == slug))
        ).scalar_one()

    assert report.verifier_verdict == "pass_with_flags"
    assert report.is_partial is True


async def test_save_draft_report_persists_section_scores(monkeypatch, empty_session_maker):
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    slug = await report_store._save_draft_async(
        run_id="run-a",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA", "AMD"],
        section_scores=[{"section": "NVDA", "confidence": 8, "rationale": "consistent"}],
    )

    async with empty_session_maker() as session:
        report = (
            await session.execute(select(ReportRow).where(ReportRow.slug == slug))
        ).scalar_one()

    assert report.section_scores == [
        {"section": "NVDA", "confidence": 8, "rationale": "consistent"}
    ]


async def test_save_draft_report_persists_classified_violations(
    monkeypatch, empty_session_maker
):
    """Faz 5.3 / ADR 0010 §6: the banner has to *name* the violations, so the
    classified list has to survive the run. Before this, `classify_violations`
    ran every time and its output died with the process."""
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    violations = [
        {
            "severity": "compliance_hard",
            "category": "citation",
            "message": "fabricated citation (URL never retrieved): https://example.com/x",
            "section": "NVDA",
        },
        {
            "severity": "soft",
            "category": "low_reliability_label",
            "message": "missing 'low reliability' label for AMD",
            "section": "AMD",
        },
    ]

    slug = await report_store._save_draft_async(
        run_id="run-a",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA", "AMD"],
        verifier_verdict="block",
        verifier_violations=violations,
    )

    async with empty_session_maker() as session:
        report = (
            await session.execute(select(ReportRow).where(ReportRow.slug == slug))
        ).scalar_one()

    assert report.verifier_verdict == "block"
    # Round-trips whole, severity and section included -- the banner renders
    # both, and a lossy round-trip would silently downgrade what it can say.
    assert report.verifier_violations == violations


async def test_no_violations_and_no_verifier_report_are_stored_differently(
    monkeypatch, empty_session_maker
):
    """`[]` ("the verifier ran and found nothing") and `None` ("no verifier
    report existed") must not collapse into each other: the front-end shows
    the second as an *unverified* report, and folding it into the first would
    render an unchecked report as a clean one."""
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    clean_slug = await report_store._save_draft_async(
        run_id="run-clean",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA"],
        verifier_verdict="pass",
        verifier_violations=[],
    )
    unknown_slug = await report_store._save_draft_async(
        run_id="run-unknown",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA"],
    )

    async with empty_session_maker() as session:
        clean = (
            await session.execute(select(ReportRow).where(ReportRow.slug == clean_slug))
        ).scalar_one()
        unknown = (
            await session.execute(
                select(ReportRow).where(ReportRow.slug == unknown_slug)
            )
        ).scalar_one()

    assert clean.verifier_violations == []
    assert unknown.verifier_violations is None
    assert unknown.verifier_verdict is None


async def test_re_saving_a_run_clears_stale_violations(monkeypatch, empty_session_maker):
    """A retry that now passes must not leave the previous attempt's
    violations on the row -- the banner would keep naming problems the report
    no longer has, which erodes the banner exactly as fast as missing ones."""
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    await report_store._save_draft_async(
        run_id="run-a",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA"],
        verifier_verdict="block",
        verifier_violations=[
            {
                "severity": "compliance_hard",
                "category": "disclaimer",
                "message": "report is missing the required disclaimer phrase",
                "section": None,
            }
        ],
    )
    slug = await report_store._save_draft_async(
        run_id="run-a",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA"],
        verifier_verdict="pass",
        verifier_violations=[],
    )

    async with empty_session_maker() as session:
        report = (
            await session.execute(select(ReportRow).where(ReportRow.slug == slug))
        ).scalar_one()

    assert report.verifier_verdict == "pass"
    assert report.verifier_violations == []


def _summary_state(**overrides) -> dict:
    base = dict(
        run_id="run-a",
        highlight_tickers=["NVDA"],
        macro_topics=[],
        research_findings=[],
        failures=[],
        branch_yields=[
            BranchYield(scope="company", ticker="NVDA", findings_count=3, tokens=200, cost_usd=0.02)
        ],
        verifier_report=VerifierReport(verdict="pass"),
    )
    base.update(overrides)
    return base


async def test_save_run_summary_persists_a_run_row(monkeypatch, empty_session_maker):
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)
    summary = summarize_run(_summary_state(), duration_seconds=12.5)

    await report_store._save_run_summary_async(summary)

    async with empty_session_maker() as session:
        row = (
            await session.execute(
                select(RunRow).where(RunRow.run_id == "run-a")
            )
        ).scalar_one()

    assert row.findings_count == 0
    assert row.total_tokens == 200
    assert row.verdict == "pass"
    assert row.branch_yields[0]["ticker"] == "NVDA"
    # The measurement columns ADR 0004 §8 depends on survived the merge of
    # `pipeline_runs` into `runs`, and the run lands in a terminal state.
    assert row.status == "succeeded"
    assert row.finished_at is not None
    assert row.duration_seconds == 12.5


async def test_save_run_summary_upserts_on_run_id(monkeypatch, empty_session_maker):
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)
    summary1 = summarize_run(_summary_state(), duration_seconds=1.0)
    summary2 = summarize_run(
        _summary_state(verifier_report=VerifierReport(verdict="block")), duration_seconds=2.0
    )

    await report_store._save_run_summary_async(summary1)
    await report_store._save_run_summary_async(summary2)

    async with empty_session_maker() as session:
        rows = (
            await session.execute(
                select(RunRow).where(RunRow.run_id == "run-a")
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].verdict == "block"


def test_split_into_sections_types_deep_dive_outside_top3_as_company():
    """R8: a ticker that got a deep-dive but wasn't in the top-3 highlight
    selection must still type as `company`, not fall back to `macro`."""
    body = (
        "# Weekly\n\n"
        "### WDAY -- Workday Inc.\n\n"
        "Silver Lake buyout rumor.\n\n"
        "### NVDA -- NVIDIA Corp.\n\n"
        "Chip launch.\n"
    )
    sections = report_store.split_into_sections(
        body, title="T", researched_tickers=["NVDA", "WDAY", "MSFT"]
    )
    company_tickers = {s["ticker"] for s in sections if s["section_type"] == "company"}
    assert company_tickers == {"WDAY", "NVDA"}


async def test_trailing_findings_counts_returns_most_recent_first(
    monkeypatch, empty_session_maker
):
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)
    for run_id, count in [("2026-07-27", 10), ("2026-08-03", 12), ("2026-08-10", 8)]:
        summary = summarize_run(
            _summary_state(run_id=run_id, research_findings=[object()] * count),
            duration_seconds=1.0,
        )
        # findings_count comes from len(research_findings); use plain dicts
        # since Finding validation isn't needed for this count-only test.
        summary.findings_count = count
        await report_store._save_run_summary_async(summary)

    counts = await report_store._trailing_findings_counts_async(limit=2)
    assert counts == [8, 12]


def test_badgeable_highlights_drops_a_selection_that_got_no_section():
    """ADR 0006 §3.4's "agree by construction" holds for the input list,
    not the output: the writer may write 3 deep-dives from a 4-ticker
    selection (REPORT_SPEC.md §10 allows either), leaving a badge pointing
    at a section that isn't in the report. Seen on 2026-08-17: SNOW badged,
    only WDAY/META/MSFT written up."""
    from pipeline.storage.report_store import _badgeable_highlights

    sections = [
        {"section_type": "macro", "ticker": None},
        {"section_type": "company", "ticker": "MSFT"},
        {"section_type": "company", "ticker": "WDAY"},
        {"section_type": "company", "ticker": "META"},
    ]
    assert _badgeable_highlights(["MSFT", "WDAY", "SNOW", "META"], sections) == [
        "MSFT",
        "WDAY",
        "META",
    ]


def test_badgeable_highlights_keeps_everything_when_typing_failed():
    """Zero company sections means section typing broke outright. Emptying
    the badges here would turn that visible inconsistency into a silently
    empty badge row."""
    from pipeline.storage.report_store import _badgeable_highlights

    sections = [{"section_type": "macro", "ticker": None}]
    assert _badgeable_highlights(["MSFT", "SNOW"], sections) == ["MSFT", "SNOW"]


def test_slug_is_derived_from_the_run_id():
    """Plan §9 Q4, decided: report slugs are run-derived. Nothing external
    links to these URLs, so the simplest run-keyed scheme wins."""
    assert report_store.slug_for_run("20260818T101500-a1b2c3") == "run-20260818T101500-a1b2c3"


async def test_saved_report_is_keyed_on_its_run(monkeypatch, empty_session_maker):
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    slug = await report_store._save_draft_async(
        run_id="run-b",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA"],
        verifier_verdict="pass",
    )

    assert slug == "run-run-b"
    async with empty_session_maker() as session:
        report = (
            await session.execute(select(ReportRow).where(ReportRow.slug == slug))
        ).scalar_one()
    assert report.run_id == "run-b"


async def test_two_runs_on_the_same_day_produce_two_reports(
    monkeypatch, empty_session_maker
):
    """The case week identity could not represent at all (ADR 0010 §2).

    Under `week_of` keying both of these collapsed onto one slug and the
    second silently overwrote the first, which is why the week-premised
    re-run guard existed. Two runs now simply produce two reports.
    """
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    first = await report_store._save_draft_async(
        run_id="morning",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA"],
        verifier_verdict="pass",
    )
    second = await report_store._save_draft_async(
        run_id="afternoon",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA"],
        verifier_verdict="pass_with_flags",
    )

    assert first != second
    async with empty_session_maker() as session:
        rows = (await session.execute(select(ReportRow))).scalars().all()
    assert {r.slug for r in rows} == {"run-morning", "run-afternoon"}


async def test_re_saving_the_same_run_overwrites_the_same_row(
    monkeypatch, empty_session_maker
):
    """A retry under the same run id (`--label`) overwrites in place rather
    than minting a second row -- the idempotency that used to be keyed on
    the week is now keyed on the run."""
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    await report_store._save_draft_async(
        run_id="run-b",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA"],
        verifier_verdict="pass",
    )
    second_slug = await report_store._save_draft_async(
        run_id="run-b",
        title="TechInves Weekly",
        body_markdown=SAMPLE_BODY,
        highlight_tickers=["NVDA"],
        verifier_verdict="pass_with_flags",
    )
    assert second_slug == "run-run-b"

    async with empty_session_maker() as session:
        rows = (
            await session.execute(select(ReportRow).where(ReportRow.run_id == "run-b"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].verifier_verdict == "pass_with_flags"


async def test_start_run_creates_a_running_row_before_any_work(
    monkeypatch, empty_session_maker
):
    """ADR 0010 §3: the run is observable while it runs, and every row keyed
    onto it needs its parent to exist first."""
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    await report_store._start_run_async("run-c", trigger_type="company", ticker="NVDA")

    async with empty_session_maker() as session:
        row = (
            await session.execute(select(RunRow).where(RunRow.run_id == "run-c"))
        ).scalar_one()

    assert row.status == "running"
    assert row.trigger_type == "company"
    assert row.ticker == "NVDA"
    assert row.created_at is not None
    assert row.started_at is not None
    assert row.finished_at is None


async def test_trailing_findings_counts_ignores_runs_without_a_verdict(
    monkeypatch, empty_session_maker
):
    """`runs` now also holds `scores` runs and in-flight rows, both with
    findings_count 0. Letting those into the yield-floor baseline would drag
    the trailing median to zero and quietly disable the floor (R3)."""
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    await report_store._save_run_summary_async(
        summarize_run(_summary_state(run_id="reported"), duration_seconds=1.0)
    )
    await report_store._start_run_async("scores-refresh", trigger_type="scores", ticker=None)

    counts = await report_store._trailing_findings_counts_async(limit=8)

    assert counts == [0]  # the reported run only; the scores run is excluded


async def test_save_run_summary_persists_onto_the_callers_run_id(
    monkeypatch, empty_session_maker, caplog
):
    """Faz 3, carried over from Faz 2: the executor's id is authoritative.

    `summary.run_id` is whatever the graph state echoed back; the `run_id`
    argument is the id that created the row and, under the background
    executor, holds the in-flight lock through it. They are equal by
    construction today, and this test pins the behaviour when they are not --
    writing the summary onto the echoed id would strand the real row
    non-terminal, where startup reconciliation would later mark it abandoned.
    """
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)
    summary = summarize_run(_summary_state(run_id="echoed-back"), duration_seconds=3.0)

    with caplog.at_level("WARNING"):
        await report_store._save_run_summary_async(summary, "executor-owned")

    async with empty_session_maker() as session:
        run_ids = (await session.execute(select(RunRow.run_id))).scalars().all()
    assert list(run_ids) == ["executor-owned"]
    assert "echoed-back" in caplog.text and "authoritative" in caplog.text


async def test_start_run_refuses_when_the_in_flight_lock_is_held(
    monkeypatch, empty_session_maker
):
    """ADR 0010 §4's lock is a unique partial index, so it also binds callers
    that reach the pipeline directly (the CLI). The raw IntegrityError is
    translated into a refusal naming the run that holds it."""
    monkeypatch.setattr(report_store, "get_sessionmaker", lambda: empty_session_maker)

    await report_store._start_run_async("first", trigger_type="report", ticker=None)

    with pytest.raises(report_store.RunLockHeld) as excinfo:
        await report_store._start_run_async("second", trigger_type="report", ticker=None)
    assert "first" in str(excinfo.value)

    # A different trigger type is unaffected -- the lock is per action type.
    await report_store._start_run_async("scores-1", trigger_type="scores", ticker=None)

    # And reopening the run that holds it is not a collision: this is exactly
    # what the background executor does on its own row.
    await report_store._start_run_async("first", trigger_type="report", ticker=None)
