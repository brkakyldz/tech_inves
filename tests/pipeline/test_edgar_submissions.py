from __future__ import annotations

import pipeline.data.edgar_submissions as edgar_submissions
from pipeline.data.edgar_submissions import EdgarSubmissionsSearcher


class _FakeEdgarClient:
    """Stands in for techinves.data.edgar_client.EdgarClient -- no network,
    no cache directory. get_json() is the only method the searcher calls."""

    def __init__(self, submissions: dict | None):
        self._submissions = submissions
        self.calls: list[tuple[str, str, str]] = []

    def get_json(self, url: str, endpoint: str, cache_key: str):
        self.calls.append((url, endpoint, cache_key))
        return self._submissions


def _submissions(*, name="NVIDIA CORP", forms=None, dates=None, accessions=None, docs=None):
    forms = forms or ["8-K", "4", "10-Q"]
    dates = dates or ["2026-08-15", "2026-08-10", "2026-08-01"]
    accessions = accessions or ["0001045810-26-000123", "0001045810-26-000100", "0001045810-26-000050"]
    docs = docs or ["form8k.htm", "form4.xml", "form10q.htm"]
    return {
        "name": name,
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": accessions,
                "primaryDocument": docs,
            }
        },
    }


def test_search_resolves_cik_and_filters_primary_forms():
    client = _FakeEdgarClient(_submissions())
    searcher = EdgarSubmissionsSearcher(client=client, cik_map={"NVDA": "0001045810"})

    results = searcher.search("NVDA")

    # The "4" (Form 4, not a primary event form) is dropped; 8-K and 10-Q kept.
    assert len(results) == 2
    assert results[0]["url"] == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123/form8k.htm"
    )
    assert "8-K" in results[0]["title"]


def test_search_filters_by_date_window():
    client = _FakeEdgarClient(_submissions())
    searcher = EdgarSubmissionsSearcher(client=client, cik_map={"NVDA": "0001045810"})

    results = searcher.search("NVDA", start_date="2026-08-12", end_date="2026-08-20")

    assert len(results) == 1
    assert results[0]["title"].startswith("NVIDIA CORP 8-K")


def test_search_returns_empty_for_unresolvable_ticker():
    client = _FakeEdgarClient(_submissions())
    searcher = EdgarSubmissionsSearcher(client=client, cik_map={})

    assert searcher.search("NOPE") == []
    # Never even attempted the network call for an unresolved ticker.
    assert client.calls == []


def test_search_returns_empty_when_edgar_has_nothing():
    client = _FakeEdgarClient(None)
    searcher = EdgarSubmissionsSearcher(client=client, cik_map={"NVDA": "0001045810"})

    assert searcher.search("NVDA") == []


def test_search_returns_empty_on_request_exception():
    class _RaisingClient:
        def get_json(self, url, endpoint, cache_key):
            raise edgar_submissions.requests.RequestException("network down")

    searcher = EdgarSubmissionsSearcher(client=_RaisingClient(), cik_map={"NVDA": "0001045810"})

    assert searcher.search("NVDA") == []


def test_search_resolves_cik_map_lazily_only_once():
    calls = {"count": 0}

    def fake_load_cik_map(client):
        calls["count"] += 1
        return {"NVDA": "0001045810"}

    class _Client:
        def get_json(self, url, endpoint, cache_key):
            return _submissions()

    import pipeline.data.edgar_submissions as mod

    orig = mod.load_cik_map
    mod.load_cik_map = fake_load_cik_map
    try:
        searcher = EdgarSubmissionsSearcher(client=_Client())
        searcher.search("NVDA")
        searcher.search("NVDA")
        assert calls["count"] == 1
    finally:
        mod.load_cik_map = orig
