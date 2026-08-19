"""Top-level orchestration: score_watchlist() / score_ticker().

Ties together data fetching, regime detection, metric computation, cohort
percentile ranking, category scoring, risk sub-score, distress ceiling,
coverage, and assembles the final ScoreBlock per company. No LLM calls
anywhere in this module.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from techinves.data.raw_facts import FactsProvider, build_default_provider
from techinves.models import (
    CategoryScore,
    Cohort,
    MetricValue,
    RawFinancialFacts,
    Regime,
    ScoreBlock,
)
from techinves.scoring import cohort as cohort_mod
from techinves.scoring import composite, coverage, risk
from techinves.scoring.categories import WEIGHT_PROFILES, score_category
from techinves.scoring.metrics import growth, health, quality, valuation
from techinves.scoring.metrics._helpers import latest_annual
from techinves.scoring.normalization import compute_metric_percentiles
from techinves.scoring.regime import detect_regime
from techinves.watchlist import load_watchlist

_METRIC_MODULES = (valuation, growth, quality, health)
_CATEGORY_NAMES = ("valuation", "growth", "quality", "financial_health")


def _net_debt(facts: RawFinancialFacts) -> float | None:
    period = latest_annual(facts)
    if period is None or period.total_debt is None or period.cash_and_equivalents is None:
        return None
    return period.total_debt - period.cash_and_equivalents


def _cycle_normalization_note(facts: RawFinancialFacts, cohort: Cohort, regime: Regime) -> str | None:
    """Section 5.1: Cohort B's Forward P/E / EV/EBITDA should be earnings-
    base-normalized over a full cycle; if fewer than ~4 years of annual
    history exist, the raw metric is used and this note is attached.
    """
    if cohort != Cohort.HARDWARE_SEMI_SPACE or regime != Regime.PROFITABLE:
        return None
    if len(facts.annual) < 4:
        return "cycle normalization not applied (fewer than 4 years of annual history)"
    return None


def score_watchlist(
    tickers: list[str] | None = None,
    provider: FactsProvider | None = None,
) -> dict[str, ScoreBlock]:
    watchlist = load_watchlist()
    provider = provider or build_default_provider()

    ticker_to_group, group_extended = cohort_mod.effective_groups(watchlist)

    if tickers:
        requested = set(tickers)
        required_groups = {ticker_to_group[t] for t in requested if t in ticker_to_group}
        universe = [t for t, g in ticker_to_group.items() if g in required_groups]
    else:
        requested = None
        universe = list(watchlist.keys())

    facts_by_ticker: dict[str, RawFinancialFacts] = {}
    regime_by_ticker: dict[str, Regime] = {}
    for i, ticker in enumerate(universe, start=1):
        print(f"[{i}/{len(universe)}] fetching {ticker}...", file=sys.stderr, flush=True)
        facts = provider.fetch(ticker)
        facts_by_ticker[ticker] = facts
        regime_by_ticker[ticker] = detect_regime(facts)

    # 1. Raw metric values per ticker (4 categories + risk-only inputs).
    metrics_by_ticker: dict[str, list[MetricValue]] = {}
    for ticker in universe:
        facts = facts_by_ticker[ticker]
        regime = regime_by_ticker[ticker]
        cohort = watchlist[ticker]
        all_metrics: list[MetricValue] = []
        for module in _METRIC_MODULES:
            all_metrics.extend(module.compute(facts, regime, cohort))
        all_metrics.extend(risk.compute_risk_inputs(facts))
        metrics_by_ticker[ticker] = all_metrics

    # 2. Cohort-wide (effective-group-wide) percentile ranking per metric name.
    # Note: within a mixed-regime group, a metric name that only exists in
    # one regime's set (e.g. "forward_pe" vs "ev_sales_growth_adjusted") is
    # implicitly only ranked among tickers of that regime -- this is a
    # consequence of the regime substitution design, not a separate cohort.
    percentiles_by_ticker: dict[str, dict[str, float | None]] = {t: {} for t in universe}
    groups = {ticker_to_group[t] for t in universe}
    for group_id in groups:
        members = [t for t in universe if ticker_to_group[t] == group_id]
        metric_names: set[str] = set()
        for t in members:
            metric_names.update(m.name for m in metrics_by_ticker[t])

        for metric_name in metric_names:
            direction = None
            raw_values: dict[str, float | None] = {}
            for t in members:
                match = next((m for m in metrics_by_ticker[t] if m.name == metric_name), None)
                if match is None:
                    continue
                direction = match.direction
                raw_values[t] = match.raw_value if match.available else None
            if direction is None or not raw_values:
                continue
            ranked = compute_metric_percentiles(direction, raw_values)
            for t, pct in ranked.items():
                percentiles_by_ticker[t][metric_name] = pct

    # 3. Assemble per-ticker categories, risk, composite, coverage.
    # composites_by_ticker holds only tickers that actually produced a
    # composite; tickers with no usable metric are absent, which keeps them out
    # of their peers' sector-percentile distribution (step 4).
    composites_by_ticker: dict[str, float] = {}
    provisional: dict[str, dict] = {}
    for ticker in universe:
        cohort = watchlist[ticker]
        regime = regime_by_ticker[ticker]
        metrics = metrics_by_ticker[ticker]
        percentiles = percentiles_by_ticker[ticker]
        weight_profile = WEIGHT_PROFILES[cohort]

        categories: list[CategoryScore] = [
            score_category(name, metrics, percentiles, weight_profile[name])
            for name in _CATEGORY_NAMES
        ]

        composite_raw = composite.composite_score(categories)
        risk_subscore = risk.compute_risk_subscore(facts_by_ticker[ticker], regime, percentiles, metrics)
        net_debt = _net_debt(facts_by_ticker[ticker])
        composite_final, distress_applied = composite.apply_distress_ceiling(
            composite_raw, risk_subscore, regime, net_debt
        )

        # "No data" is one condition, evaluated once: not a single metric --
        # category or risk -- was computable for this company. That is what an
        # empty/failed fetch (delisted or unlisted ticker, exhausted provider
        # quota) looks like by the time it reaches here.
        insufficient_data = composite_final is None and risk_subscore.score is None

        if composite_final is not None:
            composites_by_ticker[ticker] = composite_final
        provisional[ticker] = {
            "cohort": cohort,
            "regime": regime,
            "categories": categories,
            "risk": risk_subscore,
            "composite": composite_final,
            "distress_applied": distress_applied,
            "insufficient_data": insufficient_data,
        }

    # 4. Sector percentile needs every ticker's final composite within the group.
    result: dict[str, ScoreBlock] = {}
    for ticker in universe:
        if requested is not None and ticker not in requested:
            continue

        group_id = ticker_to_group[ticker]
        peer_composites = {
            t: composites_by_ticker[t]
            for t in universe
            if ticker_to_group[t] == group_id and t in composites_by_ticker
        }
        pct = composite.sector_percentile(ticker, peer_composites)

        data = provisional[ticker]
        cov = coverage.compute_coverage(data["categories"])
        low_rel = coverage.is_low_reliability(cov)

        warnings: list[str] = []
        if data["insufficient_data"]:
            warnings.append("insufficient data")
        if group_extended[group_id]:
            warnings.append("extended cohort")
        if low_rel and not data["insufficient_data"]:
            warnings.append("low coverage")
        if data["distress_applied"]:
            warnings.append("distress ceiling applied")
        cycle_note = _cycle_normalization_note(facts_by_ticker[ticker], data["cohort"], data["regime"])
        if cycle_note:
            warnings.append(cycle_note)

        result[ticker] = ScoreBlock(
            ticker=ticker,
            cohort=data["cohort"],
            cohort_size=len([t for t in universe if ticker_to_group[t] == group_id]),
            extended_cohort=group_extended[group_id],
            regime=data["regime"],
            composite_score=data["composite"],
            composite_band=composite.composite_band(data["composite"]),
            distress_ceiling_applied=data["distress_applied"],
            categories=data["categories"],
            risk=data["risk"],
            sector_percentile=pct,
            sector_percentile_band=composite.sector_percentile_band(pct),
            coverage_pct=cov,
            low_reliability=low_rel,
            insufficient_data=data["insufficient_data"],
            warnings=warnings,
            generated_at=datetime.now(timezone.utc),
        )

    return result


def score_ticker(ticker: str, provider: FactsProvider | None = None) -> ScoreBlock:
    result = score_watchlist(tickers=[ticker], provider=provider)
    return result[ticker]
