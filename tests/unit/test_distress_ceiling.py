from techinves.models import AltmanZone, Regime, RiskSubScore
from techinves.scoring.composite import (
    DISTRESS_CEILING,
    apply_distress_ceiling,
    trigger_altman_distress,
    trigger_cash_runway_distress,
    trigger_interest_coverage_distress,
)


def _risk(**overrides) -> RiskSubScore:
    base = dict(
        score=50.0,
        band="Worth watching",
        altman_z=3.0,
        altman_zone=AltmanZone.SAFE,
        piotroski_f=5,
        net_debt_ebitda=1.0,
        interest_coverage=5.0,
        cash_runway_months=24.0,
        burn_multiple=None,
        dilution_yoy_pct=0.02,
        components_used=["altman_z"],
    )
    base.update(overrides)
    return RiskSubScore(**base)


def test_no_trigger_when_all_healthy():
    risk = _risk()
    assert not trigger_altman_distress(risk)
    assert not trigger_interest_coverage_distress(risk, net_debt=100.0)
    assert not trigger_cash_runway_distress(risk, Regime.UNPROFITABLE_GROWTH)
    score, applied = apply_distress_ceiling(90.0, risk, Regime.PROFITABLE, net_debt=100.0)
    assert applied is False
    assert score == 90.0


def test_altman_distress_trigger():
    risk = _risk(altman_z=1.0)
    assert trigger_altman_distress(risk)
    score, applied = apply_distress_ceiling(90.0, risk, Regime.PROFITABLE, net_debt=None)
    assert applied is True
    assert score == DISTRESS_CEILING


def test_altman_at_exact_threshold_does_not_trigger():
    risk = _risk(altman_z=1.1)
    assert not trigger_altman_distress(risk)


def test_interest_coverage_trigger_requires_positive_net_debt():
    risk = _risk(interest_coverage=1.5)
    assert trigger_interest_coverage_distress(risk, net_debt=50.0)
    assert not trigger_interest_coverage_distress(risk, net_debt=-50.0)
    assert not trigger_interest_coverage_distress(risk, net_debt=None)


def test_interest_coverage_at_exact_threshold_does_not_trigger():
    risk = _risk(interest_coverage=2.0)
    assert not trigger_interest_coverage_distress(risk, net_debt=50.0)


def test_cash_runway_trigger_only_applies_in_unprofitable_regime():
    risk = _risk(cash_runway_months=6.0)
    assert trigger_cash_runway_distress(risk, Regime.UNPROFITABLE_GROWTH)
    assert not trigger_cash_runway_distress(risk, Regime.PROFITABLE)


def test_cash_runway_at_exact_threshold_does_not_trigger():
    risk = _risk(cash_runway_months=12.0)
    assert not trigger_cash_runway_distress(risk, Regime.UNPROFITABLE_GROWTH)


def test_apply_distress_ceiling_never_raises_score():
    risk = _risk(altman_z=1.0)
    score, applied = apply_distress_ceiling(50.0, risk, Regime.PROFITABLE, net_debt=None)
    assert applied is True
    assert score == 50.0  # already below ceiling, min() keeps the lower value
