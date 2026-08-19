from __future__ import annotations

import pytest

import pipeline.data.fred_client as fred_client
from pipeline.data.fred_client import LiveFredClient


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        LiveFredClient()


def test_latest_observation_parses_value_and_date(monkeypatch):
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"observations": [{"date": "2026-08-01", "value": "5.33"}]}

    def fake_get(url, *, params, timeout):
        captured.update(url=url, params=params)
        return _FakeResponse()

    monkeypatch.setattr(fred_client.requests, "get", fake_get)

    point = LiveFredClient(api_key="k").latest_observation("FEDFUNDS")

    assert point == {"date": "2026-08-01", "value": 5.33}
    assert captured["params"]["series_id"] == "FEDFUNDS"
    assert captured["params"]["api_key"] == "k"


def test_latest_observation_missing_value_sentinel_returns_none(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"observations": [{"date": "2026-08-01", "value": "."}]}

    monkeypatch.setattr(
        fred_client.requests, "get", lambda url, *, params, timeout: _FakeResponse()
    )

    assert LiveFredClient(api_key="k").latest_observation("FEDFUNDS") is None


def test_latest_observation_no_observations_returns_none(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"observations": []}

    monkeypatch.setattr(
        fred_client.requests, "get", lambda url, *, params, timeout: _FakeResponse()
    )

    assert LiveFredClient(api_key="k").latest_observation("FEDFUNDS") is None
