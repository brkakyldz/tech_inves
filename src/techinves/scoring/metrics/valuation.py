"""Valuation category metrics -- report_scoring_metadology.md Section 5.1.

Base regime: EV/EBITDA (33%), EV/Sales (27%), SBC-adjusted FCF Yield (27%),
Earnings Yield (13%).

Unprofitable-growth regime (Section 6): EV/EBITDA is substituted out; weight
shifts to a growth-adjusted EV/Sales and the SBC-adjusted FCF yield.

Forward P/E was dropped from the methodology by ADR 0001 clause 4. It needed an
analyst consensus EPS, which no free provider serves -- it was already
unavailable for all 42 watchlist companies, so the weights below are the
Section 5.1 base weights with its 25% redistributed proportionally over the
survivors. Removing it is not a loss of coverage; it is the removal of a metric
that never once contributed a percentile.
"""

from __future__ import annotations

from techinves.models import Cohort, MetricValue, RawFinancialFacts, Regime
from techinves.scoring.metrics._helpers import (
    ebitda,
    latest_annual,
    make_metric,
    safe_div,
    sbc_adjusted_fcf,
    yoy_growth,
)

_BASE_WEIGHTS = {
    "ev_ebitda": 0.33,
    "ev_sales": 0.27,
    "sbc_fcf_yield": 0.27,
    "earnings_yield": 0.13,
}

_UNPROFITABLE_WEIGHTS = {
    "ev_sales_growth_adjusted": 0.45,
    "sbc_fcf_yield": 0.35,
    "earnings_yield": 0.20,
}


def compute(facts: RawFinancialFacts, regime: Regime, cohort: Cohort) -> list[MetricValue]:
    period = latest_annual(facts)
    prior = latest_annual(facts, 1)

    revenue = period.revenue if period else None
    rev_growth = yoy_growth(revenue, prior.revenue if prior else None)

    ev = facts.enterprise_value
    ev_sales = safe_div(ev, revenue)
    ev_ebitda = safe_div(ev, ebitda(period))

    sbc_fcf_yield = safe_div(sbc_adjusted_fcf(period), ev)
    earnings_yield = safe_div(period.operating_income if period else None, ev)

    if regime == Regime.UNPROFITABLE_GROWTH:
        ev_sales_growth_adj = (
            safe_div(ev_sales, rev_growth) if rev_growth is not None and rev_growth > 0 else None
        )
        return [
            make_metric(
                "ev_sales_growth_adjusted", "valuation", ev_sales_growth_adj, "lower_better",
                _UNPROFITABLE_WEIGHTS["ev_sales_growth_adjusted"],
                "EV/Sales or positive revenue growth unavailable",
            ),
            make_metric(
                "sbc_fcf_yield", "valuation", sbc_fcf_yield, "higher_better",
                _UNPROFITABLE_WEIGHTS["sbc_fcf_yield"], "FCF, SBC or EV unavailable",
            ),
            make_metric(
                "earnings_yield", "valuation", earnings_yield, "higher_better",
                _UNPROFITABLE_WEIGHTS["earnings_yield"], "EBIT or EV unavailable",
            ),
        ]

    return [
        make_metric(
            "ev_ebitda", "valuation", ev_ebitda, "lower_better", _BASE_WEIGHTS["ev_ebitda"],
            "EBITDA or EV unavailable",
        ),
        make_metric(
            "ev_sales", "valuation", ev_sales, "lower_better", _BASE_WEIGHTS["ev_sales"],
            "Revenue or EV unavailable",
        ),
        make_metric(
            "sbc_fcf_yield", "valuation", sbc_fcf_yield, "higher_better", _BASE_WEIGHTS["sbc_fcf_yield"],
            "FCF, SBC or EV unavailable",
        ),
        make_metric(
            "earnings_yield", "valuation", earnings_yield, "higher_better", _BASE_WEIGHTS["earnings_yield"],
            "EBIT or EV unavailable",
        ),
    ]
