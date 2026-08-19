"""Risk sub-score composition -- report_scoring_metadology.md Section 7.

Reuses the raw values/percentiles already computed for the Financial Health
category (net_debt_ebitda/cash_runway_months, interest_coverage/burn_multiple,
dilution_yoy come from scoring.metrics.health) plus two risk-only inputs
(altman_z, piotroski_f) that are percentile-ranked the same way as any other
metric -- see engine.py, which adds them to each ticker's metric list under
category="risk" before the shared percentile-ranking pass runs.
"""

from __future__ import annotations

from techinves.models import AltmanZone, MetricValue, RawFinancialFacts, Regime, RiskSubScore
from techinves.scoring.metrics.risk_inputs import (
    altman_z_double_prime,
    altman_zone,
    cash_runway_months,
    piotroski_f_score,
)
from techinves.scoring.metrics._helpers import latest_annual
from techinves.scoring.normalization import redistribute_weights

# Section 7 "Risk Sub-Score components". The three components below are shared
# by both regimes; the remaining 40% is regime-dependent (Section 6 metric
# substitution). Kept as three dicts rather than one combined dict so that each
# regime's weight set visibly sums to 1.0 -- a single merged dict summed to
# 1.30, which read like a bug even though no code path ever summed it.
_SHARED_RISK_WEIGHTS = {
    "altman_z": 0.30,
    "piotroski_f": 0.20,
    "dilution_yoy": 0.10,
}

#: Profitable regime: leverage and coverage. Sums to 1.0.
PROFITABLE_RISK_WEIGHTS = {
    **_SHARED_RISK_WEIGHTS,
    "net_debt_ebitda": 0.20,
    "interest_coverage": 0.20,
}

#: Unprofitable-growth regime: runway and burn substitute for leverage and
#: coverage (Section 6). Sums to 1.0.
UNPROFITABLE_GROWTH_RISK_WEIGHTS = {
    **_SHARED_RISK_WEIGHTS,
    "cash_runway_months": 0.20,
    "burn_multiple": 0.20,
}


def risk_weights_for(regime: Regime) -> dict[str, float]:
    if regime == Regime.UNPROFITABLE_GROWTH:
        return UNPROFITABLE_GROWTH_RISK_WEIGHTS
    return PROFITABLE_RISK_WEIGHTS


_RISK_BAND_THRESHOLDS = [
    (80, "Solid"),
    (60, "Adequate"),
    (40, "Worth watching"),
    (20, "Fragile"),
]

#: Band for a risk score that could not be computed at all. Distinct from
#: "High risk": a company we know nothing about is not a company we know to be
#: dangerous.
NO_DATA_RISK_BAND = "No data"


def risk_band(score: float | None) -> str:
    if score is None:
        return NO_DATA_RISK_BAND
    for threshold, band in _RISK_BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return "High risk"


def compute_risk_inputs(facts: RawFinancialFacts) -> list[MetricValue]:
    """The two risk-only metrics (not part of any of the 4 categories),
    percentile-ranked alongside the category metrics in engine.py.
    """
    period = latest_annual(facts)
    prior = latest_annual(facts, 1)

    z = altman_z_double_prime(period)
    f_score = piotroski_f_score(period, prior)

    return [
        MetricValue(
            name="altman_z", category="risk", raw_value=z, direction="higher_better",
            available=z is not None, reason_unavailable=None if z is not None else "Insufficient balance-sheet data",
            base_weight=_SHARED_RISK_WEIGHTS["altman_z"],
        ),
        MetricValue(
            name="piotroski_f", category="risk", raw_value=float(f_score) if f_score is not None else None,
            direction="higher_better", available=f_score is not None,
            reason_unavailable=None if f_score is not None else "Current or prior-year financials unavailable",
            base_weight=_SHARED_RISK_WEIGHTS["piotroski_f"],
        ),
    ]


def compute_risk_subscore(
    facts: RawFinancialFacts,
    regime: Regime,
    percentiles: dict[str, float | None],
    metrics: list[MetricValue],
) -> RiskSubScore:
    period = latest_annual(facts)
    prior = latest_annual(facts, 1)

    z = next((m.raw_value for m in metrics if m.name == "altman_z"), None)
    f_score_raw = next((m.raw_value for m in metrics if m.name == "piotroski_f"), None)
    f_score = int(f_score_raw) if f_score_raw is not None else None

    net_debt_ebitda = next((m.raw_value for m in metrics if m.name == "net_debt_ebitda"), None)
    interest_coverage = next((m.raw_value for m in metrics if m.name == "interest_coverage"), None)
    dilution_yoy = next((m.raw_value for m in metrics if m.name == "dilution_yoy"), None)
    burn_multiple = next((m.raw_value for m in metrics if m.name == "burn_multiple"), None)
    runway = next((m.raw_value for m in metrics if m.name == "cash_runway_months"), None)
    if runway is None and regime == Regime.UNPROFITABLE_GROWTH:
        runway = cash_runway_months(period)

    base_weights = risk_weights_for(regime)

    available = {name for name in base_weights if percentiles.get(name) is not None}
    weights = redistribute_weights(base_weights, available) if available else {}
    # None, not 0.0: 0.0 is the score of a company measured to be maximally
    # risky, and risk_band() would label it "High risk". A company with no
    # usable risk input has not been measured at all -> "No data".
    score = sum(weights[name] * percentiles[name] for name in available) if available else None

    return RiskSubScore(
        score=score,
        band=risk_band(score),
        altman_z=z,
        altman_zone=altman_zone(z),
        piotroski_f=f_score,
        net_debt_ebitda=net_debt_ebitda,
        interest_coverage=interest_coverage,
        cash_runway_months=runway,
        burn_multiple=burn_multiple,
        dilution_yoy_pct=dilution_yoy,
        components_used=sorted(available),
    )
