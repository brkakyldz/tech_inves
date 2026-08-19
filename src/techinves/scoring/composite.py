"""Composite score, band, distress ceiling, and sector percentile --
report_scoring_metadology.md Section 7 (distress ceiling) and Section 9
(bands).
"""

from __future__ import annotations

from techinves.models import CategoryScore, Regime, RiskSubScore
from techinves.scoring.normalization import percentile_rank

_COMPOSITE_BAND_THRESHOLDS = [
    (80, "Strong"),
    (65, "Good"),
    (45, "Moderate"),
    (30, "Weak"),
]

_SECTOR_PERCENTILE_BAND_THRESHOLDS = [
    (80, "Top tier"),
    (60, "Upper-mid"),
    (40, "Mid"),
    (20, "Lower-mid"),
]

DISTRESS_CEILING = 70.0
DISTRESS_INTEREST_COVERAGE_THRESHOLD = 2.0
DISTRESS_ALTMAN_THRESHOLD = 1.1
DISTRESS_CASH_RUNWAY_MONTHS = 12


#: Band for a composite that could not be computed at all -- see ADR 0001
#: clause 6. Deliberately not "Very Weak".
NO_DATA_BAND = "No Data"


def composite_score(categories: list[CategoryScore]) -> float | None:
    """Weighted sum of category scores, renormalized over the categories that
    actually have a score. Returns None when no category was computable.

    A category with `score is None` had no computable metric; excluding it and
    rescaling the remaining weights is the category-level analogue of the
    Section 3.4 metric rule. Letting it through as a 0.0 contribution would
    push a data gap into the score as if it were a measured failure.
    """
    scored = [c for c in categories if c.score is not None]
    total_weight = sum(c.weight for c in scored)
    if not scored or total_weight <= 0:
        return None
    return sum(c.score * c.weight for c in scored) / total_weight


def composite_band(score: float | None) -> str:
    if score is None:
        return NO_DATA_BAND
    for threshold, band in _COMPOSITE_BAND_THRESHOLDS:
        if score >= threshold:
            return band
    return "Very Weak"


def sector_percentile_band(percentile: float | None) -> str:
    if percentile is None:
        return NO_DATA_BAND
    for threshold, band in _SECTOR_PERCENTILE_BAND_THRESHOLDS:
        if percentile >= threshold:
            return band
    return "Bottom tier"


def trigger_altman_distress(risk: RiskSubScore) -> bool:
    return risk.altman_z is not None and risk.altman_z < DISTRESS_ALTMAN_THRESHOLD


def trigger_interest_coverage_distress(risk: RiskSubScore, net_debt: float | None) -> bool:
    return (
        risk.interest_coverage is not None
        and risk.interest_coverage < DISTRESS_INTEREST_COVERAGE_THRESHOLD
        and net_debt is not None
        and net_debt > 0
    )


def trigger_cash_runway_distress(risk: RiskSubScore, regime: Regime) -> bool:
    return (
        regime == Regime.UNPROFITABLE_GROWTH
        and risk.cash_runway_months is not None
        and risk.cash_runway_months < DISTRESS_CASH_RUNWAY_MONTHS
    )


def apply_distress_ceiling(
    composite: float | None, risk: RiskSubScore, regime: Regime, net_debt: float | None
) -> tuple[float | None, bool]:
    if composite is None:
        return None, False
    triggered = any(
        [
            trigger_altman_distress(risk),
            trigger_interest_coverage_distress(risk, net_debt),
            trigger_cash_runway_distress(risk, regime),
        ]
    )
    if triggered:
        return min(composite, DISTRESS_CEILING), True
    return composite, False


def sector_percentile(ticker: str, composites: dict[str, float]) -> float | None:
    """`composites` = {ticker: composite_score} for every ticker in the same
    effective peer group that HAS a composite. Higher composite = higher
    percentile, no winsorization (Section 9 doesn't call for it at this step).

    Returns None if `ticker` has no composite of its own: an unscored company
    cannot hold a rank among its peers, and it is also excluded from everyone
    else's peer set so it doesn't distort their percentiles.
    """
    if ticker not in composites:
        return None
    values = list(composites.values())
    return percentile_rank(composites[ticker], values)
