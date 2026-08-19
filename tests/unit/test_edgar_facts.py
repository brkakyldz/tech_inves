"""Tests the SEC XBRL -> PeriodFacts normalization.

These cover the three things ADR 0001 named as the real cost of moving to
EDGAR -- concept chains, cumulative quarterly figures, and restatements -- plus
the derived fields (total debt, gross profit, D&A) that us-gaap has no single
tag for. The fixtures are written in the exact shape data.sec.gov returns, so
they double as documentation of that shape.
"""

from __future__ import annotations

from datetime import date

import pytest

from techinves.data.edgar_facts import build_periods, detect_taxonomy
from tests.conftest import company_facts, edgar_fact

_REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"


class FakeFxYearSeriesClient:
    """In-memory SupportsFxYearSeries, matching tests/test_fx_rates.py's
    version. Kept local rather than moved to conftest.py so this file's file
    ownership stays exactly what R6 was scoped to touch.
    """

    def __init__(self, series_by_year: dict[int, dict[str, float]]) -> None:
        self.series_by_year = {
            year: {date.fromisoformat(d): v for d, v in series.items()}
            for year, series in series_by_year.items()
        }

    def get_year_series(self, currency: str, year: int) -> dict[date, float]:
        return dict(self.series_by_year.get(year, {}))


# --- Annual periods and concept chains --------------------------------------


def test_annual_periods_are_newest_first():
    concepts = {
        _REVENUE: [
            edgar_fact(start="2024-01-01", end="2024-12-31", val=800.0, filed="2025-02-01"),
            edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01"),
            edgar_fact(start="2025-01-01", end="2025-12-31", val=900.0, filed="2026-02-01"),
        ]
    }
    annual, _, taxonomy = build_periods(company_facts(concepts))

    assert taxonomy == "us-gaap"
    assert [p.fiscal_date.isoformat() for p in annual] == ["2026-12-31", "2025-12-31", "2024-12-31"]
    assert [p.revenue for p in annual] == [1_000.0, 900.0, 800.0]
    assert all(p.period == "FY" for p in annual)


def test_concept_chain_falls_back_per_period_not_globally():
    """ASU 606 moved tech revenue to a new tag mid-history, so the same company
    reports old years under `Revenues` and new ones under the contract-revenue
    tag. Resolution has to happen per period or the older years come back empty.
    """
    concepts = {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01")
        ],
        "Revenues": [
            edgar_fact(start="2024-01-01", end="2024-12-31", val=800.0, filed="2025-02-01")
        ],
    }
    annual, _, _ = build_periods(company_facts(concepts))

    assert {p.fiscal_date.year: p.revenue for p in annual} == {2026: 1_000.0, 2024: 800.0}


def test_the_earlier_concept_in_the_chain_wins_for_the_same_period():
    concepts = {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01")
        ],
        "Revenues": [
            edgar_fact(start="2026-01-01", end="2026-12-31", val=999.0, filed="2027-02-01")
        ],
    }
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].revenue == 1_000.0


# --- Rolling trailing-twelve-month pollution (AMZN) --------------------------


def test_a_rolling_ttm_net_income_span_does_not_manufacture_a_phantom_annual_period():
    """AMZN's 10-Qs tag a trailing-twelve-month NetIncomeLoss figure (from the
    MD&A's TTM free-cash-flow reconciliation) with a 340-400 day duration
    shape identical to a genuine annual span, but ending on a non-fiscal-
    quarter date. Discovering annual periods from `any()` driver concept
    treated every one of those as a real fiscal year, publishing a period
    with a real fiscal_date but null revenue -- which is exactly what made
    AMZN's `revenue_growth_yoy` (reading `annual[0]`) come back unavailable
    even though revenue itself was fully tagged.
    """
    concepts = {
        _REVENUE: [
            edgar_fact(start="2024-01-01", end="2024-12-31", val=1_000.0, filed="2025-02-01"),
            edgar_fact(start="2025-01-01", end="2025-12-31", val=1_100.0, filed="2026-02-01"),
        ],
        "NetIncomeLoss": [
            edgar_fact(start="2024-01-01", end="2024-12-31", val=90.0, filed="2025-02-01"),
            edgar_fact(start="2025-01-01", end="2025-12-31", val=95.0, filed="2026-02-01"),
            # Rolling TTM: ends on a quarter date that is not the fiscal year
            # end, tagged inside a 10-Q's supplementary disclosure.
            edgar_fact(
                start="2025-04-01", end="2026-03-31", val=99.0, filed="2026-04-30", form="10-Q"
            ),
        ],
    }
    annual, _, _ = build_periods(company_facts(concepts))

    assert [p.fiscal_date.isoformat() for p in annual] == ["2025-12-31", "2024-12-31"]
    assert annual[0].revenue == 1_100.0
    assert annual[0].net_income == 95.0


def test_a_rolling_ttm_operating_cash_flow_span_does_not_manufacture_a_phantom_quarter():
    concepts = {
        _REVENUE: [
            edgar_fact(start="2025-01-01", end="2025-03-31", val=100.0, filed="2025-05-01", form="10-Q"),
            edgar_fact(start="2025-01-01", end="2025-12-31", val=1_100.0, filed="2026-02-01"),
        ],
        "NetCashProvidedByUsedInOperatingActivities": [
            edgar_fact(start="2025-01-01", end="2025-03-31", val=40.0, filed="2025-05-01", form="10-Q"),
            # Rolling TTM ending mid-year, no matching revenue span -- must not
            # surface as a discovered quarter end.
            edgar_fact(
                start="2024-07-01", end="2025-06-30", val=160.0, filed="2025-08-01", form="10-Q"
            ),
        ],
    }
    _, quarterly, _ = build_periods(company_facts(concepts))

    assert [p.fiscal_date.isoformat() for p in quarterly] == ["2025-03-31"]


def test_period_discovery_falls_back_to_other_driver_concepts_when_revenue_never_resolves():
    """A filer that genuinely never tags revenue with an annual/quarter-window
    span (rare, but the original discovery logic supported it) should still
    get periods discovered from the other driver concepts -- the revenue
    anchor is a preference, not a hard requirement.
    """
    concepts = {
        "NetIncomeLoss": [
            edgar_fact(start="2024-01-01", end="2024-12-31", val=90.0, filed="2025-02-01"),
            edgar_fact(start="2025-01-01", end="2025-12-31", val=95.0, filed="2026-02-01"),
        ],
    }
    annual, _, _ = build_periods(company_facts(concepts))

    assert [p.fiscal_date.isoformat() for p in annual] == ["2025-12-31", "2024-12-31"]
    assert [p.net_income for p in annual] == [95.0, 90.0]


# --- Restatements ------------------------------------------------------------


def test_latest_filing_wins_for_a_restated_period():
    concepts = {
        _REVENUE: [
            edgar_fact(
                start="2026-01-01", end="2026-12-31", val=1_000.0,
                filed="2027-02-01", accn="0000000001-27-000001",
            ),
            edgar_fact(
                start="2026-01-01", end="2026-12-31", val=950.0,
                filed="2028-02-01", accn="0000000001-28-000001",
            ),
        ]
    }
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].revenue == 950.0


def test_non_periodic_forms_are_ignored():
    """An 8-K earnings release carries a preliminary figure that a later 10-K
    supersedes. Because dedupe is by `filed`, letting 8-Ks in would let the
    press release beat the audited statement whenever it was filed later.
    """
    concepts = {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01"),
            edgar_fact(
                start="2026-01-01", end="2026-12-31", val=1.0,
                filed="2029-01-01", form="8-K",
            ),
        ]
    }
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].revenue == 1_000.0


# --- Cumulative -> discrete quarters ----------------------------------------


def _cumulative_year() -> dict:
    """A filer that tags only year-to-date spans, as 10-Q cash-flow statements
    universally do: Q1=200, Q2=250, Q3=250, Q4=300 of a 1000 year.
    """
    return {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-03-31", val=200.0, filed="2026-05-01", form="10-Q"),
            edgar_fact(start="2026-01-01", end="2026-06-30", val=450.0, filed="2026-08-01", form="10-Q"),
            edgar_fact(start="2026-01-01", end="2026-09-30", val=700.0, filed="2026-11-01", form="10-Q"),
            edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01"),
        ]
    }


def test_quarterly_values_are_derived_from_cumulative_spans():
    _, quarterly, _ = build_periods(company_facts(_cumulative_year()))
    by_date = {p.fiscal_date.isoformat(): p.revenue for p in quarterly}

    assert by_date["2026-03-31"] == 200.0  # YTD3M is already the quarter
    assert by_date["2026-06-30"] == 250.0  # YTD6M - YTD3M
    assert by_date["2026-09-30"] == 250.0  # YTD9M - YTD6M
    assert by_date["2026-12-31"] == 300.0  # FY - YTD9M


def test_quarter_labels_follow_the_fiscal_year_end():
    _, quarterly, _ = build_periods(company_facts(_cumulative_year()))
    assert {p.fiscal_date.isoformat(): p.period for p in quarterly} == {
        "2026-03-31": "Q1",
        "2026-06-30": "Q2",
        "2026-09-30": "Q3",
        "2026-12-31": "Q4",
    }


def test_quarter_labels_follow_a_non_calendar_fiscal_year():
    """A September fiscal year end (Apple's, among others) must not be labelled
    as if the year ended in December.
    """
    concepts = {
        _REVENUE: [
            edgar_fact(start="2025-10-01", end="2026-09-30", val=1_000.0, filed="2026-11-01"),
            edgar_fact(start="2025-10-01", end="2025-12-31", val=300.0, filed="2026-02-01", form="10-Q"),
            edgar_fact(start="2025-10-01", end="2026-03-31", val=550.0, filed="2026-05-01", form="10-Q"),
            edgar_fact(start="2025-10-01", end="2026-06-30", val=780.0, filed="2026-08-01", form="10-Q"),
        ]
    }
    _, quarterly, _ = build_periods(company_facts(concepts))
    assert {p.fiscal_date.isoformat(): p.period for p in quarterly} == {
        "2025-12-31": "Q1",
        "2026-03-31": "Q2",
        "2026-06-30": "Q3",
        "2026-09-30": "Q4",
    }


def test_a_directly_tagged_three_month_span_is_used_as_is():
    """Income statements usually tag the discrete quarter alongside the YTD
    column. Differencing when the real figure is right there would introduce
    rounding noise for no reason.
    """
    concepts = {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-06-30", val=450.0, filed="2026-08-01", form="10-Q"),
            edgar_fact(start="2026-04-01", end="2026-06-30", val=249.0, filed="2026-08-01", form="10-Q"),
            edgar_fact(start="2026-01-01", end="2026-03-31", val=200.0, filed="2026-05-01", form="10-Q"),
        ]
    }
    _, quarterly, _ = build_periods(company_facts(concepts))
    q2 = next(p for p in quarterly if p.fiscal_date.isoformat() == "2026-06-30")
    # 249, the tagged value -- not 450 - 200 = 250
    assert q2.revenue == 249.0


def test_missing_nine_month_tag_does_not_produce_a_fake_six_month_quarter():
    """If the 9-month YTD fact was never filed, the quarter-end candidate list
    skips straight from the 6-month mark to the fiscal year end. Differencing
    FY against the 6-month cumulative span then produces a 6-month span, not a
    discrete quarter -- it must be rejected, not silently emitted as Q4.
    """
    concepts = {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-03-31", val=200.0, filed="2026-05-01", form="10-Q"),
            edgar_fact(start="2026-01-01", end="2026-06-30", val=450.0, filed="2026-08-01", form="10-Q"),
            # No 9-month (YTD) fact filed at all.
            edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01"),
        ]
    }
    _, quarterly, _ = build_periods(company_facts(concepts))
    by_date = {p.fiscal_date.isoformat(): p.revenue for p in quarterly}

    assert by_date.get("2026-12-31") != 550.0  # 1000 - 450, the invalid 6-month span
    assert "2026-12-31" not in by_date  # no other driver field is tagged either -> dropped entirely


def test_balance_sheet_instants_are_not_differenced():
    """Instants are stocks, not flows. Subtracting one quarter's cash from the
    next would turn a balance into a change in balance.
    """
    concepts = dict(_cumulative_year())
    concepts["CashAndCashEquivalentsAtCarryingValue"] = [
        edgar_fact(end="2026-03-31", val=500.0, filed="2026-05-01", form="10-Q"),
        edgar_fact(end="2026-06-30", val=520.0, filed="2026-08-01", form="10-Q"),
    ]
    _, quarterly, _ = build_periods(company_facts(concepts))
    by_date = {p.fiscal_date.isoformat(): p.cash_and_equivalents for p in quarterly}

    assert by_date["2026-03-31"] == 500.0
    assert by_date["2026-06-30"] == 520.0


# --- Derived fields us-gaap has no single tag for ---------------------------


def _with_annual_revenue(extra: dict) -> dict:
    concepts = {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01")
        ]
    }
    concepts.update(extra)
    return concepts


def test_total_debt_sums_current_and_noncurrent():
    concepts = _with_annual_revenue(
        {
            "DebtCurrent": [edgar_fact(end="2026-12-31", val=50.0, filed="2027-02-01")],
            "LongTermDebtNoncurrent": [edgar_fact(end="2026-12-31", val=150.0, filed="2027-02-01")],
        }
    )
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].total_debt == 200.0


def test_total_debt_sums_individual_current_tags_when_debtcurrent_is_absent():
    concepts = _with_annual_revenue(
        {
            "LongTermDebtCurrent": [edgar_fact(end="2026-12-31", val=30.0, filed="2027-02-01")],
            "CommercialPaper": [edgar_fact(end="2026-12-31", val=20.0, filed="2027-02-01")],
            "LongTermDebtNoncurrent": [edgar_fact(end="2026-12-31", val=150.0, filed="2027-02-01")],
        }
    )
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].total_debt == 200.0


def test_debtcurrent_wins_over_its_components_rather_than_double_counting():
    concepts = _with_annual_revenue(
        {
            "DebtCurrent": [edgar_fact(end="2026-12-31", val=50.0, filed="2027-02-01")],
            "LongTermDebtCurrent": [edgar_fact(end="2026-12-31", val=30.0, filed="2027-02-01")],
            "CommercialPaper": [edgar_fact(end="2026-12-31", val=20.0, filed="2027-02-01")],
            "LongTermDebtNoncurrent": [edgar_fact(end="2026-12-31", val=150.0, filed="2027-02-01")],
        }
    )
    annual, _, _ = build_periods(company_facts(concepts))
    # 50 + 150, not 50 + 30 + 20 + 150
    assert annual[0].total_debt == 200.0


def test_total_debt_falls_back_to_a_combined_tag():
    concepts = _with_annual_revenue(
        {"LongTermDebt": [edgar_fact(end="2026-12-31", val=175.0, filed="2027-02-01")]}
    )
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].total_debt == 175.0


def test_total_debt_is_none_when_nothing_is_tagged():
    """Absent debt tagging must stay None. Treating it as zero would flatter
    net debt and enterprise value for exactly the worst-covered companies.
    """
    annual, _, _ = build_periods(company_facts(_with_annual_revenue({})))
    assert annual[0].total_debt is None


def test_total_debt_is_zero_when_balance_sheet_resolves_but_no_debt_is_tagged():
    """A genuinely debt-free filer never tags any debt concept -- but its
    balance sheet still resolves. That is positive evidence of zero debt, not
    missing data (unlike test_total_debt_is_none_when_nothing_is_tagged,
    where the balance sheet itself doesn't resolve either). Regression test:
    the pre-fix behavior dropped enterprise value entirely for these filers.
    """
    concepts = _with_annual_revenue(
        {"Assets": [edgar_fact(end="2026-12-31", val=5_000.0, filed="2027-02-01")]}
    )
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].total_debt == 0.0


def test_gross_profit_falls_back_to_revenue_minus_cost_of_revenue():
    concepts = _with_annual_revenue(
        {"CostOfRevenue": [edgar_fact(start="2026-01-01", end="2026-12-31", val=350.0, filed="2027-02-01")]}
    )
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].gross_profit == 650.0


def test_depreciation_and_amortization_sums_components_when_not_combined():
    """ADR 0001 named this case: filers with no combined D&A line tag the two
    pieces separately, and EBITDA depends on getting the sum.
    """
    concepts = _with_annual_revenue(
        {
            "Depreciation": [edgar_fact(start="2026-01-01", end="2026-12-31", val=30.0, filed="2027-02-01")],
            "AmortizationOfIntangibleAssets": [
                edgar_fact(start="2026-01-01", end="2026-12-31", val=20.0, filed="2027-02-01")
            ],
        }
    )
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].depreciation_and_amortization == 50.0


def test_depreciation_and_amortization_is_none_when_only_one_component_is_tagged():
    """A partial sum (only depreciation, amortization tagged under a concept
    not in the chain) must not be treated as the complete figure -- that
    silently understates D&A and overstates ev_ebitda. Regression test for
    the pre-fix behavior, which summed whichever component resolved.
    """
    concepts = _with_annual_revenue(
        {"Depreciation": [edgar_fact(start="2026-01-01", end="2026-12-31", val=30.0, filed="2027-02-01")]}
    )
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].depreciation_and_amortization is None


def test_total_liabilities_falls_back_to_the_balance_sheet_identity():
    concepts = _with_annual_revenue(
        {
            "LiabilitiesAndStockholdersEquity": [edgar_fact(end="2026-12-31", val=2_000.0, filed="2027-02-01")],
            "StockholdersEquity": [edgar_fact(end="2026-12-31", val=1_200.0, filed="2027-02-01")],
        }
    )
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].total_liabilities == 800.0


def test_free_cash_flow_is_derived_and_capex_is_signed_as_an_outflow():
    concepts = _with_annual_revenue(
        {
            "NetCashProvidedByUsedInOperatingActivities": [
                edgar_fact(start="2026-01-01", end="2026-12-31", val=250.0, filed="2027-02-01")
            ],
            # XBRL tags this positive: it is a payment, not a signed cash-flow line
            "PaymentsToAcquirePropertyPlantAndEquipment": [
                edgar_fact(start="2026-01-01", end="2026-12-31", val=50.0, filed="2027-02-01")
            ],
        }
    )
    annual, _, _ = build_periods(company_facts(concepts))
    assert annual[0].capital_expenditure == -50.0
    assert annual[0].free_cash_flow == 200.0


# --- Taxonomy selection ------------------------------------------------------


def test_ifrs_filers_are_normalized_through_the_ifrs_chain():
    """SPOT and TSM report under ifrs-full with zero us-gaap concepts
    (ADR 0001's known-gaps section).
    """
    concepts = {
        "Revenue": [edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01", form="20-F")],
        "ProfitLoss": [edgar_fact(start="2026-01-01", end="2026-12-31", val=120.0, filed="2027-02-01", form="20-F")],
        "Assets": [edgar_fact(end="2026-12-31", val=5_000.0, filed="2027-02-01", form="20-F")],
        "CashAndCashEquivalents": [edgar_fact(end="2026-12-31", val=900.0, filed="2027-02-01", form="20-F")],
    }
    document = company_facts(concepts, taxonomy="ifrs-full")

    assert detect_taxonomy(document) == "ifrs-full"
    annual, _, taxonomy = build_periods(document)
    assert taxonomy == "ifrs-full"
    assert annual[0].revenue == 1_000.0
    assert annual[0].net_income == 120.0
    assert annual[0].total_assets == 5_000.0
    assert annual[0].cash_and_equivalents == 900.0


def test_taxonomy_is_chosen_by_weight_not_by_mere_presence():
    document = {
        "facts": {
            "us-gaap": {name: {"units": {"USD": []}} for name in ("A", "B", "C")},
            "ifrs-full": {"Revenue": {"units": {"USD": []}}},
        }
    }
    assert detect_taxonomy(document) == "us-gaap"


def test_a_company_with_no_xbrl_facts_yields_no_taxonomy():
    assert detect_taxonomy({"facts": {"ffd": {}}}) is None
    annual, quarterly, taxonomy = build_periods({"facts": {"ffd": {}}})
    assert (annual, quarterly, taxonomy) == ([], [], None)


# --- FX translation for non-USD filers (ADR 0007) ----------------------------
#
# ASML tags its statements only in EUR. These cover the two conventions ADR
# 0007 fixes (average rate for duration facts, closing rate for instant
# facts), missing-rate refusal, and the traceability record -- plus the
# regression guarantee the R6 brief called out by name: the pre-existing USD
# path (all 30 tests above) must stay byte-identical, with or without an
# fx_client supplied.


def _eur_concepts(extra: dict | None = None) -> dict:
    concepts = {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01")
        ]
    }
    concepts.update(extra or {})
    return concepts


def test_eur_facts_are_dropped_when_no_fx_client_is_supplied():
    """Unchanged pre-ADR-0007 behaviour: EUR is simply not in _ACCEPTED_UNITS,
    and passing no fx_client (the default) must reproduce exactly that --
    ASML stays insufficient_data rather than the caller having to opt out.
    """
    document = company_facts(_eur_concepts(), unit="EUR")
    annual, quarterly, taxonomy = build_periods(document)
    assert (annual, quarterly) == ([], [])
    assert taxonomy == "us-gaap"  # taxonomy detection doesn't depend on unit


def test_eur_duration_fact_is_translated_at_the_average_rate():
    fx = FakeFxYearSeriesClient(
        {2026: {"2026-01-01": 1.05, "2026-06-15": 1.15, "2026-12-31": 1.10}}
    )
    document = company_facts(_eur_concepts(), unit="EUR")
    annual, _, _ = build_periods(document, fx_client=fx)

    assert len(annual) == 1
    # mean(1.05, 1.15, 1.10) == 1.10
    assert annual[0].revenue == pytest.approx(1_000.0 * 1.10)


def test_eur_instant_fact_is_translated_at_the_closing_rate():
    fx = FakeFxYearSeriesClient(
        {2026: {"2026-01-01": 1.05, "2026-06-15": 1.15, "2026-12-31": 1.10}}
    )
    concepts = _eur_concepts({"Assets": [edgar_fact(end="2026-12-31", val=5_000.0, filed="2027-02-01")]})
    document = company_facts(concepts, unit="EUR")
    annual, _, _ = build_periods(document, fx_client=fx)

    assert annual[0].total_assets == pytest.approx(5_000.0 * 1.10)


def test_eur_period_carries_fx_traceability_metadata():
    fx = FakeFxYearSeriesClient(
        {2026: {"2026-01-01": 1.05, "2026-06-15": 1.15, "2026-12-31": 1.10}}
    )
    document = company_facts(_eur_concepts(), unit="EUR")
    annual, _, _ = build_periods(document, fx_client=fx)

    fx_record = annual[0].fx
    assert fx_record is not None
    assert fx_record.source_currency == "EUR"
    assert fx_record.rate_source == "ECB reference rate"
    assert fx_record.average_rate == pytest.approx(1.10)
    assert fx_record.closing_rate == pytest.approx(1.10)
    assert fx_record.closing_rate_date == date(2026, 12, 31)


def test_usd_periods_have_no_fx_metadata_even_when_an_fx_client_is_supplied():
    """Regression guard: a USD filer must never be routed through translation
    just because the caller happens to pass an fx_client (raw_facts.py's
    default provider passes one for every ticker, USD and EUR alike).
    """
    fx = FakeFxYearSeriesClient({2026: {"2026-06-15": 1.15}})
    document = company_facts(_eur_concepts(), unit="USD")
    annual, _, _ = build_periods(document, fx_client=fx)

    assert annual[0].revenue == 1_000.0  # untranslated
    assert annual[0].fx is None


def test_eur_instant_fact_with_no_rate_in_the_lookback_window_is_dropped_not_zeroed():
    """The revenue duration fact still translates (its year-long average
    window catches the one published point), but the balance-sheet instant
    has nothing published anywhere near its own date -- it must come back
    None, never a fabricated rate and never the raw EUR figure passed through
    as if it were USD (ADR 0007 §3).
    """
    fx = FakeFxYearSeriesClient({2026: {"2026-03-15": 1.20}})  # nothing near Dec
    concepts = _eur_concepts({"Assets": [edgar_fact(end="2026-12-31", val=5_000.0, filed="2027-02-01")]})
    document = company_facts(concepts, unit="EUR")
    annual, _, _ = build_periods(document, fx_client=fx)

    assert annual[0].revenue == pytest.approx(1_000.0 * 1.20)
    assert annual[0].total_assets is None


def test_eur_duration_fact_with_zero_observations_in_window_drops_the_period_entirely():
    """If the *only* driver concept (revenue) can't be translated at all, no
    period is discovered for it -- same as an unresolvable USD fact today.
    """
    fx = FakeFxYearSeriesClient({})  # no rates published anywhere
    document = company_facts(_eur_concepts(), unit="EUR")
    annual, quarterly, _ = build_periods(document, fx_client=fx)
    assert (annual, quarterly) == ([], [])


def test_quarterly_eur_spans_are_each_translated_at_their_own_window():
    """Q3 = YTD9M - YTD6M must translate each cumulative span at its own
    average rate before subtracting -- not translate the raw EUR difference
    at one blended rate -- or the derived discrete quarter would not equal
    what the filer's own EUR-denominated Q3 actually was.
    """
    fx = FakeFxYearSeriesClient(
        {
            2026: {
                "2026-01-01": 1.00,  # flat rate all year: makes the arithmetic easy to check by hand
                "2026-12-31": 1.00,
            }
        }
    )
    concepts = {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-06-30", val=450.0, filed="2026-08-01", form="10-Q"),
            edgar_fact(start="2026-01-01", end="2026-09-30", val=700.0, filed="2026-11-01", form="10-Q"),
            edgar_fact(start="2026-01-01", end="2026-12-31", val=1_000.0, filed="2027-02-01"),
        ]
    }
    document = company_facts(concepts, unit="EUR")
    _, quarterly, _ = build_periods(document, fx_client=fx)
    by_date = {p.fiscal_date.isoformat(): p.revenue for p in quarterly}
    # (700 - 450) * 1.00 == 250.0
    assert by_date["2026-09-30"] == pytest.approx(250.0)


# --- Reporting currency ------------------------------------------------------


def test_foreign_currency_only_filers_yield_no_monetary_facts():
    """Without an injected fx_client, a EUR-only filer (e.g. ASML) still must
    not pass raw EUR figures through as if they were USD -- that produced EV
    ratios wrong by the exchange rate while looking perfectly well-formed.
    This is the pre-ADR-0007 fallback path: as of ADR 0007, ASML resolves via
    FX translation in production because `build_periods` is called with a real
    fx_client there (see the fx-translation tests above); this test only
    covers the no-client case, which must still fail honest rather than
    silent.
    """
    concepts = {
        _REVENUE: [
            edgar_fact(start="2026-01-01", end="2026-12-31", val=32_667.0, filed="2027-02-01")
        ]
    }
    document = company_facts(concepts, unit="EUR")

    annual, quarterly, taxonomy = build_periods(document)
    assert taxonomy == "us-gaap"  # the taxonomy is still recognized...
    assert (annual, quarterly) == ([], [])  # ...but nothing is denominated usably


def test_usd_is_preferred_when_a_filer_reports_two_currencies():
    """TSM tags both TWD and USD for the same period; picking the wrong one is
    a ~30x error.
    """
    document = {
        "facts": {
            "ifrs-full": {
                "Revenue": {
                    "units": {
                        "TWD": [
                            edgar_fact(
                                start="2024-01-01", end="2024-12-31",
                                val=2_894_307_700_000.0, filed="2025-02-01", form="20-F",
                            )
                        ],
                        "USD": [
                            edgar_fact(
                                start="2024-01-01", end="2024-12-31",
                                val=88_268_000_000.0, filed="2025-02-01", form="20-F",
                            )
                        ],
                    }
                }
            }
        }
    }
    annual, _, _ = build_periods(document)
    assert annual[0].revenue == 88_268_000_000.0


def test_share_counts_are_read_from_the_shares_unit():
    """Share counts are the one non-USD unit that must still be accepted."""
    document = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            edgar_fact(start="2026-01-01", end="2026-12-31", val=100.0, filed="2027-02-01")
                        ]
                    }
                },
                "WeightedAverageNumberOfDilutedSharesOutstanding": {
                    "units": {
                        "shares": [
                            edgar_fact(
                                start="2026-01-01", end="2026-12-31",
                                val=7_453_000_000.0, filed="2027-02-01",
                            )
                        ]
                    }
                },
            }
        }
    }
    annual, _, _ = build_periods(document)
    assert annual[0].weighted_avg_diluted_shares == 7_453_000_000.0


# --- Annual-only filers ------------------------------------------------------


def test_annual_only_filers_produce_no_phantom_quarters():
    """A 20-F issuer files no 10-Qs, so its fiscal year end is the only period
    end on record. It still lands in the quarter-end candidate list, and
    emitting it would publish a quarter of Nones wearing a real fiscal date.
    """
    concepts = {
        _REVENUE: [
            edgar_fact(start="2025-01-01", end="2025-12-31", val=1_000.0, filed="2026-02-01", form="20-F"),
            edgar_fact(start="2024-01-01", end="2024-12-31", val=900.0, filed="2025-02-01", form="20-F"),
        ],
        "Assets": [
            edgar_fact(end="2025-12-31", val=5_000.0, filed="2026-02-01", form="20-F"),
        ],
    }
    annual, quarterly, _ = build_periods(company_facts(concepts))

    assert len(annual) == 2
    assert quarterly == []


def test_period_limits_are_respected():
    concepts = {
        _REVENUE: [
            edgar_fact(
                start=f"{year}-01-01", end=f"{year}-12-31", val=float(year), filed=f"{year + 1}-02-01"
            )
            for year in range(2010, 2027)
        ]
    }
    annual, _, _ = build_periods(company_facts(concepts), annual_limit=3)
    assert [p.fiscal_date.year for p in annual] == [2026, 2025, 2024]
