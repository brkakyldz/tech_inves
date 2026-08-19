"""Single-ticker, cohort-independent inspection -- for testing the data
integration and metric computation without paying the cohort-wide API cost
that score_ticker()/score_watchlist() require (percentile ranking is
inherently cohort-wide, per report_scoring_metadology.md Section 3).

This is NOT a substitute for a real score: no percentile, composite, risk
sub-score, or coverage % is produced here, because none of those are
computable from a single company's data. It exists purely so XBRL concept
mapping, regime detection, and per-metric raw-value math can be iterated on
one company at a time -- two requests (EDGAR companyfacts + FMP profile)
instead of the cohort's ~40.
"""

from __future__ import annotations

from pydantic import BaseModel

from techinves.data.raw_facts import FactsProvider
from techinves.models import Cohort, MetricValue, RawFinancialFacts, Regime
from techinves.scoring import risk
from techinves.scoring.metrics import growth, health, quality, valuation
from techinves.scoring.regime import detect_regime
from techinves.watchlist import load_scoring_excluded, load_watchlist

_METRIC_MODULES = (valuation, growth, quality, health)


class TickerDebugReport(BaseModel):
    ticker: str
    cohort: Cohort
    regime: Regime
    facts: RawFinancialFacts
    metrics: list[MetricValue]


def debug_ticker(ticker: str, provider: FactsProvider) -> TickerDebugReport:
    """Fetches and computes raw (unnormalized) metrics for a single ticker.
    Does not touch any other watchlist member -- two requests, regardless of
    the ticker's cohort size.
    """
    # Same ordering rationale as techinves.cli._require_known_ticker: check
    # scoring_excluded before the generic membership check, or an excluded
    # ticker (which load_watchlist() deliberately drops) would be misreported
    # as "not in the watchlist" when it actually is (ADR 0005 §5).
    if ticker in load_scoring_excluded():
        raise ValueError(f"{ticker} is in the watchlist but excluded from financial scoring (ADR 0005 §5)")
    watchlist = load_watchlist()
    if ticker not in watchlist:
        raise ValueError(f"{ticker} is not in the watchlist (data/watchlist.yaml)")
    cohort = watchlist[ticker]

    facts = provider.fetch(ticker)
    regime = detect_regime(facts)

    metrics: list[MetricValue] = []
    for module in _METRIC_MODULES:
        metrics.extend(module.compute(facts, regime, cohort))
    metrics.extend(risk.compute_risk_inputs(facts))

    return TickerDebugReport(
        ticker=ticker,
        cohort=cohort,
        regime=regime,
        facts=facts,
        metrics=metrics,
    )
