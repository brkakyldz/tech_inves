from __future__ import annotations

import pipeline.research.ir_feeds as ir_feeds
from pipeline.research.ir_feeds import IRFeedSearcher, parse_feed

RSS_BODY = """<?xml version="1.0"?>
<rss version="2.0">
  <channel>
    <title>Example Newsroom</title>
    <item>
      <title>Example Corp reports Q2 results</title>
      <link>https://example.com/news/q2-results</link>
      <pubDate>Mon, 17 Aug 2026 12:00:00 GMT</pubDate>
      <description>Quarterly earnings release.</description>
    </item>
    <item>
      <title>Example Corp announces partnership</title>
      <link>https://example.com/news/partnership</link>
      <pubDate>Tue, 11 Aug 2026 09:00:00 GMT</pubDate>
      <description>New partnership announced.</description>
    </item>
  </channel>
</rss>
"""

ATOM_BODY = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Blog</title>
  <entry>
    <title>Example launches new product</title>
    <link href="https://example.com/blog/new-product" />
    <updated>2026-08-15T10:00:00Z</updated>
    <summary>Product launch announcement.</summary>
  </entry>
</feed>
"""


def test_parse_feed_handles_rss():
    items = parse_feed(RSS_BODY)
    assert len(items) == 2
    assert items[0]["title"] == "Example Corp reports Q2 results"
    assert items[0]["link"] == "https://example.com/news/q2-results"


def test_parse_feed_handles_atom():
    items = parse_feed(ATOM_BODY)
    assert len(items) == 1
    assert items[0]["link"] == "https://example.com/blog/new-product"


def test_search_fetches_and_parses_mapped_ticker(monkeypatch):
    class _FakeResponse:
        text = RSS_BODY

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        ir_feeds.requests.Session, "get", lambda self, url, timeout: _FakeResponse()
    )

    searcher = IRFeedSearcher(feeds={"NVDA": "https://example.com/rss"})
    results = searcher.search("nvda")

    assert len(results) == 2
    assert results[0]["url"] == "https://example.com/news/q2-results"


def test_search_returns_empty_for_unmapped_ticker(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ir_feeds.requests.Session,
        "get",
        lambda self, url, timeout: calls.append(url),
    )

    searcher = IRFeedSearcher(feeds={"NVDA": "https://example.com/rss"})
    assert searcher.search("AAPL") == []
    assert calls == []  # never fetched -- not in the map


def test_search_returns_empty_on_request_failure(monkeypatch):
    def fake_get(self, url, timeout):
        raise ir_feeds.requests.RequestException("network down")

    monkeypatch.setattr(ir_feeds.requests.Session, "get", fake_get)

    searcher = IRFeedSearcher(feeds={"NVDA": "https://example.com/rss"})
    assert searcher.search("NVDA") == []


def test_search_returns_empty_on_malformed_xml(monkeypatch):
    class _FakeResponse:
        text = "<not-xml"

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        ir_feeds.requests.Session, "get", lambda self, url, timeout: _FakeResponse()
    )

    searcher = IRFeedSearcher(feeds={"NVDA": "https://example.com/rss"})
    assert searcher.search("NVDA") == []


def test_search_caps_at_max_results(monkeypatch):
    many_items = "".join(
        f"<item><title>Item {i}</title><link>https://example.com/{i}</link></item>"
        for i in range(20)
    )
    body = f"<rss><channel>{many_items}</channel></rss>"

    class _FakeResponse:
        text = body

        def raise_for_status(self):
            return None

    monkeypatch.setattr(
        ir_feeds.requests.Session, "get", lambda self, url, timeout: _FakeResponse()
    )

    searcher = IRFeedSearcher(feeds={"NVDA": "https://example.com/rss"})
    results = searcher.search("NVDA")
    assert len(results) == ir_feeds.MAX_RESULTS


def test_default_feed_map_only_contains_confirmed_tickers():
    # The map is a documented, deliberately partial subset of the 43-ticker
    # watchlist (see module docstring) -- this pins its size so a future
    # edit that silently pads it with unverified URLs breaks a test instead
    # of shipping quietly.
    assert set(ir_feeds.TICKER_IR_FEEDS) == {
        "AAPL", "CRM", "NVDA", "AMD", "INTC", "MSFT", "GOOGL", "META", "AMZN", "CSCO",
    }
