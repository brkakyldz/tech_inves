from datetime import date

from techinves.models import Regime
from techinves.scoring.regime import detect_regime
from tests.conftest import make_company_facts, make_period


_Q_DATES = [date(2026, 3, 31), date(2025, 12, 31), date(2025, 9, 30), date(2025, 6, 30)]


def _facts_with_quarterly_op_income(values: list[float | None]) -> object:
    facts = make_company_facts("TEST", years=1)
    quarterly = [
        make_period(fiscal_date=_Q_DATES[i], period="Q1", operating_income=v)
        for i, v in enumerate(values)
    ]
    facts.quarterly = quarterly
    return facts


def test_four_of_four_negative_quarters_is_unprofitable_growth():
    facts = _facts_with_quarterly_op_income([-10.0, -5.0, -1.0, -2.0])
    assert detect_regime(facts) == Regime.UNPROFITABLE_GROWTH


def test_three_of_four_negative_quarters_is_profitable():
    facts = _facts_with_quarterly_op_income([-10.0, -5.0, -1.0, 2.0])
    assert detect_regime(facts) == Regime.PROFITABLE


def test_four_of_four_positive_quarters_is_profitable():
    facts = _facts_with_quarterly_op_income([10.0, 5.0, 1.0, 2.0])
    assert detect_regime(facts) == Regime.PROFITABLE


def test_falls_back_to_annual_when_fewer_than_four_quarters():
    facts = make_company_facts("TEST", years=1, operating_margin=-0.10)
    facts.quarterly = []
    assert detect_regime(facts) == Regime.UNPROFITABLE_GROWTH


def test_defaults_to_profitable_when_no_data_at_all():
    facts = make_company_facts("TEST", years=1)
    facts.quarterly = []
    facts.annual = []
    assert detect_regime(facts) == Regime.PROFITABLE
