"""Risk sub-score inputs -- report_scoring_metadology.md Section 7.

This module computes the *raw* values (Altman Z-double-prime, Piotroski
F-Score, cash runway, burn multiple) used both for the Section 9 output
line (`Altman Z'' zone`, `Piotroski F-Score: 0-9`) and, cohort-percentile-
ranked like the four main categories, for the weighted risk composite in
risk.py.

Piotroski's 9 signals each require both the current and prior annual period.
A signal is scored False (not satisfied) if either period, or the specific
field it needs, is missing -- a documented simplification rather than
returning a partial/asterisked F-score.
"""

from __future__ import annotations

from techinves.models import AltmanZone, PeriodFacts, RawFinancialFacts, Regime
from techinves.scoring.metrics._helpers import safe_div


def altman_z_double_prime(period: PeriodFacts | None) -> float | None:
    if period is None:
        return None
    if period.total_assets is None or period.total_assets == 0:
        return None
    ta = period.total_assets

    if period.total_current_assets is None or period.total_current_liabilities is None:
        return None
    working_capital = period.total_current_assets - period.total_current_liabilities

    if period.retained_earnings is None or period.operating_income is None:
        return None
    if period.total_stockholders_equity is None or period.total_liabilities is None or period.total_liabilities == 0:
        return None

    return (
        6.56 * (working_capital / ta)
        + 3.26 * (period.retained_earnings / ta)
        + 6.72 * (period.operating_income / ta)
        + 1.05 * (period.total_stockholders_equity / period.total_liabilities)
    )


def altman_zone(z: float | None) -> AltmanZone:
    if z is None:
        return AltmanZone.UNAVAILABLE
    if z > 2.6:
        return AltmanZone.SAFE
    if z >= 1.1:
        return AltmanZone.GREY
    return AltmanZone.DISTRESSED


def _gross_margin(period: PeriodFacts) -> float | None:
    return safe_div(period.gross_profit, period.revenue)


def _signal_positive_net_income(period: PeriodFacts) -> bool | None:
    if period.net_income is None:
        return None
    return period.net_income > 0


def _signal_positive_ocf(period: PeriodFacts) -> bool | None:
    if period.operating_cash_flow is None:
        return None
    return period.operating_cash_flow > 0


def _signal_roa_improved(period: PeriodFacts, prior: PeriodFacts) -> bool | None:
    roa_now = safe_div(period.net_income, period.total_assets)
    roa_prior = safe_div(prior.net_income, prior.total_assets)
    if roa_now is None or roa_prior is None:
        return None
    return roa_now > roa_prior


def _signal_earnings_quality(period: PeriodFacts) -> bool | None:
    if period.operating_cash_flow is None or period.net_income is None:
        return None
    return period.operating_cash_flow > period.net_income


def _signal_lower_leverage(period: PeriodFacts, prior: PeriodFacts) -> bool | None:
    lev_now = safe_div(period.long_term_debt, period.total_assets)
    lev_prior = safe_div(prior.long_term_debt, prior.total_assets)
    if lev_now is None or lev_prior is None:
        return None
    return lev_now < lev_prior


def _signal_higher_current_ratio(period: PeriodFacts, prior: PeriodFacts) -> bool | None:
    cr_now = safe_div(period.total_current_assets, period.total_current_liabilities)
    cr_prior = safe_div(prior.total_current_assets, prior.total_current_liabilities)
    if cr_now is None or cr_prior is None:
        return None
    return cr_now > cr_prior


def _signal_no_dilution(period: PeriodFacts, prior: PeriodFacts) -> bool | None:
    if period.weighted_avg_diluted_shares is None or prior.weighted_avg_diluted_shares is None:
        return None
    return period.weighted_avg_diluted_shares <= prior.weighted_avg_diluted_shares


def _signal_higher_gross_margin(period: PeriodFacts, prior: PeriodFacts) -> bool | None:
    gm_now = _gross_margin(period)
    gm_prior = _gross_margin(prior)
    if gm_now is None or gm_prior is None:
        return None
    return gm_now > gm_prior


def _signal_higher_asset_turnover(period: PeriodFacts, prior: PeriodFacts) -> bool | None:
    at_now = safe_div(period.revenue, period.total_assets)
    at_prior = safe_div(prior.revenue, prior.total_assets)
    if at_now is None or at_prior is None:
        return None
    return at_now > at_prior


_PIOTROSKI_MIN_SIGNALS = 6


def piotroski_f_score(period: PeriodFacts | None, prior: PeriodFacts | None) -> int | None:
    if period is None or prior is None:
        return None
    signals = [
        _signal_positive_net_income(period),
        _signal_positive_ocf(period),
        _signal_roa_improved(period, prior),
        _signal_earnings_quality(period),
        _signal_lower_leverage(period, prior),
        _signal_higher_current_ratio(period, prior),
        _signal_no_dilution(period, prior),
        _signal_higher_gross_margin(period, prior),
        _signal_higher_asset_turnover(period, prior),
    ]
    evaluated = [s for s in signals if s is not None]
    if len(evaluated) < _PIOTROSKI_MIN_SIGNALS:
        return None
    return sum(1 for s in evaluated if s)


def cash_runway_months(period: PeriodFacts | None) -> float | None:
    if period is None or period.operating_cash_flow is None or period.cash_and_equivalents is None:
        return None
    if period.operating_cash_flow >= 0:
        return None  # not burning cash this period
    monthly_burn = abs(period.operating_cash_flow) / 12
    return safe_div(period.cash_and_equivalents, monthly_burn)
