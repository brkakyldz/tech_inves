from __future__ import annotations

from techinves.api.repositories import _metrics_out
from techinves.api.schemas import MetricPercentileOut


def test_metrics_out_maps_raw_value_percentile_and_weight_used():
    metrics = [
        {"name": "forward_pe", "raw_value": 34.2, "percentile": 71.0, "weight_used": 0.4},
        {"name": "ev_ebitda", "raw_value": None, "percentile": None, "weight_used": 0.0},
    ]

    result = _metrics_out(metrics)

    assert result == [
        MetricPercentileOut(name="forward_pe", raw_value=34.2, percentile=71.0, weight_used=0.4),
        MetricPercentileOut(name="ev_ebitda", raw_value=None, percentile=None, weight_used=0.0),
    ]


def test_metrics_out_empty_list():
    assert _metrics_out([]) == []


def test_metrics_out_none():
    assert _metrics_out(None) == []
