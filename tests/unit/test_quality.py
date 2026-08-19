from datetime import date

import pytest

from techinves.models import Cohort, RawFinancialFacts, Regime
from techinves.scoring.metrics.quality import compute
from tests.conftest import make_period


def _facts(period) -> RawFinancialFacts:
    from datetime import datetime, timezone

    return RawFinancialFacts(ticker="TAXCASE", annual=[period], fetched_at=datetime.now(timezone.utc))


def _roic_metric(period):
    metrics = compute(_facts(period), Regime.PROFITABLE, Cohort.SOFTWARE_INTERNET)
    return next(m for m in metrics if m.name == "roic")


def test_negative_effective_tax_rate_is_clamped_to_zero():
    """A one-off tax benefit (income_tax_expense negative relative to
    pretax income) would otherwise inflate NOPAT above operating income."""
    period = make_period(
        fiscal_date=date(2026, 12, 31),
        operating_income=1000.0,
        net_income=900.0,
        income_tax_expense=-50.0,  # pretax = 850, raw rate = -0.0588
        total_debt=500.0,
        total_stockholders_equity=1500.0,
    )
    metric = _roic_metric(period)
    # nopat = 1000 * (1 - 0) = 1000; roic = 1000 / 2000
    assert metric.raw_value == pytest.approx(0.5)


def test_high_effective_tax_rate_is_clamped_to_one_half():
    period = make_period(
        fiscal_date=date(2026, 12, 31),
        operating_income=1000.0,
        net_income=100.0,
        income_tax_expense=900.0,  # pretax = 1000, raw rate = 0.9
        total_debt=500.0,
        total_stockholders_equity=1500.0,
    )
    metric = _roic_metric(period)
    # nopat = 1000 * (1 - 0.5) = 500; roic = 500 / 2000
    assert metric.raw_value == pytest.approx(0.25)
