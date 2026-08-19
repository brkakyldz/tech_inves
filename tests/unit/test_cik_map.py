"""Tests ticker -> CIK resolution against SEC's company_tickers.json shape."""

from __future__ import annotations

import requests

from techinves.data.cik_map import load_cik_map, normalize_cik, resolve_cik


class _FakeEdgarJson:
    """Stands in for EdgarClient at the get_json() seam that load_cik_map uses."""

    def __init__(self, document) -> None:
        self.document = document
        self.calls: list[tuple[str, str, str]] = []

    def get_json(self, url: str, endpoint: str, cache_key: str):
        self.calls.append((url, endpoint, cache_key))
        return self.document


class _RaisingEdgarJson:
    """Stands in for EdgarClient when it has exhausted retries on a network
    error -- get_json raises rather than returning a falsy document."""

    def get_json(self, url: str, endpoint: str, cache_key: str):
        raise requests.ConnectionError("boom")


_DOCUMENT = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
    "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway"},
}


def test_cik_is_zero_padded_to_ten_digits():
    """data.sec.gov 404s on the unpadded CIK the JSON actually carries."""
    assert normalize_cik(320193) == "0000320193"
    assert normalize_cik("320193") == "0000320193"
    assert normalize_cik("0000320193") == "0000320193"


def test_index_keyed_document_is_parsed():
    client = _FakeEdgarJson(_DOCUMENT)
    mapping = load_cik_map(client)

    assert mapping["AAPL"] == "0000320193"
    assert mapping["MSFT"] == "0000789019"


def test_class_share_tickers_resolve_under_both_spellings():
    """SEC writes class shares as BRK-B; watchlists and FMP use BRK.B."""
    mapping = load_cik_map(_FakeEdgarJson(_DOCUMENT))

    assert mapping["BRK-B"] == "0001067983"
    assert mapping["BRK.B"] == "0001067983"


def test_resolution_is_case_insensitive():
    mapping = load_cik_map(_FakeEdgarJson(_DOCUMENT))
    assert resolve_cik("aapl", mapping) == "0000320193"
    assert resolve_cik(" MSFT ", mapping) == "0000789019"


def test_unknown_ticker_resolves_to_none():
    mapping = load_cik_map(_FakeEdgarJson(_DOCUMENT))
    assert resolve_cik("NOTLISTED", mapping) is None


def test_one_request_covers_the_whole_watchlist():
    """The map is a single document for every registrant -- resolving it per
    ticker instead of per run would be 42 requests rather than one.
    """
    client = _FakeEdgarJson(_DOCUMENT)
    load_cik_map(client)
    assert len(client.calls) == 1


def test_unreachable_sec_yields_an_empty_map_not_an_exception():
    assert load_cik_map(_FakeEdgarJson(None)) == {}


def test_sec_outage_raising_a_request_exception_also_yields_an_empty_map():
    """The prior test only covers get_json returning a falsy document; a real
    outage (DNS failure, connection refused) makes get_json raise instead
    after exhausting retries. Must degrade the same way, not crash the run.
    """
    assert load_cik_map(_RaisingEdgarJson()) == {}


def test_malformed_rows_are_skipped():
    document = {
        "0": {"cik_str": 320193, "ticker": "AAPL"},
        "1": {"ticker": "NOCIK"},
        "2": {"cik_str": 1},
        "3": "not a dict",
    }
    mapping = load_cik_map(_FakeEdgarJson(document))
    assert mapping == {"AAPL": "0000320193"}
