"""Winsorization, direction correction, percentile ranking, and weight
redistribution -- report_scoring_metadology.md Section 3.

Order matters and mirrors the doc's own steps: winsorize raw values first
(in original units), THEN apply direction correction (negate lower-is-better
metrics so "higher percentile = always better"), THEN rank.
"""

from __future__ import annotations

from typing import Literal

import numpy as np

Direction = Literal["higher_better", "lower_better"]

#: Minimum number of cohort members with a *defined* raw value before a metric
#: can be percentile-ranked at all. With n=1 the average-rank percentile is
#: identically 50.0 for the single member -- a statistical degeneracy, not a
#: measurement. Section 3.4 forbids exactly this ("a neutral 50 is not
#: assigned"), so a sub-threshold metric is treated as *unrankable* and is
#: reported as None, which routes it into the existing unavailable-metric path
#: (`redistribute_weights`) instead of fabricating an average result.
#:
#: NOTE: the 2026-08-13 scoring review recommends raising this to 5
#: ("insufficient peers" for n<5). That is a wider behavioural change than the
#: n=1 degeneracy fix and is left as a separate decision; the threshold is
#: exposed here and as a keyword argument so it can be raised in one place.
MIN_PEERS_FOR_PERCENTILE = 2


def winsorize(values: list[float], lower_pct: float = 2.5, upper_pct: float = 97.5) -> list[float]:
    """Clips each value to the [lower_pct, upper_pct] percentile range of
    `values` itself. Operates only on already-defined values -- callers must
    exclude None/missing values before calling this (Section 3: undefined
    values are excluded, never clipped to 0).
    """
    if len(values) <= 1:
        return list(values)
    lo = float(np.percentile(values, lower_pct))
    hi = float(np.percentile(values, upper_pct))
    return [min(max(v, lo), hi) for v in values]


def percentile_rank(value: float, distribution: list[float]) -> float:
    """Average-rank percentile: (count(d < v) + 0.5*count(d == v)) / n * 100."""
    n = len(distribution)
    if n == 0:
        return 50.0
    less = sum(1 for d in distribution if d < value)
    equal = sum(1 for d in distribution if d == value)
    return (less + 0.5 * equal) / n * 100


def compute_metric_percentiles(
    direction: Direction,
    raw_values: dict[str, float | None],
    min_peers: int = MIN_PEERS_FOR_PERCENTILE,
) -> dict[str, float | None]:
    """Given one metric's raw values across a cohort ({ticker: value|None}),
    returns {ticker: percentile|None} -- None for tickers whose raw value was
    None (the metric is unavailable for them; caller redistributes weight).

    Returns all-None when fewer than `min_peers` cohort members have a defined
    value: a cohort that small cannot be normalized against, and emitting the
    degenerate 50.0 would disguise a data gap as average performance
    (Section 3.4). All-None is the same signal as "unavailable", so callers
    need no special case -- the weight is redistributed.
    """
    defined_tickers = [t for t, v in raw_values.items() if v is not None]
    if len(defined_tickers) < min_peers:
        return {t: None for t in raw_values}

    raw_defined = [raw_values[t] for t in defined_tickers]  # type: ignore[misc]
    winsorized = winsorize(raw_defined)

    corrected = [(-v if direction == "lower_better" else v) for v in winsorized]

    result: dict[str, float | None] = {t: None for t in raw_values}
    for ticker, value in zip(defined_tickers, corrected):
        result[ticker] = percentile_rank(value, corrected)
    return result


def redistribute_weights(base_weights: dict[str, float], available: set[str]) -> dict[str, float]:
    """Proportionally redistributes weight among `available` metrics only
    (Section 3.4: a metric that can't be computed is disabled, never given a
    neutral 50 -- its weight goes to the metrics that ARE computable).
    Always sums to 1.0 for any non-empty `available` subset.
    """
    total = sum(base_weights[m] for m in available if m in base_weights)
    if total <= 0:
        return {m: 0.0 for m in available}
    return {m: base_weights[m] / total for m in available if m in base_weights}
