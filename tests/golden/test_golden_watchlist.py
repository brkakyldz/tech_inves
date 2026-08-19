"""End-to-end test over a synthetic 13-company cohort (meeting the
methodology's minimum cohort size), run through the full
engine.score_watchlist() pipeline. Catches wiring regressions across
cohort/regime/normalization/category/risk/composite/coverage that the
narrower unit tests (which test one module at a time) would miss.

Substitutes a synthetic watchlist and feeds pre-built RawFinancialFacts
through StubFactsProvider, bypassing both data sources entirely -- no live
calls, and no EDGAR/FMP JSON shapes needed here.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from techinves.models import Cohort, RawFinancialFacts
from techinves.output.formatter import format_score_block
from techinves.scoring import engine as engine_module
from tests.conftest import StubFactsProvider, make_company_facts


def _build_cohort_c_facts() -> dict:
    facts = {}
    for i in range(12):
        ticker = f"IT{i}"
        facts[ticker] = make_company_facts(
            ticker,
            base_revenue=500_000_000.0 * (1 + i * 0.1),
            revenue_growth=0.05 + i * 0.01,
            gross_margin=0.45 + i * 0.01,
            operating_margin=0.10 + i * 0.005,
            total_assets=1_400_000_000.0,
            total_debt=100_000_000.0,
            cash=200_000_000.0,
            equity=800_000_000.0,
            liabilities=400_000_000.0,
            current_assets=350_000_000.0,
            current_liabilities=150_000_000.0,
            retained_earnings=300_000_000.0,
        )

    # One company designed to trip the distress ceiling: negative equity,
    # negative retained earnings, thin interest coverage, positive net debt.
    facts["DISTRESSED"] = make_company_facts(
        "DISTRESSED",
        base_revenue=200_000_000.0,
        revenue_growth=0.02,
        gross_margin=0.30,
        operating_margin=0.02,
        total_assets=900_000_000.0,
        total_debt=900_000_000.0,
        cash=20_000_000.0,
        equity=-100_000_000.0,
        liabilities=1_000_000_000.0,
        current_assets=80_000_000.0,
        current_liabilities=150_000_000.0,
        retained_earnings=-500_000_000.0,
        interest_expense=60_000_000.0,
    )
    return facts


def _cohort_c_watchlist() -> dict[str, Cohort]:
    tickers = [f"IT{i}" for i in range(12)] + ["DISTRESSED"]
    return {t: Cohort.IT_SERVICES_INFRA for t in tickers}


@pytest.fixture
def golden_watchlist(monkeypatch):
    watchlist = _cohort_c_watchlist()
    facts = _build_cohort_c_facts()

    monkeypatch.setattr(engine_module, "load_watchlist", lambda: watchlist)
    return StubFactsProvider(facts)


def test_all_companies_get_scored(golden_watchlist):
    blocks = engine_module.score_watchlist(provider=golden_watchlist)
    assert set(blocks.keys()) == set(golden_watchlist.facts_by_ticker)
    for block in blocks.values():
        assert 0.0 <= block.composite_score <= 100.0
        assert block.cohort_size == 13
        assert block.extended_cohort is False


def test_distress_ceiling_applies_to_distressed_company(golden_watchlist):
    blocks = engine_module.score_watchlist(provider=golden_watchlist)
    distressed = blocks["DISTRESSED"]
    assert distressed.distress_ceiling_applied is True
    assert distressed.composite_score <= 70.0
    assert "distress ceiling applied" in distressed.warnings
    assert distressed.risk.altman_zone.value == "Distressed"


def test_healthy_companies_are_not_distress_capped(golden_watchlist):
    blocks = engine_module.score_watchlist(provider=golden_watchlist)
    for i in range(12):
        assert blocks[f"IT{i}"].distress_ceiling_applied is False


def test_sector_percentiles_span_the_cohort(golden_watchlist):
    blocks = engine_module.score_watchlist(provider=golden_watchlist)
    percentiles = [b.sector_percentile for b in blocks.values()]
    assert min(percentiles) >= 0.0
    assert max(percentiles) <= 100.0
    assert blocks["DISTRESSED"].sector_percentile < max(percentiles)


def test_end_to_end_score_block_renders(golden_watchlist):
    blocks = engine_module.score_watchlist(provider=golden_watchlist)
    text = format_score_block(blocks["IT0"])
    assert text.startswith("Company: IT0")
    assert "Cohort: C" in text
    assert "COMPOSITE SCORE" in text
    assert "RISK INDICATOR" in text
    assert "SECTOR PERCENTILE" in text
    assert "DATA COVERAGE" in text


def test_score_ticker_returns_same_result_as_full_watchlist(golden_watchlist):
    full = engine_module.score_watchlist(provider=golden_watchlist)
    single = engine_module.score_ticker("IT3", provider=golden_watchlist)
    assert single.composite_score == pytest.approx(full["IT3"].composite_score)
    assert single.cohort_size == full["IT3"].cohort_size


# --- "No data" is not a score of zero ---------------------------------------
#
# ADR 0001 clause 6. A ticker whose fetch came back empty (unlisted symbol,
# delisted, provider quota exhausted) used to fall all the way through the
# pipeline as composite 0.0 / "Very Weak" -- the same output a genuinely
# terrible company gets. These tests pin the two apart end to end.


@pytest.fixture
def watchlist_with_a_dataless_company(monkeypatch):
    """The golden cohort plus NODATA, whose fetch returned nothing at all."""
    watchlist = _cohort_c_watchlist()
    watchlist["NODATA"] = Cohort.IT_SERVICES_INFRA

    facts = _build_cohort_c_facts()
    facts["NODATA"] = RawFinancialFacts(
        ticker="NODATA",
        fetched_at=datetime.now(timezone.utc),
        missing_endpoints=["profile", "income-statement (annual)", "balance-sheet-statement (annual)"],
    )

    monkeypatch.setattr(engine_module, "load_watchlist", lambda: watchlist)
    return StubFactsProvider(facts)


def test_company_with_no_data_gets_a_no_data_state_not_a_zero_score(
    watchlist_with_a_dataless_company,
):
    block = engine_module.score_watchlist(provider=watchlist_with_a_dataless_company)["NODATA"]

    assert block.insufficient_data is True
    assert block.composite_score is None
    assert block.composite_band == "No Data"
    assert block.risk.score is None
    assert block.risk.band == "No data"
    assert block.sector_percentile is None
    assert "insufficient data" in block.warnings
    assert all(c.score is None for c in block.categories)


def test_no_data_block_is_never_reported_as_very_weak_or_high_risk(
    watchlist_with_a_dataless_company,
):
    block = engine_module.score_watchlist(provider=watchlist_with_a_dataless_company)["NODATA"]
    assert block.composite_band != "Very Weak"
    assert block.risk.band != "High risk"


def test_a_genuinely_weak_company_still_gets_a_real_number(
    watchlist_with_a_dataless_company,
):
    """The distress case must keep a real composite -- the no-data state must
    not swallow companies that simply score badly.
    """
    block = engine_module.score_watchlist(provider=watchlist_with_a_dataless_company)["DISTRESSED"]
    assert block.insufficient_data is False
    assert block.composite_score is not None
    assert block.composite_band != "No Data"
    assert block.risk.score is not None


def test_dataless_company_is_excluded_from_its_peers_percentiles(
    watchlist_with_a_dataless_company,
):
    """A company with no composite must not occupy a rank in the cohort's
    sector-percentile distribution -- at 0.0 it would have inflated everyone.
    """
    with_nodata = engine_module.score_watchlist(provider=watchlist_with_a_dataless_company)
    scored = [b for b in with_nodata.values() if not b.insufficient_data]
    assert len(scored) == 13
    assert all(b.sector_percentile is not None for b in scored)
    assert max(b.sector_percentile for b in scored) <= 100.0


def test_no_data_block_renders_without_fabricating_numbers(
    watchlist_with_a_dataless_company,
):
    text = format_score_block(engine_module.score_watchlist(provider=watchlist_with_a_dataless_company)["NODATA"])
    assert "COMPOSITE SCORE: n/a" in text
    assert "RISK INDICATOR: n/a" in text
    assert "SECTOR PERCENTILE: n/a" in text
    assert "insufficient data" in text


# --- n=1 cohorts ------------------------------------------------------------


@pytest.fixture
def single_member_cohort(monkeypatch):
    """One company alone in its effective peer group, with complete financials.

    Every metric has exactly one defined value cohort-wide, so nothing can be
    percentile-ranked. Previously each metric scored a neutral 50.0 and the
    composite came out at exactly 50.00 -- a data-coverage problem wearing the
    costume of "average performance".
    """
    watchlist = {"SOLO": Cohort.IT_SERVICES_INFRA}
    facts = {"SOLO": make_company_facts("SOLO")}

    monkeypatch.setattr(engine_module, "load_watchlist", lambda: watchlist)
    return StubFactsProvider(facts)


def test_single_member_cohort_is_not_scored_a_neutral_fifty(single_member_cohort):
    block = engine_module.score_watchlist(provider=single_member_cohort)["SOLO"]

    assert block.composite_score != pytest.approx(50.0)
    assert block.composite_score is None
    assert block.composite_band == "No Data"
    assert block.insufficient_data is True
    assert "insufficient data" in block.warnings


def test_single_member_cohort_ranks_no_metric(single_member_cohort):
    block = engine_module.score_watchlist(provider=single_member_cohort)["SOLO"]
    ranked = [m for c in block.categories for m in c.metrics if m.percentile is not None]
    assert ranked == [], "a cohort of one has nothing to rank against"
