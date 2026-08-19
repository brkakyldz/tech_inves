"""Financial Health category metrics -- report_scoring_metadology.md
Section 5.4.

Base weights: Net Debt/EBITDA 30%, Interest Coverage 25%, FCF/Total Debt 20%,
YoY diluted share count change 15%, Current ratio 10%.

Note: the methodology's "Current / Quick ratio" row is implemented here as
Current ratio only -- FMP's free-tier statements don't reliably break out
inventory for every company, and quick ratio's weight (10%) is already the
smallest in the category, so this is a documented simplification rather than
a silent gap.

Unprofitable-growth regime (Section 6): Net Debt/EBITDA and Interest Coverage
(meaningless with negative EBITDA) are replaced by Cash Runway and Burn
Multiple. Burn Multiple requires ARR data unavailable from FMP at any tier
and will be `available=False` for essentially all companies -- expected, per
Section 6's own caveat.
"""

from __future__ import annotations

from techinves.models import Cohort, MetricValue, RawFinancialFacts, Regime
from techinves.scoring.metrics._helpers import ebitda, latest_annual, make_metric, safe_div, yoy_growth

_BASE_WEIGHTS = {
    "net_debt_ebitda": 0.30,
    "interest_coverage": 0.25,
    "fcf_total_debt": 0.20,
    "dilution_yoy": 0.15,
    "current_ratio": 0.10,
}

_UNPROFITABLE_WEIGHTS = {
    "cash_runway_months": 0.35,
    "burn_multiple": 0.20,
    "fcf_total_debt": 0.20,
    "dilution_yoy": 0.15,
    "current_ratio": 0.10,
}

# fcf_total_debt and interest_coverage are both higher-is-better ratios with
# total debt / interest expense as the denominator. `safe_div` returns None
# on a zero denominator, which routes a genuinely debt-free company (best
# case, not a data gap) into "unavailable" alongside companies with no debt
# figure at all. A sentinel far above any realistic ratio ties a debt-free
# company for best-in-class after winsorization (normalization.py clips it
# down to the cohort's own 97.5th percentile) without requiring a special
# case in the percentile-ranking code itself.
_DEBT_FREE_SENTINEL = 1e6


def compute(facts: RawFinancialFacts, regime: Regime, cohort: Cohort) -> list[MetricValue]:
    period = latest_annual(facts)
    prior = latest_annual(facts, 1)

    net_debt = None
    if period and period.total_debt is not None and period.cash_and_equivalents is not None:
        net_debt = period.total_debt - period.cash_and_equivalents

    if period and period.total_debt == 0 and period.free_cash_flow is not None:
        fcf_total_debt = _DEBT_FREE_SENTINEL
    else:
        fcf_total_debt = safe_div(period.free_cash_flow if period else None, period.total_debt if period else None)

    dilution_yoy = yoy_growth(
        period.weighted_avg_diluted_shares if period else None,
        prior.weighted_avg_diluted_shares if prior else None,
    )

    current_ratio = safe_div(
        period.total_current_assets if period else None,
        period.total_current_liabilities if period else None,
    )

    if regime == Regime.UNPROFITABLE_GROWTH:
        cash_runway_months = None
        if period and period.operating_cash_flow is not None and period.operating_cash_flow < 0 and period.cash_and_equivalents is not None:
            monthly_burn = abs(period.operating_cash_flow) / 12
            cash_runway_months = safe_div(period.cash_and_equivalents, monthly_burn)

        return [
            make_metric(
                "cash_runway_months", "financial_health", cash_runway_months, "higher_better",
                _UNPROFITABLE_WEIGHTS["cash_runway_months"],
                "No cash burn this period, or cash/operating cash flow unavailable",
            ),
            make_metric(
                "burn_multiple", "financial_health", None, "lower_better",
                _UNPROFITABLE_WEIGHTS["burn_multiple"],
                "Net new ARR is not a standard financial-statement line item; not derivable from FMP",
            ),
            make_metric(
                "fcf_total_debt", "financial_health", fcf_total_debt, "higher_better",
                _UNPROFITABLE_WEIGHTS["fcf_total_debt"], "FCF or total debt unavailable",
            ),
            make_metric(
                "dilution_yoy", "financial_health", dilution_yoy, "lower_better",
                _UNPROFITABLE_WEIGHTS["dilution_yoy"], "Current or prior-year diluted share count unavailable",
            ),
            make_metric(
                "current_ratio", "financial_health", current_ratio, "higher_better",
                _UNPROFITABLE_WEIGHTS["current_ratio"], "Current assets or current liabilities unavailable",
            ),
        ]

    net_debt_ebitda = safe_div(net_debt, ebitda(period))
    if period and period.interest_expense == 0 and period.operating_income is not None:
        interest_coverage = _DEBT_FREE_SENTINEL
    else:
        interest_coverage = safe_div(
            period.operating_income if period else None,
            period.interest_expense if period and period.interest_expense else None,
        )

    return [
        make_metric(
            "net_debt_ebitda", "financial_health", net_debt_ebitda, "lower_better",
            _BASE_WEIGHTS["net_debt_ebitda"], "Net debt or EBITDA unavailable",
        ),
        make_metric(
            "interest_coverage", "financial_health", interest_coverage, "higher_better",
            _BASE_WEIGHTS["interest_coverage"], "Operating income or interest expense unavailable",
        ),
        make_metric(
            "fcf_total_debt", "financial_health", fcf_total_debt, "higher_better",
            _BASE_WEIGHTS["fcf_total_debt"], "FCF or total debt unavailable",
        ),
        make_metric(
            "dilution_yoy", "financial_health", dilution_yoy, "lower_better",
            _BASE_WEIGHTS["dilution_yoy"], "Current or prior-year diluted share count unavailable",
        ),
        make_metric(
            "current_ratio", "financial_health", current_ratio, "higher_better",
            _BASE_WEIGHTS["current_ratio"], "Current assets or current liabilities unavailable",
        ),
    ]
