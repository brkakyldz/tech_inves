"""Tests the hybrid EDGAR+FMP assembly into RawFinancialFacts (ADR 0001):
statements from SEC XBRL, price and market cap from FMP's profile, enterprise
value derived rather than fetched.
"""

from __future__ import annotations

import pytest

from techinves.data.raw_facts import HybridFactsProvider
from tests.conftest import (
    FakeEdgarClient,
    FakeFMPClient,
    company_facts,
    edgar_fact,
    synthetic_company_facts,
)

_PROFILE = [
    {"sector": "Technology", "industry": "Software", "marketCap": 3_000_000_000.0, "price": 150.0}
]

_CIK = "0001234567"


def _provider(
    *,
    ticker: str = "ACME",
    profile=_PROFILE,
    facts=None,
    cik_map=None,
) -> HybridFactsProvider:
    fmp = FakeFMPClient({("profile", ticker, None): profile} if profile is not None else {})
    edgar = FakeEdgarClient({ticker: facts} if facts is not None else {})
    return HybridFactsProvider(
        fmp, edgar, cik_map if cik_map is not None else {ticker: _CIK}
    )


def test_market_data_comes_from_fmp_and_statements_from_edgar():
    provider = _provider(facts=synthetic_company_facts())
    facts = provider.fetch("ACME")

    assert facts.ticker == "ACME"
    # profile fields use /stable's names -- `marketCap`, not v3's `mktCap`
    assert facts.sector == "Technology"
    assert facts.market_cap == 3_000_000_000.0
    assert facts.price == 150.0

    assert len(facts.annual) == 5
    latest = facts.annual[0]
    assert latest.fiscal_date.isoformat() == "2026-12-31"
    assert latest.period == "FY"
    assert latest.revenue == pytest.approx(1_000_000_000.0)
    assert latest.total_assets == 2_000_000_000.0
    assert latest.operating_cash_flow == pytest.approx(205_000_000.0)


def test_enterprise_value_is_derived_not_fetched():
    """ADR 0001 clause 1: EV = market cap + total debt - cash. Nothing on the
    free tier serves EV, and EDGAR never will -- it is a market measure.
    """
    provider = _provider(facts=synthetic_company_facts())
    facts = provider.fetch("ACME")

    # debt_current 50M + long-term 150M = 200M total debt; cash 500M
    assert facts.annual[0].total_debt == 200_000_000.0
    assert facts.enterprise_value == pytest.approx(3_000_000_000.0 + 200_000_000.0 - 500_000_000.0)


def test_enterprise_value_is_none_without_market_cap():
    """Missing market cap must not silently degrade EV to debt - cash: that
    would be a different quantity wearing the same name.
    """
    provider = _provider(profile=[{"sector": "Technology"}], facts=synthetic_company_facts())
    facts = provider.fetch("ACME")

    assert facts.market_cap is None
    assert facts.enterprise_value is None
    assert any("enterprise value" in m for m in facts.missing_endpoints)


def test_capex_is_normalized_to_a_negative_outflow():
    """XBRL tags capex as a positive payment; the whole downstream (fcf =
    ocf + capex) was written against FMP's negative convention.
    """
    provider = _provider(facts=synthetic_company_facts())
    latest = provider.fetch("ACME").annual[0]

    assert latest.capital_expenditure == pytest.approx(-55_000_000.0)
    # fcf = ocf + capex = 205M - 55M
    assert latest.free_cash_flow == pytest.approx(150_000_000.0)


def test_analyst_estimates_are_always_empty():
    """ADR 0001 clause 4 dropped the two metrics that consumed them; no free
    source provides consensus estimates.
    """
    provider = _provider(facts=synthetic_company_facts())
    assert provider.fetch("ACME").analyst_estimates == []


def test_unknown_ticker_records_a_missing_cik_and_skips_edgar():
    provider = _provider(facts=synthetic_company_facts(), cik_map={})
    facts = provider.fetch("ACME")

    assert facts.annual == []
    assert facts.quarterly == []
    assert any("edgar cik" in m for m in facts.missing_endpoints)


def test_missing_sources_are_recorded_not_raised():
    provider = _provider(profile=None, facts=None)
    facts = provider.fetch("ACME")

    assert facts.annual == []
    assert facts.market_cap is None
    assert any("fmp profile" in m for m in facts.missing_endpoints)
    assert any("edgar companyfacts" in m for m in facts.missing_endpoints)


def test_company_filing_no_recognized_taxonomy_is_reported():
    provider = _provider(facts={"cik": 1, "entityName": "X", "facts": {"ffd": {}}})
    facts = provider.fetch("ACME")

    assert facts.annual == []
    assert any("no us-gaap or ifrs-full facts" in m for m in facts.missing_endpoints)


def test_enterprise_value_prefers_the_newest_balance_sheet():
    """EV is a present-tense measure, so a fresher quarter-end balance sheet
    should beat a year-old annual one.
    """
    concepts = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            edgar_fact(start="2025-01-01", end="2025-12-31", val=1_000.0, filed="2026-02-01"),
            edgar_fact(start="2026-01-01", end="2026-03-31", val=260.0, filed="2026-05-01", form="10-Q"),
        ],
        "CashAndCashEquivalentsAtCarryingValue": [
            edgar_fact(end="2025-12-31", val=500.0, filed="2026-02-01"),
            edgar_fact(end="2026-03-31", val=100.0, filed="2026-05-01", form="10-Q"),
        ],
        "DebtCurrent": [
            edgar_fact(end="2025-12-31", val=200.0, filed="2026-02-01"),
            edgar_fact(end="2026-03-31", val=900.0, filed="2026-05-01", form="10-Q"),
        ],
    }
    provider = _provider(
        profile=[{"marketCap": 10_000.0, "price": 1.0}], facts=company_facts(concepts)
    )
    facts = provider.fetch("ACME")

    # newest balance sheet is 2026-03-31: 10000 + 900 - 100
    assert facts.enterprise_value == pytest.approx(10_800.0)
