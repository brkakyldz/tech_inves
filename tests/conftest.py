"""Shared test fixtures: fakes for both data sources (no network) and
synthetic RawFinancialFacts builders used across unit and golden tests.

Also gates the suites that need the optional `api` / `pipeline` extras -- see
the collection block below.
"""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from techinves.models import PeriodFacts, RawFinancialFacts


# --- optional-extra collection gate -----------------------------------------
#
# README documents `pip install -e ".[dev]"` (pytest, nothing else) as the way
# to run the suite, but three test packages import dependencies that only the
# optional `api` and `pipeline` extras install. Without this gate a dev-only
# install cannot collect *anything*: pytest aborts on ImportError before a
# single unit or golden test runs, which makes the documented install look
# broken rather than partial.
#
# So: the extras' test directories are dropped from collection when their
# dependencies are absent, and `pytest_report_header` says so out loud -- a
# silently smaller suite would be worse than the error it replaces. Installing
# `.[api-dev]` / `.[pipeline]` brings each one back automatically.


def _importable(*modules: str) -> bool:
    """True only if every named top-level module can be located.

    `find_spec` is deliberate: it resolves without executing module code, so a
    heavy optional dependency is not imported just to decide whether to collect.
    """
    return all(importlib.util.find_spec(m) is not None for m in modules)


HAS_API_EXTRA = _importable("fastapi", "httpx", "sqlalchemy", "aiosqlite", "pytest_asyncio")
HAS_PIPELINE_EXTRA = _importable("langgraph", "langchain_core", "sqlalchemy", "pytest_asyncio")

collect_ignore: list[str] = []
if not HAS_API_EXTRA:
    # `tests/unit/test_rate_limit.py` lives outside tests/api but exercises the
    # Starlette middleware, so it belongs to the same gate.
    collect_ignore += ["api", "runs", "unit/test_rate_limit.py"]
if not HAS_PIPELINE_EXTRA:
    collect_ignore += ["pipeline"]


def pytest_report_header() -> list[str]:
    missing = []
    if not HAS_API_EXTRA:
        missing.append("api (tests/api, tests/runs, tests/unit/test_rate_limit.py)")
    if not HAS_PIPELINE_EXTRA:
        missing.append("pipeline (tests/pipeline)")
    if not missing:
        return []
    return [
        "skipping suites for uninstalled extras: " + "; ".join(missing),
        'install them with: pip install -e ".[api-dev,pipeline]"',
    ]


class FakeFMPClient:
    """Drop-in replacement for FMPClient.get() backed by an in-memory dict
    keyed by (endpoint, ticker, params.get("period")). Records every call so
    tests can assert on call volume/caching behavior if needed.
    """

    def __init__(self, responses: dict[tuple[str, str, str | None], Any] | None = None) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, endpoint: str, ticker: str, params: dict[str, Any] | None = None) -> Any:
        self.calls.append((endpoint, ticker, params))
        period = (params or {}).get("period")
        return self.responses.get((endpoint, ticker, period))


class FakeEdgarClient:
    """Drop-in replacement for EdgarClient, backed by an in-memory
    {ticker: companyfacts} dict. Records calls so tests can assert that one
    ticker costs exactly one companyfacts request.
    """

    def __init__(self, company_facts: dict[str, Any] | None = None) -> None:
        self.company_facts = company_facts or {}
        self.calls: list[tuple[str, str]] = []

    def get_company_facts(self, cik: str, ticker: str) -> dict[str, Any] | None:
        self.calls.append((cik, ticker))
        return self.company_facts.get(ticker)


class StubFactsProvider:
    """Returns pre-built RawFinancialFacts, bypassing both data sources.

    Used where a test cares about the scoring pipeline rather than the
    fetch/normalize layer -- previously done by monkeypatching a module-level
    `fetch_raw_facts`, which the provider split makes unnecessary.
    """

    def __init__(self, facts_by_ticker: dict[str, RawFinancialFacts]) -> None:
        self.facts_by_ticker = facts_by_ticker
        self.fetched: list[str] = []

    def fetch(self, ticker: str) -> RawFinancialFacts:
        self.fetched.append(ticker)
        return self.facts_by_ticker[ticker]


# --- SEC EDGAR companyfacts builders ----------------------------------------
#
# These assemble the exact nesting data.sec.gov returns
# (facts -> taxonomy -> concept -> units -> [entries]) so the XBRL tests
# double as documentation of the shape edgar_facts.py parses.


def edgar_fact(
    *,
    end: str,
    val: float,
    start: str | None = None,
    filed: str = "2027-02-01",
    form: str = "10-K",
    accn: str = "0000000000-00-000000",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "end": end,
        "val": val,
        "filed": filed,
        "form": form,
        "accn": accn,
        "fy": int(end[:4]),
        "fp": "FY",
    }
    if start is not None:
        entry["start"] = start
    return entry


def company_facts(
    concepts: dict[str, list[dict[str, Any]]],
    *,
    taxonomy: str = "us-gaap",
    unit: str = "USD",
    entity_name: str = "Acme Corp",
) -> dict[str, Any]:
    return {
        "cik": 1234567,
        "entityName": entity_name,
        "facts": {
            taxonomy: {
                name: {"label": name, "units": {unit: entries}}
                for name, entries in concepts.items()
            }
        },
    }


def annual_span(year: int, *, month: int = 12, day: int = 31) -> tuple[str, str]:
    """(start, end) ISO strings for the fiscal year ending `year-month-day`."""
    end = date(year, month, day)
    start = end - timedelta(days=364)
    return start.isoformat(), end.isoformat()


# Concepts carrying a duration (income statement, cash flow) vs. an instant
# (balance sheet). Split here so the builder below tags each correctly --
# getting this wrong is the single most likely way to write a fixture that
# passes while describing XBRL that could not exist.
_DURATION_CONCEPTS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncomeLoss": "operating_income",
    "NetIncomeLoss": "net_income",
    "InterestExpenseNonoperating": "interest_expense",
    "IncomeTaxExpenseBenefit": "income_tax_expense",
    "NetCashProvidedByUsedInOperatingActivities": "operating_cash_flow",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capex_payment",
    "ShareBasedCompensation": "stock_based_compensation",
    "DepreciationDepletionAndAmortization": "depreciation_and_amortization",
}

_INSTANT_CONCEPTS = {
    "Assets": "total_assets",
    "Liabilities": "total_liabilities",
    "AssetsCurrent": "total_current_assets",
    "LiabilitiesCurrent": "total_current_liabilities",
    "RetainedEarningsAccumulatedDeficit": "retained_earnings",
    "StockholdersEquity": "total_stockholders_equity",
    "CashAndCashEquivalentsAtCarryingValue": "cash_and_equivalents",
    "DebtCurrent": "debt_current",
    "LongTermDebtNoncurrent": "long_term_debt",
}


def synthetic_company_facts(
    *,
    years: int = 5,
    latest_year: int = 2026,
    base_revenue: float = 1_000_000_000.0,
    revenue_growth: float = 0.15,
) -> dict[str, Any]:
    """A full multi-year us-gaap companyfacts document for one company.

    Deliberately built from the same growth model as make_company_facts(), so a
    test can assert that the EDGAR path produces the numbers the synthetic
    RawFinancialFacts path already encodes.

    Note `capex_payment` is stored POSITIVE, which is how XBRL actually tags it
    (`PaymentsToAcquire...` is a payment, not a signed cash-flow line). The
    normalizer is responsible for flipping it; a fixture that pre-negated it
    would hide exactly that bug.
    """
    concepts: dict[str, list[dict[str, Any]]] = {name: [] for name in _DURATION_CONCEPTS}
    concepts.update({name: [] for name in _INSTANT_CONCEPTS})

    for i in range(years):
        year = latest_year - i
        start, end = annual_span(year)
        revenue = base_revenue / ((1 + revenue_growth) ** i)
        values = {
            "revenue": revenue,
            "gross_profit": revenue * 0.65,
            "operating_income": revenue * 0.20,
            "net_income": revenue * 0.15,
            "interest_expense": 5_000_000.0,
            "income_tax_expense": revenue * 0.04,
            "operating_cash_flow": revenue * 0.205,
            "capex_payment": revenue * 0.055,
            "stock_based_compensation": revenue * 0.05,
            "depreciation_and_amortization": revenue * 0.05,
            "total_assets": 2_000_000_000.0,
            "total_liabilities": 800_000_000.0,
            "total_current_assets": 900_000_000.0,
            "total_current_liabilities": 300_000_000.0,
            "retained_earnings": 400_000_000.0,
            "total_stockholders_equity": 1_200_000_000.0,
            "cash_and_equivalents": 500_000_000.0,
            "debt_current": 50_000_000.0,
            "long_term_debt": 150_000_000.0,
        }
        filed = f"{year + 1}-02-15"
        accn = f"000000000{i}-00-000000"

        for concept, key in _DURATION_CONCEPTS.items():
            concepts[concept].append(
                edgar_fact(start=start, end=end, val=values[key], filed=filed, accn=accn)
            )
        for concept, key in _INSTANT_CONCEPTS.items():
            concepts[concept].append(
                edgar_fact(end=end, val=values[key], filed=filed, accn=accn)
            )

    return company_facts(concepts)


def make_period(
    *,
    fiscal_date: date,
    period: str = "FY",
    revenue: float | None = None,
    gross_profit: float | None = None,
    operating_income: float | None = None,
    net_income: float | None = None,
    interest_expense: float | None = None,
    income_tax_expense: float | None = None,
    weighted_avg_diluted_shares: float | None = None,
    total_assets: float | None = None,
    total_liabilities: float | None = None,
    total_current_assets: float | None = None,
    total_current_liabilities: float | None = None,
    retained_earnings: float | None = None,
    total_stockholders_equity: float | None = None,
    total_debt: float | None = None,
    cash_and_equivalents: float | None = None,
    long_term_debt: float | None = None,
    operating_cash_flow: float | None = None,
    capital_expenditure: float | None = None,
    stock_based_compensation: float | None = None,
    free_cash_flow: float | None = None,
    depreciation_and_amortization: float | None = None,
) -> PeriodFacts:
    return PeriodFacts(
        fiscal_date=fiscal_date,
        period=period,
        revenue=revenue,
        gross_profit=gross_profit,
        operating_income=operating_income,
        net_income=net_income,
        interest_expense=interest_expense,
        income_tax_expense=income_tax_expense,
        weighted_avg_diluted_shares=weighted_avg_diluted_shares,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_current_assets=total_current_assets,
        total_current_liabilities=total_current_liabilities,
        retained_earnings=retained_earnings,
        total_stockholders_equity=total_stockholders_equity,
        total_debt=total_debt,
        cash_and_equivalents=cash_and_equivalents,
        long_term_debt=long_term_debt,
        operating_cash_flow=operating_cash_flow,
        capital_expenditure=capital_expenditure,
        stock_based_compensation=stock_based_compensation,
        free_cash_flow=free_cash_flow,
        depreciation_and_amortization=depreciation_and_amortization,
    )


def make_company_facts(
    ticker: str,
    *,
    base_revenue: float = 1_000_000_000.0,
    revenue_growth: float = 0.15,
    gross_margin: float = 0.65,
    operating_margin: float = 0.20,
    total_assets: float = 2_000_000_000.0,
    total_debt: float = 200_000_000.0,
    cash: float = 500_000_000.0,
    equity: float = 1_200_000_000.0,
    liabilities: float = 800_000_000.0,
    current_assets: float = 900_000_000.0,
    current_liabilities: float = 300_000_000.0,
    retained_earnings: float = 400_000_000.0,
    sbc_pct_revenue: float = 0.05,
    fcf_margin: float = 0.15,
    diluted_shares: float = 100_000_000.0,
    dilution_growth: float = 0.01,
    years: int = 5,
    price: float = 100.0,
    interest_expense: float = 5_000_000.0,
    tax_rate: float = 0.21,
    sector: str = "Technology",
) -> RawFinancialFacts:
    """Builds a realistic-but-fabricated RawFinancialFacts for one company,
    with `years` of annual history (newest first) and 4 quarters matching
    the annual trend (so regime detection behaves consistently).
    """
    annual: list[PeriodFacts] = []
    for i in range(years):
        rev = base_revenue / ((1 + revenue_growth) ** i)
        gp = rev * gross_margin
        op_income = rev * operating_margin
        net_income = (op_income - interest_expense) * (1 - tax_rate)
        income_tax = max(net_income * tax_rate / (1 - tax_rate), 0.0) if net_income is not None else None
        sbc = rev * sbc_pct_revenue
        fcf = rev * fcf_margin
        d_and_a = rev * 0.05
        capex = -(d_and_a * 1.1)
        ocf = fcf - capex  # ocf = fcf - capex(negative) => fcf = ocf + capex
        shares = diluted_shares / ((1 + dilution_growth) ** i)

        annual.append(
            make_period(
                fiscal_date=date(2026 - i, 12, 31),
                period="FY",
                revenue=rev,
                gross_profit=gp,
                operating_income=op_income,
                net_income=net_income,
                interest_expense=interest_expense,
                income_tax_expense=income_tax,
                weighted_avg_diluted_shares=shares,
                total_assets=total_assets,
                total_liabilities=liabilities,
                total_current_assets=current_assets,
                total_current_liabilities=current_liabilities,
                retained_earnings=retained_earnings,
                total_stockholders_equity=equity,
                total_debt=total_debt,
                cash_and_equivalents=cash,
                long_term_debt=total_debt * 0.8,
                operating_cash_flow=ocf,
                capital_expenditure=capex,
                stock_based_compensation=sbc,
                free_cash_flow=fcf,
                depreciation_and_amortization=d_and_a,
            )
        )

    quarterly: list[PeriodFacts] = []
    q_dates = [date(2026, 3, 31), date(2025, 12, 31), date(2025, 9, 30), date(2025, 6, 30)]
    q_labels = ["Q1", "Q4", "Q3", "Q2"]
    for q_date, q_label in zip(q_dates, q_labels):
        q_rev = base_revenue / 4
        quarterly.append(
            make_period(
                fiscal_date=q_date,
                period=q_label,
                revenue=q_rev,
                operating_income=q_rev * operating_margin,
            )
        )

    market_cap = price * diluted_shares
    enterprise_value = market_cap + total_debt - cash

    return RawFinancialFacts(
        ticker=ticker,
        sector=sector,
        industry=sector,
        market_cap=market_cap,
        price=price,
        enterprise_value=enterprise_value,
        annual=annual,
        quarterly=quarterly,
        analyst_estimates=[],
        fetched_at=datetime.now(timezone.utc),
        missing_endpoints=[],
    )


@pytest.fixture
def fake_client() -> FakeFMPClient:
    return FakeFMPClient()
