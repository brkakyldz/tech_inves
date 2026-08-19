"""Profitability & Quality category metrics -- report_scoring_metadology.md
Section 5.3.

Base weights (Cohort A/C): ROIC 25%, gross margin level 12% + trend 8%,
GAAP operating margin 20%, earnings quality/accruals 15%, SBC-adjusted FCF
margin 12%, earnings stability 8%.

Cohort B: operating margin raised to 25% (methodology Section 4); ROIC
reduced to 20% to keep the category weights summing to 1.0 (the methodology
states the operating-margin raise but does not specify the offset -- this is
a documented judgment call, not a literal quote from the doc).

Unprofitable-growth regime (Section 6): ROIC and operating margin are
substituted out; gross margin level/trend and the *rate of operating margin
improvement* (delta, not level) take the weight. The methodology does not
give exact substitute weights, so the split below is this project's own
reasoned allocation.
"""

from __future__ import annotations

from techinves.models import Cohort, MetricValue, RawFinancialFacts, Regime
from techinves.scoring.metrics._helpers import (
    latest_annual,
    make_metric,
    safe_div,
    sbc_adjusted_fcf,
    stdev_or_none,
    yoy_growth,
)

_BASE_WEIGHTS = {
    "roic": 0.25,
    "gross_margin_level": 0.12,
    "gross_margin_trend": 0.08,
    "operating_margin": 0.20,
    "earnings_quality_accruals": 0.15,
    "sbc_fcf_margin": 0.12,
    "earnings_stability": 0.08,
}

_COHORT_B_WEIGHTS = {**_BASE_WEIGHTS, "operating_margin": 0.25, "roic": 0.20}

_UNPROFITABLE_WEIGHTS = {
    "gross_margin_level": 0.30,
    "gross_margin_trend": 0.20,
    "operating_margin_improvement_rate": 0.20,
    "earnings_quality_accruals": 0.15,
    "sbc_fcf_margin": 0.10,
    "earnings_stability": 0.05,
}

_DEFAULT_TAX_RATE = 0.21  # US federal statutory rate, used only when effective rate can't be derived


def _operating_margin(period) -> float | None:
    if period is None:
        return None
    return safe_div(period.operating_income, period.revenue)


def _gross_margin(period) -> float | None:
    if period is None:
        return None
    return safe_div(period.gross_profit, period.revenue)


def _roic(period) -> float | None:
    if period is None or period.operating_income is None:
        return None
    tax_rate = _DEFAULT_TAX_RATE
    if period.net_income is not None and period.income_tax_expense is not None:
        pretax = period.net_income + period.income_tax_expense
        if pretax > 0:
            tax_rate = period.income_tax_expense / pretax
    # A one-off tax benefit or a low-pretax-income quarter can push the
    # effective rate negative or well above the statutory range, which then
    # inflates or deflates NOPAT into a distorted ROIC. Clamp to a plausible
    # effective-tax-rate band before applying it.
    tax_rate = max(0.0, min(tax_rate, 0.5))
    nopat = period.operating_income * (1 - tax_rate)
    if period.total_debt is None or period.total_stockholders_equity is None:
        return None
    invested_capital = period.total_debt + period.total_stockholders_equity
    return safe_div(nopat, invested_capital)


def compute(facts: RawFinancialFacts, regime: Regime, cohort: Cohort) -> list[MetricValue]:
    period = latest_annual(facts)
    prior = latest_annual(facts, 1)
    earliest_3y = latest_annual(facts, 3)

    gross_margin_level = _gross_margin(period)
    gross_margin_trend = None
    if gross_margin_level is not None and earliest_3y is not None:
        earliest_margin = _gross_margin(earliest_3y)
        if earliest_margin is not None:
            gross_margin_trend = gross_margin_level - earliest_margin

    accrual_ratio = None
    if period and period.net_income is not None and period.operating_cash_flow is not None and period.total_assets:
        accrual_ratio = safe_div(period.net_income - period.operating_cash_flow, period.total_assets)

    sbc_fcf_margin = safe_div(sbc_adjusted_fcf(period), period.revenue if period else None)

    op_margins = [m for m in (_operating_margin(p) for p in facts.annual[:5]) if m is not None]
    earnings_stability = stdev_or_none(op_margins)

    if regime == Regime.UNPROFITABLE_GROWTH:
        weights = _UNPROFITABLE_WEIGHTS
        op_margin_now = _operating_margin(period)
        op_margin_prior = _operating_margin(prior)
        op_margin_improvement = yoy_growth(op_margin_now, op_margin_prior) if op_margin_prior else (
            (op_margin_now - op_margin_prior) if op_margin_now is not None and op_margin_prior is not None else None
        )
        return [
            make_metric(
                "gross_margin_level", "quality", gross_margin_level, "higher_better",
                weights["gross_margin_level"], "Revenue or gross profit unavailable",
            ),
            make_metric(
                "gross_margin_trend", "quality", gross_margin_trend, "higher_better",
                weights["gross_margin_trend"], "Fewer than 4 years of annual history available",
            ),
            make_metric(
                "operating_margin_improvement_rate", "quality", op_margin_improvement, "higher_better",
                weights["operating_margin_improvement_rate"],
                "Current or prior-year operating margin unavailable",
            ),
            make_metric(
                "earnings_quality_accruals", "quality", accrual_ratio, "lower_better",
                weights["earnings_quality_accruals"], "Net income, operating cash flow or total assets unavailable",
            ),
            make_metric(
                "sbc_fcf_margin", "quality", sbc_fcf_margin, "higher_better", weights["sbc_fcf_margin"],
                "FCF, SBC or revenue unavailable",
            ),
            make_metric(
                "earnings_stability", "quality", earnings_stability, "lower_better",
                weights["earnings_stability"], "Fewer than 2 years of operating margin history available",
            ),
        ]

    weights = _COHORT_B_WEIGHTS if cohort == Cohort.HARDWARE_SEMI_SPACE else _BASE_WEIGHTS
    roic = _roic(period)
    operating_margin = _operating_margin(period)

    return [
        make_metric("roic", "quality", roic, "higher_better", weights["roic"], "Operating income, debt or equity unavailable"),
        make_metric(
            "gross_margin_level", "quality", gross_margin_level, "higher_better",
            weights["gross_margin_level"], "Revenue or gross profit unavailable",
        ),
        make_metric(
            "gross_margin_trend", "quality", gross_margin_trend, "higher_better",
            weights["gross_margin_trend"], "Fewer than 4 years of annual history available",
        ),
        make_metric(
            "operating_margin", "quality", operating_margin, "higher_better", weights["operating_margin"],
            "Revenue or operating income unavailable",
        ),
        make_metric(
            "earnings_quality_accruals", "quality", accrual_ratio, "lower_better",
            weights["earnings_quality_accruals"], "Net income, operating cash flow or total assets unavailable",
        ),
        make_metric(
            "sbc_fcf_margin", "quality", sbc_fcf_margin, "higher_better", weights["sbc_fcf_margin"],
            "FCF, SBC or revenue unavailable",
        ),
        make_metric(
            "earnings_stability", "quality", earnings_stability, "lower_better",
            weights["earnings_stability"], "Fewer than 2 years of operating margin history available",
        ),
    ]
