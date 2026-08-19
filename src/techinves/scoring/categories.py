"""Per-category weighted scoring -- report_scoring_metadology.md Section 3.5
and Section 4 (cohort weight profiles).
"""

from __future__ import annotations

from techinves.models import CategoryName, CategoryScore, Cohort, MetricPercentile, MetricValue
from techinves.scoring.normalization import redistribute_weights

# Section 4: "Weight profile by cohort" table.
WEIGHT_PROFILES: dict[Cohort, dict[CategoryName, float]] = {
    Cohort.SOFTWARE_INTERNET: {
        "quality": 0.30,
        "growth": 0.30,
        "valuation": 0.20,
        "financial_health": 0.20,
    },
    Cohort.HARDWARE_SEMI_SPACE: {
        "quality": 0.35,
        "growth": 0.20,
        "valuation": 0.25,
        "financial_health": 0.20,
    },
    Cohort.IT_SERVICES_INFRA: {
        "quality": 0.30,
        "growth": 0.25,
        "valuation": 0.25,
        "financial_health": 0.20,
    },
}


def score_category(
    category: CategoryName,
    metrics: list[MetricValue],
    percentiles: dict[str, float | None],
    cohort_weight: float,
) -> CategoryScore:
    """`metrics` is this ticker's full metric list (any category); this
    function filters to `category` and computes the weighted score using
    within-category weight redistribution for unavailable metrics
    (Section 3.4).

    Returns `score=None` -- not 0.0 -- when no metric in the category was
    computable. 0.0 is a legitimate score (bottom of the cohort on every
    metric); the absence of data is a different fact and gets a different
    value. Callers must exclude None-scored categories from the composite
    rather than letting them contribute a zero.
    """
    in_category = [m for m in metrics if m.category == category]
    base_weights = {m.name: m.base_weight for m in in_category}
    available = {m.name for m in in_category if percentiles.get(m.name) is not None}

    redistributed = redistribute_weights(base_weights, available) if available else {}

    score = (
        sum(redistributed[name] * percentiles[name] for name in available) if available else None
    )

    metric_percentiles = [
        MetricPercentile(
            name=m.name,
            raw_value=m.raw_value,
            percentile=percentiles.get(m.name),
            weight_used=redistributed.get(m.name, 0.0),
        )
        for m in in_category
    ]

    coverage = (len(available) / len(in_category)) if in_category else 0.0

    return CategoryScore(
        name=category,
        score=score,
        weight=cohort_weight,
        metrics=metric_percentiles,
        coverage=coverage,
    )
