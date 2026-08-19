"""Growth category metrics -- report_scoring_metadology.md Section 5.2.

Base regime: Revenue growth YoY (29%), 3y revenue CAGR (24%), Rule of 40
FCF-based (29%), Gross profit growth YoY (18%).

Unprofitable-growth regime (Section 6): Rule of 40 is retained; Magic Number
(net new ARR / prior-quarter S&M) is added. Magic Number requires ARR data
that no financial statement reports -- per the methodology's own Section 6
caveat, it is implemented but will be `available=False` for essentially all
companies today. That is correct behavior, not missing coverage.

Forward revenue growth was dropped from the methodology by ADR 0001 clause 4,
for the same reason as valuation's Forward P/E: it needed an analyst consensus
revenue estimate, which no free provider serves, and it was already unavailable
for all 42 watchlist companies. Its 15% is redistributed proportionally over
the survivors below.
"""

from __future__ import annotations

from techinves.models import Cohort, MetricValue, RawFinancialFacts, Regime
from techinves.scoring.metrics._helpers import cagr, latest_annual, make_metric, safe_div, yoy_growth

_BASE_WEIGHTS = {
    "revenue_growth_yoy": 0.29,
    "revenue_cagr_3y": 0.24,
    "rule_of_40_fcf": 0.29,
    "gross_profit_growth_yoy": 0.18,
}

_UNPROFITABLE_WEIGHTS = {
    "revenue_growth_yoy": 0.24,
    "revenue_cagr_3y": 0.18,
    "rule_of_40_fcf": 0.24,
    "gross_profit_growth_yoy": 0.17,
    "magic_number": 0.17,
}


def compute(facts: RawFinancialFacts, regime: Regime, cohort: Cohort) -> list[MetricValue]:
    period = latest_annual(facts)
    prior = latest_annual(facts, 1)
    earliest_3y = latest_annual(facts, 3)

    revenue = period.revenue if period else None
    prior_revenue = prior.revenue if prior else None
    rev_growth = yoy_growth(revenue, prior_revenue)

    revenue_cagr_3y = cagr(revenue, earliest_3y.revenue if earliest_3y else None, 3)

    gp_growth = yoy_growth(
        period.gross_profit if period else None, prior.gross_profit if prior else None
    )

    fcf_margin = safe_div(period.free_cash_flow if period else None, revenue)
    rule_of_40 = (rev_growth + fcf_margin) if rev_growth is not None and fcf_margin is not None else None

    weights = _UNPROFITABLE_WEIGHTS if regime == Regime.UNPROFITABLE_GROWTH else _BASE_WEIGHTS

    metrics = [
        make_metric(
            "revenue_growth_yoy", "growth", rev_growth, "higher_better", weights["revenue_growth_yoy"],
            "Current or prior-year revenue unavailable",
        ),
        make_metric(
            "revenue_cagr_3y", "growth", revenue_cagr_3y, "higher_better", weights["revenue_cagr_3y"],
            "Fewer than 4 years of annual revenue history available",
        ),
        make_metric(
            "rule_of_40_fcf", "growth", rule_of_40, "higher_better", weights["rule_of_40_fcf"],
            "Revenue growth or FCF margin unavailable",
        ),
        make_metric(
            "gross_profit_growth_yoy", "growth", gp_growth, "higher_better", weights["gross_profit_growth_yoy"],
            "Current or prior-year gross profit unavailable",
        ),
    ]

    if regime == Regime.UNPROFITABLE_GROWTH:
        metrics.append(
            make_metric(
                "magic_number", "growth", None, "higher_better", weights["magic_number"],
                "Net new ARR / S&M is not a standard financial-statement line item; not derivable from filings",
            )
        )

    return metrics
