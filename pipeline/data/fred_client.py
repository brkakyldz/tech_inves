"""FRED (Federal Reserve Economic Data) client (R28): the quantitative
macro spine's data source. FRED is a public-domain aggregator that
includes series sourced from the Federal Reserve, Census, and BEA, so a
single client here covers the "FRED/Census" half of R28's named sources
without a second integration -- see pipeline/macro_spine.py's series
selection for which agency each series actually originates from.

TSMC monthly revenue and a forward earnings/FOMC calendar are NOT covered
here: TSMC publishes no free public API for its monthly revenue releases,
and FRED has no calendar-of-events series. Recorded as follow-up in
reports/backlog/macro-spine-tsmc-and-forward-calendar.md rather than
fabricated.
"""

from __future__ import annotations

import os
from typing import Protocol

import requests

FRED_BASE_URL = "https://api.stlouisfed.org/fred"


class FredSeriesPoint(dict):
    """{"date": "2026-08-01", "value": 5.33}."""


class FredClient(Protocol):
    def latest_observation(self, series_id: str) -> FredSeriesPoint | None: ...


class LiveFredClient:
    def __init__(self, api_key: str | None = None, timeout_seconds: int = 15) -> None:
        key = api_key or os.environ.get("FRED_API_KEY")
        if not key:
            raise RuntimeError("FRED_API_KEY is not set")
        self._api_key = key
        self._timeout = timeout_seconds

    def latest_observation(self, series_id: str) -> FredSeriesPoint | None:
        response = requests.get(
            f"{FRED_BASE_URL}/series/observations",
            params={
                "series_id": series_id,
                "api_key": self._api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        observations = response.json().get("observations") or []
        if not observations:
            return None
        obs = observations[0]
        if obs.get("value") in (None, ".", ""):  # FRED's "no data" sentinel is "."
            return None
        return FredSeriesPoint({"date": obs["date"], "value": float(obs["value"])})
