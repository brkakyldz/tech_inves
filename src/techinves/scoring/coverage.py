"""Data coverage % and the "low reliability" label --
report_scoring_metadology.md Section 3 ("Data coverage indicator").
"""

from __future__ import annotations

from techinves.config import LOW_RELIABILITY_COVERAGE_THRESHOLD
from techinves.models import CategoryScore


def compute_coverage(categories: list[CategoryScore]) -> float:
    """Fraction of the four categories' metrics that were computable for
    this company's composite score (0.0-1.0).
    """
    total = sum(len(c.metrics) for c in categories)
    if total == 0:
        return 0.0
    available = sum(1 for c in categories for m in c.metrics if m.percentile is not None)
    return available / total


def is_low_reliability(coverage_pct: float) -> bool:
    return coverage_pct < LOW_RELIABILITY_COVERAGE_THRESHOLD
