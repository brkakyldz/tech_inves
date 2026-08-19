"""Ticker -> SEC CIK resolution, from SEC's published company_tickers.json.

EDGAR keys everything by CIK, never by ticker, so this lookup sits in front of
every companyfacts fetch. The file is a single ~1 MB JSON document covering all
listed registrants, so one request resolves the whole watchlist -- it is fetched
once per run and cached for a day (ADR 0001, step 1).
"""

from __future__ import annotations

from typing import Any

import requests

from techinves.config import SEC_COMPANY_TICKERS_URL
from techinves.data.edgar_client import COMPANY_TICKERS_ENDPOINT, EdgarClient

# Not a real ticker -- FileCache keys on (endpoint, ticker, params) and this
# document is not per-company, so it needs a placeholder in the ticker slot.
_CACHE_KEY = "_all"


def normalize_cik(raw: int | str) -> str:
    """EDGAR's JSON carries the CIK as a bare int; its URLs need it zero-padded
    to 10 digits. data.sec.gov 404s on the unpadded form, so every caller must
    go through this.
    """
    return str(raw).strip().lstrip("0").zfill(10)


def _parse(document: Any) -> dict[str, str]:
    """company_tickers.json is a dict keyed by row index, not a list:
    `{"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}`.
    Tolerates a list too, in case SEC ever flattens it.
    """
    rows = document.values() if isinstance(document, dict) else document
    mapping: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = row.get("ticker")
        cik = row.get("cik_str", row.get("cik"))
        if not ticker or cik is None:
            continue
        # SEC writes class shares as "BRK-B"; watchlists and FMP both use
        # "BRK.B". Register both spellings so either resolves.
        symbol = str(ticker).strip().upper()
        mapping[symbol] = normalize_cik(cik)
        if "-" in symbol:
            mapping.setdefault(symbol.replace("-", "."), normalize_cik(cik))
    return mapping


def load_cik_map(client: EdgarClient) -> dict[str, str]:
    """Ticker -> zero-padded CIK for every SEC-registered listed company.

    Caching (and its TTL) belongs to the client's FileCache, same as every
    other EDGAR fetch. Returns an empty dict if SEC is unreachable; callers
    then report every ticker as missing rather than crashing the run. This
    covers both "got a response with nothing useful in it" (get_json returns
    a falsy document) and "never got a response at all" (get_json exhausts
    its retries and raises) -- a DNS blip or SEC outage must degrade the same
    way a 404 does, not take down the whole watchlist run with a traceback.
    A 403 (misconfigured User-Agent) is not caught here: that is a config
    error, not a transient outage, and edgar_client raises it as ConfigError
    specifically so it is not mistaken for one.
    """
    try:
        document = client.get_json(SEC_COMPANY_TICKERS_URL, COMPANY_TICKERS_ENDPOINT, _CACHE_KEY)
    except requests.RequestException:
        return {}
    if not document:
        return {}
    return _parse(document)


def resolve_cik(ticker: str, cik_map: dict[str, str]) -> str | None:
    return cik_map.get(ticker.strip().upper())
