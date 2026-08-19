from __future__ import annotations

import pytest

from pipeline.research.exa_client import FallbackSearcher, LiveExaSearcher
from pipeline.research.tavily_client import TavilySearchResult


class _StubSearcher:
    def __init__(self, results=None, raise_error: bool = False):
        self._results = results or []
        self.raise_error = raise_error
        self.calls: list[tuple] = []

    def search(self, query, *, start_date=None, end_date=None):
        self.calls.append((query, start_date, end_date))
        if self.raise_error:
            raise RuntimeError("primary provider failure")
        return [TavilySearchResult(r) for r in self._results]


def test_fallback_not_used_when_primary_has_enough_results():
    primary = _StubSearcher(results=[{"url": "https://reuters.com/a"}, {"url": "https://reuters.com/b"}])
    fallback = _StubSearcher(results=[{"url": "https://exa.example/c"}])

    results = FallbackSearcher(primary, fallback, min_results=2).search("query")

    assert len(results) == 2
    assert fallback.calls == []


def test_fallback_supplements_thin_primary_results():
    primary = _StubSearcher(results=[{"url": "https://reuters.com/a"}])
    fallback = _StubSearcher(results=[{"url": "https://exa.example/b"}])

    results = FallbackSearcher(primary, fallback, min_results=2).search("query")

    urls = {r["url"] for r in results}
    assert urls == {"https://reuters.com/a", "https://exa.example/b"}


def test_fallback_dedupes_by_url():
    primary = _StubSearcher(results=[{"url": "https://reuters.com/a"}])
    fallback = _StubSearcher(
        results=[{"url": "https://reuters.com/a"}, {"url": "https://exa.example/b"}]
    )

    results = FallbackSearcher(primary, fallback, min_results=2).search("query")

    urls = [r["url"] for r in results]
    assert urls == ["https://reuters.com/a", "https://exa.example/b"]


def test_fallback_used_when_primary_raises():
    primary = _StubSearcher(raise_error=True)
    fallback = _StubSearcher(results=[{"url": "https://exa.example/b"}])

    results = FallbackSearcher(primary, fallback, min_results=2).search("query")

    assert [r["url"] for r in results] == ["https://exa.example/b"]


def test_fallback_passes_through_search_window():
    primary = _StubSearcher(results=[{"url": "https://reuters.com/a"}, {"url": "https://reuters.com/b"}])
    fallback = _StubSearcher()

    FallbackSearcher(primary, fallback, min_results=2).search(
        "query", start_date="2026-08-04", end_date="2026-08-10"
    )

    assert primary.calls == [("query", "2026-08-04", "2026-08-10")]


def test_live_exa_searcher_requires_api_key(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        LiveExaSearcher()


def test_live_exa_searcher_restricts_to_trusted_domains(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "test-key")
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": "t", "url": "https://reuters.com/x", "text": "..."}]}

    def fake_post(url, *, json, headers, timeout):
        captured.update(url=url, json=json, headers=headers)
        return _FakeResponse()

    import pipeline.research.exa_client as exa_client

    monkeypatch.setattr(exa_client.requests, "post", fake_post)

    results = LiveExaSearcher().search("NVDA news", start_date="2026-08-04", end_date="2026-08-10")

    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["json"]["includeDomains"] == exa_client.TRUSTED_DOMAINS
    assert captured["json"]["startPublishedDate"] == "2026-08-04"
    assert results[0]["url"] == "https://reuters.com/x"
