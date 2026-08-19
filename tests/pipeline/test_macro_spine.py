from __future__ import annotations

from pipeline.macro_spine import MACRO_SPINE_SERIES, build_macro_spine


class _StubFredClient:
    def __init__(self, values: dict[str, dict | None]):
        self._values = values

    def latest_observation(self, series_id: str):
        return self._values.get(series_id)


class _RaisingFredClient:
    def latest_observation(self, series_id: str):
        raise RuntimeError("network error")


def test_build_macro_spine_covers_every_static_series():
    client = _StubFredClient({sid: {"date": "2026-08-01", "value": 1.0} for sid, _, _ in MACRO_SPINE_SERIES})
    items = build_macro_spine(client)
    assert [i.series_id for i in items] == [sid for sid, _, _ in MACRO_SPINE_SERIES]


def test_build_macro_spine_missing_series_value_is_none_not_dropped():
    client = _StubFredClient({"FEDFUNDS": None, "DGS10": {"date": "2026-08-01", "value": 4.1}})
    items = build_macro_spine(client)
    fedfunds = next(i for i in items if i.series_id == "FEDFUNDS")
    assert fedfunds.value is None
    assert len(items) == len(MACRO_SPINE_SERIES)  # still covers all series


def test_build_macro_spine_one_series_raising_does_not_fail_the_whole_spine():
    items = build_macro_spine(_RaisingFredClient())
    assert len(items) == len(MACRO_SPINE_SERIES)
    assert all(i.value is None for i in items)
