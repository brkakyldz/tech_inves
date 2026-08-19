"""Tests for the two lookup conventions ADR 0007
(`reports/decisions/0007-fx-translation-for-non-usd-filers.md`) defines --
average rate over a period for duration facts, closing rate at an instant
(with bounded carry-forward) for instant facts -- plus `ECBFxClient`'s CSV
parsing and cache behaviour.

All of this is driven by fixtures, never a live network call: `FakeFxClient`
below stands in for `SupportsFxYearSeries`, and the `ECBFxClient` tests stub
its HTTP session directly. The real https://data-api.ecb.europa.eu endpoint
was never exercised while writing these -- see the agent report for what
that means for confidence in the live path.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from techinves.data.cache import FileCache
from techinves.data.fx_rates import ECBFxClient, average_rate, closing_rate


class FakeFxClient:
    """In-memory SupportsFxYearSeries, analogous to tests/conftest.py's
    FakeEdgarClient. Records calls so cache/window-scoping behaviour of the
    lookup functions can be asserted independently of ECBFxClient's HTTP
    layer.
    """

    def __init__(self, series_by_year: dict[int, dict[str, float]]) -> None:
        self.series_by_year = {
            year: {date.fromisoformat(d): v for d, v in series.items()}
            for year, series in series_by_year.items()
        }
        self.calls: list[tuple[str, int]] = []

    def get_year_series(self, currency: str, year: int) -> dict[date, float]:
        self.calls.append((currency, year))
        return dict(self.series_by_year.get(year, {}))


# --- average_rate (duration-fact convention) ---------------------------------


def test_average_rate_is_the_mean_of_published_observations_in_window():
    client = FakeFxClient(
        {
            2026: {
                "2026-01-02": 1.10,
                "2026-01-05": 1.20,
                "2026-01-06": 1.30,
            }
        }
    )
    rate = average_rate(client, "EUR", date(2026, 1, 1), date(2026, 1, 6))
    assert rate == pytest.approx((1.10 + 1.20 + 1.30) / 3)


def test_average_rate_excludes_observations_outside_the_window():
    client = FakeFxClient(
        {
            2026: {
                "2026-01-02": 1.00,
                "2026-06-15": 9.99,  # outside the requested window
            }
        }
    )
    rate = average_rate(client, "EUR", date(2026, 1, 1), date(2026, 1, 10))
    assert rate == pytest.approx(1.00)


def test_average_rate_spans_multiple_calendar_years():
    """An annual period ending in Q1 spans two calendar years; the series
    fetch must cover both, since ECBFxClient caches per (currency, year).
    """
    client = FakeFxClient(
        {
            2025: {"2025-04-01": 1.00},
            2026: {"2026-01-15": 2.00},
        }
    )
    rate = average_rate(client, "EUR", date(2025, 4, 1), date(2026, 1, 15))
    assert rate == pytest.approx(1.5)
    assert set(client.calls) == {("EUR", 2025), ("EUR", 2026)}


def test_average_rate_returns_none_when_window_has_no_observations():
    client = FakeFxClient({2026: {}})
    assert average_rate(client, "EUR", date(2026, 1, 1), date(2026, 1, 10)) is None


def test_average_rate_handles_a_reversed_start_and_end():
    client = FakeFxClient({2026: {"2026-01-02": 1.00, "2026-01-05": 2.00}})
    forward = average_rate(client, "EUR", date(2026, 1, 1), date(2026, 1, 10))
    backward = average_rate(client, "EUR", date(2026, 1, 10), date(2026, 1, 1))
    assert forward == backward == pytest.approx(1.5)


# --- closing_rate (instant-fact convention) ----------------------------------


def test_closing_rate_uses_the_exact_date_when_published():
    client = FakeFxClient({2026: {"2026-03-31": 1.08}})
    rate, used = closing_rate(client, "EUR", date(2026, 3, 31))
    assert rate == 1.08
    assert used == date(2026, 3, 31)


def test_closing_rate_carries_forward_over_an_unpublished_gap():
    """A fixture gap standing in for a weekend/TARGET holiday -- 2026-01-03
    and 2026-01-04 have no published observation, only 2026-01-02 does."""
    client = FakeFxClient({2026: {"2026-01-02": 1.15}})
    rate, used = closing_rate(client, "EUR", date(2026, 1, 4))
    assert rate == 1.15
    assert used == date(2026, 1, 2)


def test_closing_rate_returns_none_beyond_the_lookback_window():
    client = FakeFxClient({2026: {"2026-01-01": 1.00}})
    result = closing_rate(client, "EUR", date(2026, 1, 20), lookback_days=7)
    assert result is None


def test_closing_rate_respects_a_custom_lookback_window():
    client = FakeFxClient({2026: {"2026-01-01": 1.00}})
    assert closing_rate(client, "EUR", date(2026, 1, 5), lookback_days=3) is None
    rate, used = closing_rate(client, "EUR", date(2026, 1, 5), lookback_days=10)
    assert rate == 1.00
    assert used == date(2026, 1, 1)


def test_closing_rate_prefers_the_most_recent_observation_in_window():
    client = FakeFxClient({2026: {"2026-01-01": 1.00, "2026-01-03": 1.50}})
    rate, used = closing_rate(client, "EUR", date(2026, 1, 4), lookback_days=7)
    assert rate == 1.50
    assert used == date(2026, 1, 3)


# --- ECBFxClient: HTTP + CSV parsing + cache ---------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:  # pragma: no cover - unused, csvdata is text
        raise NotImplementedError


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self.response = response
        self.calls = 0
        self.headers: dict[str, str] = {}

    def get(self, url: str, timeout: int) -> _FakeResponse:
        self.calls += 1
        return self.response


_SAMPLE_CSV = (
    "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE\n"
    "D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-01-02,1.1000\n"
    "D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2026-01-05,1.1200\n"
)


def test_get_year_series_parses_csvdata(tmp_path):
    client = ECBFxClient(cache=FileCache(cache_dir=tmp_path))
    client._session = _FakeSession(_FakeResponse(200, _SAMPLE_CSV))

    series = client.get_year_series("EUR", 2026)

    assert series == {date(2026, 1, 2): 1.1000, date(2026, 1, 5): 1.1200}


def test_get_year_series_returns_empty_on_404(tmp_path):
    client = ECBFxClient(cache=FileCache(cache_dir=tmp_path))
    client._session = _FakeSession(_FakeResponse(404))

    assert client.get_year_series("EUR", 1998) == {}


def test_get_year_series_is_cached_across_calls(tmp_path):
    cache = FileCache(cache_dir=tmp_path)
    client = ECBFxClient(cache=cache)
    fake_session = _FakeSession(_FakeResponse(200, _SAMPLE_CSV))
    client._session = fake_session

    first = client.get_year_series("EUR", 2026)
    assert fake_session.calls == 1

    # A second client (simulating a later process) sharing the same cache
    # directory must not hit the network again.
    other_client = ECBFxClient(cache=cache)
    other_client._session = _FakeSession(_FakeResponse(500, ""))  # would fail if hit
    second = other_client.get_year_series("EUR", 2026)

    assert second == first
    assert second == {date(2026, 1, 2): 1.1000, date(2026, 1, 5): 1.1200}


def test_get_year_series_use_cache_false_bypasses_cache(tmp_path):
    cache = FileCache(cache_dir=tmp_path)
    client = ECBFxClient(cache=cache, use_cache=False)
    client._session = _FakeSession(_FakeResponse(200, _SAMPLE_CSV))

    client.get_year_series("EUR", 2026)
    assert cache.get("ecb_fx_rates", "EUR_2026", None) is None
