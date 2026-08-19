from datetime import date

from techinves.models import Cohort, RawFinancialFacts, Regime
from techinves.scoring.metrics.health import compute
from tests.conftest import make_period


def _facts(period) -> RawFinancialFacts:
    from datetime import datetime, timezone

    return RawFinancialFacts(ticker="DEBTFREE", annual=[period], fetched_at=datetime.now(timezone.utc))


def _metric(metrics, name):
    return next(m for m in metrics if m.name == name)


def test_debt_free_company_ranks_fcf_total_debt_as_available_not_missing():
    period = make_period(
        fiscal_date=date(2026, 12, 31),
        total_debt=0.0,
        free_cash_flow=100.0,
    )
    metrics = compute(_facts(period), Regime.PROFITABLE, Cohort.SOFTWARE_INTERNET)
    m = _metric(metrics, "fcf_total_debt")
    assert m.available is True
    assert m.raw_value is not None and m.raw_value > 0


def test_debt_free_company_ranks_interest_coverage_as_available_not_missing():
    period = make_period(
        fiscal_date=date(2026, 12, 31),
        interest_expense=0.0,
        operating_income=200.0,
    )
    metrics = compute(_facts(period), Regime.PROFITABLE, Cohort.SOFTWARE_INTERNET)
    m = _metric(metrics, "interest_coverage")
    assert m.available is True
    assert m.raw_value is not None and m.raw_value > 0


def test_missing_debt_figure_still_unavailable_not_debt_free():
    """total_debt=None (unknown) must stay unavailable -- only a confirmed
    total_debt=0.0 is treated as debt-free."""
    period = make_period(fiscal_date=date(2026, 12, 31), free_cash_flow=100.0)
    metrics = compute(_facts(period), Regime.PROFITABLE, Cohort.SOFTWARE_INTERNET)
    m = _metric(metrics, "fcf_total_debt")
    assert m.available is False
