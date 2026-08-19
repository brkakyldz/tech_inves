"""Faz 0 seed script: loads `front-end/lib/data/{scores,score-history}.json`
into the DB so the API serves, byte-for-byte in shape, what the front-end's
mock data layer serves today -- before any real ingestion job exists. See
`reports/research/BACKEND_IMPLEMENTATION_PLAN.md` Section 10, Faz 0.

Usage:
    techinves-seed-mock [--database-url URL] [--reset]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime, time
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from techinves.api._time import now_naive_utc
from techinves.api.schemas import COHORT_LABELS
from techinves.db.models import (
    Base,
    CategoryScoreRow,
    CohortRow,
    CompanyRow,
    ReportHighlightRow,
    ReportRow,
    ReportSectionRow,
    RiskMetricsRow,
    RunRow,
    ScoreHistoryRow,
)
from techinves.db.session import get_sessionmaker, make_engine

REPO_ROOT = Path(__file__).resolve().parents[3]
SCORES_JSON = REPO_ROOT / "front-end" / "lib" / "data" / "scores.json"
HISTORY_JSON = REPO_ROOT / "front-end" / "lib" / "data" / "score-history.json"
REPORTS_JSON = REPO_ROOT / "front-end" / "lib" / "data" / "reports.json"

CATEGORY_LABEL_TO_KEY = {
    "Valuation": "valuation",
    "Growth": "growth",
    "Profitability & Quality": "quality",
    "Financial Health": "financial_health",
}

QUARTER_END = {"Q1": (3, 31), "Q2": (6, 30), "Q3": (9, 30), "Q4": (12, 31)}

# Every seeded score row and report row is keyed onto a run (ADR 0010 §2), so
# the seed mints the runs it needs. One `scores` run per historical quarter
# plus one for the current snapshot; one `report` run per fixture report.
SEED_SCORES_RUN_PREFIX = "mock-seed"
SEED_CURRENT_RUN_ID = "mock-seed-current"

# Faz 6 (`reports/backlog/verifier-banner-followups.md` item 1): the fixtures
# never went through a real verifier run, so writing a `pass` for all five
# would be fabricating exactly the signal the banner exists to convey (ADR
# 0010 §6). Seeding the verdict *range* instead -- one of each interesting
# state, the rest `pass` -- is defensible because these are already
# admittedly-fictional fixtures (the financials are invented too) and a demo
# that shows nothing but "Unverified" hides the single most interesting
# behaviour the banner has. Conditional on the demo state being unmistakably
# labelled elsewhere in the UI (`front-end/components/layout/DemoModeBanner.tsx`)
# -- see this phase's report for why that satisfies the condition.
#
# `violations` mirror pipeline/verifier/rules.py's actual shape
# (severity/category/message/section) and are each a plausible instance of a
# check the verifier really runs -- not placeholder text.
SEED_VERIFIER_VERDICTS: dict[str, dict] = {
    "2026-08-04-yari-iletken-kohortu-ayrisiyor": {
        "verdict": "block",
        "violations": [
            {
                "severity": "compliance_hard",
                "category": "citation",
                "message": "fabricated citation (URL never retrieved): https://ir.nvidia.com/news/2026-q2-datacenter-update",
                "section": "NVDA",
            },
            {
                "severity": "compliance_hard",
                "category": "number_leak",
                "message": "number not found in scores/financials: '38.4%'",
                "section": "INTC",
            },
        ],
    },
    "2026-07-28-bulut-guvenlik-marjlari": {
        "verdict": "degraded_publish",
        "violations": [
            {
                "severity": "structural_hard",
                "category": "completeness",
                "message": "2 deep-dive sections found, expected 3-4",
                "section": None,
            },
        ],
    },
    "2026-07-21-yazilim-buyume-yavaslamasi": {
        "verdict": "pass_with_flags",
        "violations": [
            {
                "severity": "soft",
                "category": "low_reliability_label",
                "message": "missing 'low reliability' label for NOW",
                "section": "NOW",
            },
        ],
    },
    "2026-07-14-uzay-kohortu-erken-asama-riski": {"verdict": "pass", "violations": []},
    "2026-07-07-donanim-dongu-baskisi": {"verdict": "pass", "violations": []},
}


def _seed_run(
    run_id: str, *, trigger_type: str, created_at: datetime, company_count: int = 0
) -> RunRow:
    return RunRow(
        run_id=run_id,
        trigger_type=trigger_type,
        status="succeeded",
        created_at=created_at,
        started_at=created_at,
        finished_at=created_at,
        company_count=company_count,
    )


def _quarter_to_date(period: str) -> date:
    year_s, q = period.split("-")
    month, day = QUARTER_END[q]
    return date(int(year_s), month, day)


def _band_for(score: float) -> str:
    if score >= 80:
        return "Strong"
    if score >= 65:
        return "Good"
    if score >= 45:
        return "Moderate"
    if score >= 30:
        return "Weak"
    return "Very Weak"


def _add_current_score_rows(session, sh: ScoreHistoryRow, entry: dict) -> None:
    """Attach the per-category and risk rows that make a ScoreHistoryRow the
    "current" snapshot the API serves. Factored out so both the normal
    (history-point-lands-on-current-week) and the no-history paths in
    seed() build an identical current row."""
    for cat in entry["categories"]:
        session.add(
            CategoryScoreRow(
                score_history_id=sh.id,
                category_name=CATEGORY_LABEL_TO_KEY[cat["name"]],
                score=cat["score"],
                weight=cat["weight"],
                coverage=1.0,
                metrics=[],
            )
        )
    session.add(
        RiskMetricsRow(
            score_history_id=sh.id,
            score=70.0,
            band="Adequate",
            altman_z=None,
            altman_zone="Unavailable",
            piotroski_f=None,
            net_debt_ebitda=None,
            interest_coverage=None,
            cash_runway_months=None,
            burn_multiple=None,
            dilution_yoy_pct=None,
            components_used=[],
        )
    )


async def seed(engine: AsyncEngine, *, reset: bool = False) -> int:
    """Load the front-end's mock JSON into the DB.

    Seeded reports are simply visible: ADR 0010 §5 removed publication
    state, so there is no longer a draft/published distinction for the old
    `--publish-reports` flag to toggle.

    That is the intended behaviour, not an oversight -- ADR 0010 §7 makes
    the keyless demo a first-class mode in which the app seeds itself from
    these fixtures and is *fully navigable*, so the fixtures have to be
    reachable through `/v1/reports` and `/v1/reports/{slug}`, not only
    through `/v1/reports/latest`. Ordering is by `created_at`, which keeps
    real pipeline output written after a seed sorting ahead of the fixtures,
    but that is presentation only: seeding a database that also holds real
    output puts the five Turkish fixture reports into the public list
    alongside it. Seed a demo database, not a working one.
    """
    async with engine.begin() as conn:
        if reset:
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    scores = json.loads(SCORES_JSON.read_text(encoding="utf-8"))
    history = json.loads(HISTORY_JSON.read_text(encoding="utf-8"))
    reports = json.loads(REPORTS_JSON.read_text(encoding="utf-8")) if REPORTS_JSON.exists() else []

    session_maker = get_sessionmaker(engine)
    async with session_maker() as session:
        if reset:
            for model in (
                ReportHighlightRow,
                ReportSectionRow,
                ReportRow,
                RiskMetricsRow,
                CategoryScoreRow,
                ScoreHistoryRow,
                CompanyRow,
                CohortRow,
                RunRow,
            ):
                await session.execute(delete(model))

        cohort_rows: dict[str, CohortRow] = {}
        for code, label in COHORT_LABELS.items():
            row = CohortRow(code=code, label=label, weight_profile={}, methodology_version="1.0")
            session.add(row)
            cohort_rows[code] = row
        await session.flush()

        # A single shared "current" run across every company, independent of
        # any one ticker's own history: `list_companies` filters on one global
        # latest run (repositories.py::_latest_score_run_id), so every ticker
        # needs a ScoreHistoryRow in that run to appear in the list -- even a
        # ticker with an empty history array (a real current score with no
        # backfilled quarters, e.g. a just-added watchlist ticker). The
        # fixtures are quarter-labelled, so the newest quarter any ticker has
        # is what the current run stands in for; falls back to today when no
        # ticker has any history at all.
        all_latest_weeks = [
            _quarter_to_date(pts[-1]["period"]) for pts in history.values() if pts
        ]
        current_period = max(all_latest_weeks, default=date.today())

        # The runs, in the order their scores were produced: historical
        # quarters oldest-first, then the current snapshot last, so
        # `_latest_score_run_id` (insertion order) resolves to the current one.
        periods = sorted({p["period"] for pts in history.values() for p in pts})
        run_ids: dict[str, str] = {}
        for period in periods:
            if _quarter_to_date(period) == current_period:
                continue
            run_id = f"{SEED_SCORES_RUN_PREFIX}-{period}"
            run_ids[period] = run_id
            session.add(
                _seed_run(
                    run_id,
                    trigger_type="scores",
                    created_at=datetime.combine(_quarter_to_date(period), time()),
                    company_count=len(scores),
                )
            )
        session.add(
            _seed_run(
                SEED_CURRENT_RUN_ID,
                trigger_type="scores",
                created_at=now_naive_utc(),
                company_count=len(scores),
            )
        )
        await session.flush()

        for entry in scores:
            ticker = entry["ticker"]
            cohort_row = cohort_rows[entry["cohort"]]
            company = CompanyRow(
                ticker=ticker,
                name=entry["companyName"],
                cohort_id=cohort_row.id,
                is_active=True,
            )
            session.add(company)
            await session.flush()

            points = history.get(ticker, [])
            has_current_point = any(
                _quarter_to_date(p["period"]) == current_period for p in points
            )

            for point in points:
                is_current = _quarter_to_date(point["period"]) == current_period
                sh = ScoreHistoryRow(
                    company_id=company.id,
                    run_id=(
                        SEED_CURRENT_RUN_ID
                        if is_current
                        else run_ids[point["period"]]
                    ),
                    composite_score=point["compositeScore"],
                    composite_band=_band_for(point["compositeScore"]),
                    sector_percentile=entry["sectorPercentile"] if is_current else point["compositeScore"],
                    sector_percentile_band="",
                    coverage_pct=1.0,
                    low_reliability=False,
                    regime="profitable",
                    cohort_size=len([s for s in scores if s["cohort"] == entry["cohort"]]),
                    extended_cohort=False,
                    distress_ceiling_applied=False,
                    warnings=[],
                    generated_at=now_naive_utc(),
                )
                session.add(sh)
                await session.flush()

                if is_current:
                    _add_current_score_rows(session, sh, entry)
                    company.current_score_id = sh.id

            # No history point lands on the shared current run -- either the
            # ticker has no backfilled history at all (a newly added watchlist
            # ticker, real current score, empty history by design) or its
            # last historical point predates it. Either way it still needs a
            # row in the current run to appear in `list_companies`, sourced
            # from the entry's own (real, live-scored) top-level fields, not
            # invented.
            if not has_current_point:
                sh = ScoreHistoryRow(
                    company_id=company.id,
                    run_id=SEED_CURRENT_RUN_ID,
                    composite_score=entry["compositeScore"],
                    composite_band=entry["band"],
                    sector_percentile=entry["sectorPercentile"],
                    sector_percentile_band="",
                    coverage_pct=entry.get("coveragePct", 1.0),
                    low_reliability=entry.get("lowReliability", False),
                    regime="profitable",
                    cohort_size=len([s for s in scores if s["cohort"] == entry["cohort"]]),
                    extended_cohort=False,
                    distress_ceiling_applied=False,
                    warnings=[],
                    generated_at=now_naive_utc(),
                )
                session.add(sh)
                await session.flush()
                _add_current_score_rows(session, sh, entry)
                company.current_score_id = sh.id

        for entry in reports:
            # The fixtures are still dated (`weekOf`) because that is what
            # they are -- archive material from the weekly era. The date is
            # used only for ordering; the report is keyed on a run like any
            # other (ADR 0010 §2).
            created_at = datetime.combine(date.fromisoformat(entry["weekOf"]), time())
            run_id = f"{SEED_SCORES_RUN_PREFIX}-report-{entry['weekOf']}"
            session.add(
                _seed_run(run_id, trigger_type="report", created_at=created_at)
            )
            verdict_fixture = SEED_VERIFIER_VERDICTS.get(entry["slug"], {"verdict": "pass", "violations": []})
            report = ReportRow(
                # `created_at` is derived from the fixture's own date rather
                # than "now": reports are ordered by `created_at` descending
                # (repositories.list_reports / get_latest_report), and stamping
                # every fixture with the seed's wall clock would order them by
                # insertion instead -- making the oldest the "latest" report
                # purely because it was inserted last.
                created_at=created_at,
                slug=entry["slug"],
                run_id=run_id,
                title=entry["title"],
                summary=entry["excerpt"],
                # See SEED_VERIFIER_VERDICTS above -- a deliberate spread
                # across the verdict range, not a fabricated blanket "pass".
                verifier_verdict=verdict_fixture["verdict"],
                verifier_violations=verdict_fixture["violations"] or None,
            )
            session.add(report)
            await session.flush()

            session.add(
                ReportSectionRow(
                    report_id=report.id,
                    section_type="macro",
                    ticker=None,
                    topic="haftalık özet",
                    title=entry["title"],
                    body_markdown=entry["excerpt"],
                    order_index=0,
                )
            )
            for rank, ticker in enumerate(entry["highlightedTickers"]):
                session.add(ReportHighlightRow(report_id=report.id, ticker=ticker, rank=rank))

        await session.commit()

    return len(scores)


async def seed_if_empty(engine: AsyncEngine) -> int:
    """Faz 6's seed-on-empty trigger point (ADR 0010 §7): called from
    `api/main.py`'s startup lifespan, after reconciliation, only when the
    app has classified itself as demo mode. Seeds the fixtures with
    `reset=False` when the database has no companies yet, and is a no-op
    otherwise.

    `reset=False` is deliberate and different from `techinves-seed-mock
    --reset`'s normal usage: this runs on every keyless startup, and a
    real run's data (should the user later add keys, trigger a real run,
    then restart without keys again) must never be dropped by an app boot.
    The emptiness check is what makes this idempotent -- a database that
    already has one company, seeded or real, is left alone.
    """
    session_maker = get_sessionmaker(engine)
    async with session_maker() as session:
        count = (await session.execute(select(func.count()).select_from(CompanyRow))).scalar_one()
    if count > 0:
        return 0
    return await seed(engine, reset=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--reset", action="store_true", help="drop and recreate all tables first")
    args = parser.parse_args()

    engine = make_engine(args.database_url)
    count = asyncio.run(seed(engine, reset=args.reset))
    print(f"seeded {count} companies from mock data")


if __name__ == "__main__":
    main()
