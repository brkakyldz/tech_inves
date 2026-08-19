from __future__ import annotations

import pipeline.research.edgar_search as edgar_search
from pipeline.research.edgar_search import EdgarFullTextSearcher


def _hit(cik: int, accession: str, *, forms=("8-K",), name="NVIDIA CORP") -> dict:
    return {
        "_id": f"{accession}:0001.txt",
        "_source": {"ciks": [str(cik)], "forms": list(forms), "display_names": [name]},
    }


def test_search_builds_urls_from_hits(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"hits": {"hits": [_hit(1045810, "0001045810-26-000123")]}}

    monkeypatch.setattr(
        edgar_search.requests.Session,
        "get",
        lambda self, url, params, timeout: _FakeResponse(),
    )

    searcher = EdgarFullTextSearcher(user_agent="Test (test@example.com)")
    results = searcher.search("NVDA")

    assert len(results) == 1
    assert results[0]["url"] == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000123"
    assert results[0]["title"] == "NVIDIA CORP"


def test_search_passes_form_filter_and_date_range(monkeypatch):
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"hits": {"hits": []}}

    def fake_get(self, url, params, timeout):
        captured.update(url=url, params=params)
        return _FakeResponse()

    monkeypatch.setattr(edgar_search.requests.Session, "get", fake_get)

    searcher = EdgarFullTextSearcher(user_agent="Test (test@example.com)")
    searcher.search("NVDA", start_date="2026-08-04", end_date="2026-08-10")

    assert captured["params"]["forms"] == "8-K,10-Q,10-K"
    assert captured["params"]["startdt"] == "2026-08-04"
    assert captured["params"]["enddt"] == "2026-08-10"


def test_search_returns_empty_on_request_failure(monkeypatch):
    def fake_get(self, url, params, timeout):
        raise edgar_search.requests.RequestException("network down")

    monkeypatch.setattr(edgar_search.requests.Session, "get", fake_get)

    searcher = EdgarFullTextSearcher(user_agent="Test (test@example.com)")
    assert searcher.search("NVDA") == []


def test_search_skips_hits_missing_cik_or_accession(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"hits": {"hits": [{"_id": "", "_source": {}}]}}

    monkeypatch.setattr(
        edgar_search.requests.Session,
        "get",
        lambda self, url, params, timeout: _FakeResponse(),
    )

    searcher = EdgarFullTextSearcher(user_agent="Test (test@example.com)")
    assert searcher.search("NVDA") == []
