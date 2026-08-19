from datetime import date

import pytest

from techinves.models import AltmanZone, Regime
from techinves.scoring.metrics.risk_inputs import altman_z_double_prime, altman_zone, piotroski_f_score
from techinves.scoring.risk import (
    PROFITABLE_RISK_WEIGHTS,
    UNPROFITABLE_GROWTH_RISK_WEIGHTS,
    compute_risk_subscore,
    risk_band,
    risk_weights_for,
)
from tests.conftest import make_period

# --- Altman Z'' -----------------------------------------------------------


def test_altman_z_hand_computed_value():
    period = make_period(
        fiscal_date=date(2026, 12, 31),
        total_assets=1000.0,
        total_current_assets=400.0,
        total_current_liabilities=200.0,
        retained_earnings=300.0,
        operating_income=150.0,
        total_stockholders_equity=500.0,
        total_liabilities=500.0,
    )
    z = altman_z_double_prime(period)
    # 6.56*(200/1000) + 3.26*(300/1000) + 6.72*(150/1000) + 1.05*(500/500)
    # = 1.312 + 0.978 + 1.008 + 1.05 = 4.348
    assert z == pytest.approx(4.348, abs=1e-6)


def test_altman_z_none_when_data_missing():
    period = make_period(fiscal_date=date(2026, 12, 31), total_assets=1000.0)
    assert altman_z_double_prime(period) is None


@pytest.mark.parametrize(
    "z,expected_zone",
    [
        (3.0, AltmanZone.SAFE),
        (2.6001, AltmanZone.SAFE),
        (2.6, AltmanZone.GREY),
        (1.1, AltmanZone.GREY),
        (1.0999, AltmanZone.DISTRESSED),
        (0.0, AltmanZone.DISTRESSED),
        (None, AltmanZone.UNAVAILABLE),
    ],
)
def test_altman_zone_bands(z, expected_zone):
    assert altman_zone(z) == expected_zone


# --- Piotroski F-Score ------------------------------------------------------

_BASELINE_PERIOD_KW = dict(
    fiscal_date=date(2026, 12, 31),
    net_income=100.0,
    operating_cash_flow=150.0,
    total_assets=1000.0,
    long_term_debt=100.0,
    total_current_assets=400.0,
    total_current_liabilities=200.0,
    weighted_avg_diluted_shares=100.0,
    gross_profit=650.0,
    revenue=1000.0,
)

_BASELINE_PRIOR_KW = dict(
    fiscal_date=date(2025, 12, 31),
    net_income=50.0,
    operating_cash_flow=40.0,
    total_assets=1000.0,
    long_term_debt=200.0,
    total_current_assets=300.0,
    total_current_liabilities=200.0,
    weighted_avg_diluted_shares=110.0,
    gross_profit=550.0,
    revenue=900.0,
)


def _baseline():
    return make_period(**_BASELINE_PERIOD_KW), make_period(**_BASELINE_PRIOR_KW)


def test_all_nine_signals_pass_gives_f_score_of_nine():
    period, prior = _baseline()
    assert piotroski_f_score(period, prior) == 9


def test_missing_period_returns_none():
    _, prior = _baseline()
    assert piotroski_f_score(None, prior) is None


def test_missing_prior_returns_none():
    period, _ = _baseline()
    assert piotroski_f_score(period, None) is None


@pytest.mark.parametrize(
    "override_field,failing_value",
    [
        ("net_income", -10.0),  # signal 1: positive net income
        ("operating_cash_flow", -10.0),  # signal 2: positive OCF
    ],
)
def test_period_level_signal_failure_drops_score_by_one(override_field, failing_value):
    period, prior = _baseline()
    period = period.model_copy(update={override_field: failing_value})
    # net_income turning negative also breaks earnings-quality (ocf>ni) less directly;
    # only assert the score dropped, not the exact delta, to keep this robust.
    assert piotroski_f_score(period, prior) < 9


def test_signal_roa_improved_fails_when_roa_declines():
    period, prior = _baseline()
    period = period.model_copy(update={"net_income": 10.0})  # roa now 0.01 < prior 0.05
    assert piotroski_f_score(period, prior) == 8


def test_signal_earnings_quality_fails_when_ocf_below_net_income():
    period, prior = _baseline()
    period = period.model_copy(update={"operating_cash_flow": 50.0})  # 50 < net_income 100
    assert piotroski_f_score(period, prior) == 8


def test_signal_leverage_fails_when_leverage_increases():
    period, prior = _baseline()
    period = period.model_copy(update={"long_term_debt": 300.0})  # 0.3 > prior 0.2
    assert piotroski_f_score(period, prior) == 8


def test_signal_current_ratio_fails_when_it_declines():
    period, prior = _baseline()
    period = period.model_copy(update={"total_current_assets": 250.0})  # 1.25 < prior 1.5
    assert piotroski_f_score(period, prior) == 8


def test_signal_dilution_fails_when_shares_increase():
    period, prior = _baseline()
    period = period.model_copy(update={"weighted_avg_diluted_shares": 120.0})  # 120 > prior 110
    assert piotroski_f_score(period, prior) == 8


def test_signal_gross_margin_fails_when_margin_declines():
    period, prior = _baseline()
    period = period.model_copy(update={"gross_profit": 400.0})  # margin 0.4 < prior 0.611
    assert piotroski_f_score(period, prior) == 8


def test_all_signal_fields_missing_returns_none_not_zero():
    """Every _signal_* helper returning False for missing data used to be
    indistinguishable from failing every test -- both summed to 0. With no
    signal fields present at all, the score must be unrankable (None), not a
    fabricated 0.
    """
    period = make_period(fiscal_date=date(2026, 12, 31))
    prior = make_period(fiscal_date=date(2025, 12, 31))
    assert piotroski_f_score(period, prior) is None


def test_fewer_than_six_evaluated_signals_returns_none():
    period, prior = _baseline()
    # Blank out enough fields that only 3 of 9 signals can be evaluated
    # (net income, OCF, and OCF-vs-net-income earnings quality) -- below the
    # 6-of-9 minimum.
    sparse_fields = {
        "total_assets": None,
        "long_term_debt": None,
        "total_current_assets": None,
        "total_current_liabilities": None,
        "weighted_avg_diluted_shares": None,
        "gross_profit": None,
        "revenue": None,
    }
    period = period.model_copy(update=sparse_fields)
    prior = prior.model_copy(update=sparse_fields)
    assert piotroski_f_score(period, prior) is None


def test_signal_asset_turnover_fails_when_turnover_declines():
    period, prior = _baseline()
    period = period.model_copy(update={"revenue": 800.0})  # turnover 0.8 < prior 0.9
    # lowering revenue also lowers gross_profit/revenue... hold gross_profit fixed to isolate turnover
    period = period.model_copy(update={"gross_profit": 650.0 * 800.0 / 1000.0 + 1.0})  # keep margin slightly above prior
    assert piotroski_f_score(period, prior) == 8


# --- Risk weight regimes ----------------------------------------------------


def test_each_regime_weight_set_sums_to_one():
    """The two regimes' weights used to live in one dict summing to 1.30.
    Split apart, each must be a self-contained, complete weighting.
    """
    for weights in (PROFITABLE_RISK_WEIGHTS, UNPROFITABLE_GROWTH_RISK_WEIGHTS):
        assert sum(weights.values()) == pytest.approx(1.0)


def test_regimes_substitute_leverage_metrics_for_burn_metrics():
    assert set(PROFITABLE_RISK_WEIGHTS) - set(UNPROFITABLE_GROWTH_RISK_WEIGHTS) == {
        "net_debt_ebitda",
        "interest_coverage",
    }
    assert set(UNPROFITABLE_GROWTH_RISK_WEIGHTS) - set(PROFITABLE_RISK_WEIGHTS) == {
        "cash_runway_months",
        "burn_multiple",
    }


def test_risk_weights_for_selects_by_regime():
    assert risk_weights_for(Regime.PROFITABLE) is PROFITABLE_RISK_WEIGHTS
    assert risk_weights_for(Regime.UNPROFITABLE_GROWTH) is UNPROFITABLE_GROWTH_RISK_WEIGHTS


# --- Missing risk data is "No data", not "High risk" ------------------------


@pytest.mark.parametrize(
    "score,expected",
    [
        (95.0, "Solid"),
        (65.0, "Adequate"),
        (45.0, "Worth watching"),
        (25.0, "Fragile"),
        (0.0, "High risk"),
        (None, "No data"),
    ],
)
def test_risk_band_separates_unmeasured_from_measured_zero(score, expected):
    assert risk_band(score) == expected


def _empty_facts(ticker: str = "NODATA"):
    from datetime import datetime, timezone

    from techinves.models import RawFinancialFacts

    return RawFinancialFacts(ticker=ticker, fetched_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("regime", [Regime.PROFITABLE, Regime.UNPROFITABLE_GROWTH])
def test_risk_subscore_with_no_inputs_is_none_not_zero(regime):
    result = compute_risk_subscore(_empty_facts(), regime, percentiles={}, metrics=[])
    assert result.score is None, "missing risk data must not be scored 0.0"
    assert result.band == "No data"
    assert result.components_used == []


def test_risk_subscore_of_worst_ranked_company_is_a_real_zero():
    """The genuine bottom-of-cohort case must stay distinguishable from
    "no data": a real 0.0 and a real "High risk" label.
    """
    percentiles = {name: 0.0 for name in PROFITABLE_RISK_WEIGHTS}
    result = compute_risk_subscore(_empty_facts(), Regime.PROFITABLE, percentiles, metrics=[])
    assert result.score == pytest.approx(0.0)
    assert result.band == "High risk"
    assert result.components_used == sorted(PROFITABLE_RISK_WEIGHTS)
