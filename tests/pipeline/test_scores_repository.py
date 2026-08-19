from __future__ import annotations

from datetime import datetime

import pytest_asyncio
import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from pipeline.data import scores_repository
from techinves.db.models import (
    Base,
    CategoryScoreRow,
    CohortRow,
    CompanyRow,
    RiskMetricsRow,
    ScoreHistoryRow,
)
from techinves.db.session import get_sessionmaker

RUN_ID = "test-run"


@pytest_asyncio.fixture
async def seeded_session_maker():
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

        scored = CompanyRow(ticker="NVDA", name="NVIDIA Corp.", cohort_id=cohort.id)
        unscored = CompanyRow(ticker="GAP", name="Gap Co.", cohort_id=cohort.id)
        session.add_all([scored, unscored])
        await session.flush()

        history = ScoreHistoryRow(
            company_id=scored.id,
            run_id=RUN_ID,
            composite_score=84.4,
            composite_band="Strong",
            sector_percentile=92.6,
            sector_percentile_band="Top tier",
            coverage_pct=0.909,
            low_reliability=False,
            regime="profitable",
            cohort_size=15,
            extended_cohort=False,
            distress_ceiling_applied=False,
            warnings=[],
            generated_at=datetime(2026, 8, 10, 12, 0, 0),
        )
        session.add(history)
        await session.flush()
        # Scores are keyed on the run that produced them (ADR 0010 §2), and
        # a report run reads whatever is *current* rather than naming a run
        # of its own -- so the fixture has to set the pointer the loader
        # follows.
        scored.current_score_id = history.id
        await session.flush()

        session.add(
            CategoryScoreRow(
                score_history_id=history.id,
                category_name="valuation",
                score=71.2,
                weight=0.25,
                coverage=1.0,
                metrics=[
                    {"name": "forward_pe", "raw_value": 34.234, "percentile": 60},
                    {
                        "name": "ev_ebitda",
                        "raw_value": 19.038476037006337,
                        "percentile": 80.3030303030303,
                        "weight_used": 0.33,
                    },
                    {
                        "name": "sbc_fcf_yield",
                        "raw_value": 0.014759964116267078,
                        "percentile": 55.12820512820513,
                        "weight_used": 0.40298507462686567,
                    },
                    {
                        "name": "fcf_total_debt",
                        "raw_value": 1.6624559487764927,
                        "percentile": 63.74999999999999,
                        "weight_used": 0.2,
                    },
                    {
                        "name": "ev_sales",
                        "raw_value": None,
                        "percentile": None,
                        "weight_used": 0.0,
                    },
                ],
            )
        )
        session.add(
            RiskMetricsRow(
                score_history_id=history.id,
                score=88.6,
                band="Solid balance sheet",
                altman_z=3.456,
                altman_zone="Safe",
                piotroski_f=8,
                net_debt_ebitda=-0.44,
                interest_coverage=12.345,
                cash_runway_months=None,
                burn_multiple=None,
                dilution_yoy_pct=0.5,
                components_used=["altman_z", "net_debt_ebitda"],
            )
        )
        await session.commit()

    yield session_maker
    await engine.dispose()


def test_widened_payload_carries_categories_and_risk(monkeypatch, seeded_session_maker):
    monkeypatch.setattr(
        scores_repository, "get_sessionmaker", lambda: seeded_session_maker
    )

    scores, financials = scores_repository.load_scores_and_financials(
        ["NVDA", "GAP"], RUN_ID
    )

    nvda = scores["NVDA"]
    assert nvda["composite_score"] == 84
    assert nvda["risk_score"] == 89
    assert nvda["coverage_pct"] == 90.9
    assert nvda["categories"][0]["category_name"] == "valuation"
    assert nvda["categories"][0]["score"] == 71
    assert nvda["risk"]["altman_zone"] == "Safe"
    assert nvda["risk"]["piotroski_f"] == 8
    assert nvda["regime"] == "profitable"

    assert financials["NVDA"]["forward_pe"] == 34.2
    assert financials["NVDA"]["net_debt_to_ebitda"] == -0.4


def test_metrics_are_rounded_to_display_precision(monkeypatch, seeded_session_maker):
    """`categories[*].metrics` used to pass through as the engine's raw
    floats, and the score-attribution appendix printed
    `raw=19.038476037006337` into a published report. Precision follows the
    value's scale, since one table mixes ~19x ratios with ~0.015 yields."""
    monkeypatch.setattr(
        scores_repository, "get_sessionmaker", lambda: seeded_session_maker
    )

    scores, _ = scores_repository.load_scores_and_financials(["NVDA"], RUN_ID)
    metrics = {m["name"]: m for m in scores["NVDA"]["categories"][0]["metrics"]}

    assert metrics["ev_ebitda"]["raw_value"] == 19.0
    assert metrics["ev_ebitda"]["percentile"] == 80.3
    assert metrics["fcf_total_debt"]["raw_value"] == 1.66
    assert metrics["fcf_total_debt"]["percentile"] == 63.7
    assert metrics["sbc_fcf_yield"]["raw_value"] == 0.015
    assert metrics["sbc_fcf_yield"]["weight_used"] == 0.4
    # An unavailable metric keeps its None -- rounding must not invent a 0.
    assert metrics["ev_sales"]["raw_value"] is None
    assert metrics["ev_sales"]["percentile"] is None


def test_missing_score_row_is_marked_not_dropped(monkeypatch, seeded_session_maker):
    monkeypatch.setattr(
        scores_repository, "get_sessionmaker", lambda: seeded_session_maker
    )

    scores, financials = scores_repository.load_scores_and_financials(
        ["NVDA", "GAP"], RUN_ID
    )

    assert "GAP" in scores
    assert scores["GAP"]["missing"] is True
    assert scores["GAP"]["reason"]
    assert scores["GAP"]["cohort"] == "A"
    assert financials["GAP"] == {}


async def test_loads_the_current_score_when_no_run_is_named(seeded_session_maker, monkeypatch):
    """The production call: `run_pipeline` names no score run, so the loader
    follows `companies.current_score_id` -- whichever `scores` run last
    wrote it."""
    monkeypatch.setattr(scores_repository, "get_sessionmaker", lambda: seeded_session_maker)

    scores, _ = await scores_repository._load_async(["NVDA", "GAP"], None)

    assert scores["NVDA"]["composite_score"] == 84
    assert scores["GAP"]["missing"] is True
