"""Typed wrapper calls per FMP endpoint. Each function returns raw FMP JSON
(list[dict] or dict), or None if unavailable -- normalization into
RawFinancialFacts happens in raw_facts.py, not here.

Only `profile` remains. ADR 0001 moved every financial statement to SEC EDGAR
after FMP's free plan returned HTTP 402 "Special Endpoint: this value set for
'symbol' is not available under your current subscription" for 30 of the 42
watchlist tickers. `profile` is the one endpoint that restriction does not
cover -- verified live against all 42 -- and price plus market cap are exactly
what EDGAR cannot supply. The statement/ratio/estimate wrappers that used to
live here were deleted rather than left dormant: they were unreachable code
describing a data path the project no longer has access to.
"""

from __future__ import annotations

from typing import Any

from techinves.data.fmp_client import SupportsGet


def get_profile(client: SupportsGet, ticker: str) -> dict[str, Any] | None:
    data = client.get("profile", ticker)
    if not data:
        return None
    return data[0] if isinstance(data, list) else data
