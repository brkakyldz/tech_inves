from techinves.models import CategoryScore, MetricPercentile
from techinves.scoring.coverage import compute_coverage, is_low_reliability


def _category(name, percentiles: list[float | None], weight=0.25) -> CategoryScore:
    metrics = [
        MetricPercentile(name=f"m{i}", raw_value=1.0, percentile=p, weight_used=1.0 / len(percentiles))
        for i, p in enumerate(percentiles)
    ]
    coverage = sum(1 for p in percentiles if p is not None) / len(percentiles) if percentiles else 0.0
    return CategoryScore(name=name, score=50.0, weight=weight, metrics=metrics, coverage=coverage)


def test_full_coverage():
    categories = [_category("valuation", [80.0, 90.0, 70.0])]
    assert compute_coverage(categories) == 1.0


def test_partial_coverage():
    categories = [_category("valuation", [80.0, None, 70.0, None])]
    assert compute_coverage(categories) == 0.5


def test_zero_coverage():
    categories = [_category("valuation", [None, None])]
    assert compute_coverage(categories) == 0.0


def test_no_metrics_returns_zero():
    assert compute_coverage([]) == 0.0


def test_low_reliability_boundary_just_below():
    assert is_low_reliability(0.59) is True


def test_low_reliability_boundary_at_threshold():
    assert is_low_reliability(0.60) is False


def test_low_reliability_above_threshold():
    assert is_low_reliability(0.75) is False
