"""Shared helpers for the per-category metric modules."""

from __future__ import annotations

import statistics

from techinves.models import CategoryName, Direction, MetricValue, PeriodFacts, RawFinancialFacts


def latest_annual(facts: RawFinancialFacts, n: int = 0) -> PeriodFacts | None:
    """The n-th most recent annual period (0 = most recent)."""
    if len(facts.annual) > n:
        return facts.annual[n]
    return None


def yoy_growth(current: float | None, prior: float | None) -> float | None:
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior)


def cagr(latest: float | None, earliest: float | None, years: int) -> float | None:
    if latest is None or earliest is None or earliest <= 0 or latest <= 0 or years <= 0:
        return None
    return (latest / earliest) ** (1 / years) - 1


def safe_div(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def ebitda(period: PeriodFacts | None) -> float | None:
    if period is None or period.operating_income is None or period.depreciation_and_amortization is None:
        return None
    return period.operating_income + period.depreciation_and_amortization


def sbc_adjusted_fcf(period: PeriodFacts | None) -> float | None:
    if period is None or period.free_cash_flow is None or period.stock_based_compensation is None:
        return None
    return period.free_cash_flow - period.stock_based_compensation


def stdev_or_none(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None
    return statistics.pstdev(clean)


def make_metric(
    name: str,
    category: CategoryName,
    raw_value: float | None,
    direction: Direction,
    weight: float,
    reason_if_missing: str,
) -> MetricValue:
    return MetricValue(
        name=name,
        category=category,
        raw_value=raw_value,
        direction=direction,
        available=raw_value is not None,
        reason_unavailable=None if raw_value is not None else reason_if_missing,
        base_weight=weight,
    )
