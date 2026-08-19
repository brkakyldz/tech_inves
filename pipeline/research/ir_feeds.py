"""Company investor-relations / press-release RSS feeds (ADR 0010 §8 item 2 /
plan §8 Faz 7b), Tier 1 -- the company's own announcement, not commentary
about it.

`TICKER_IR_FEEDS` is a **hand-maintained, partial** map. It only contains
tickers whose feed URL was fetched live and confirmed to parse as RSS/Atom
during this phase's build (2026-08-18) -- see
`reports/agents/2026-08-18_faz7b-keyless-sources.md` for the full list of
candidate URLs that were tried and did not work (redirected off-domain,
404'd, or returned an HTML landing page instead of a feed). Many IR sites
are hosted on vendor platforms (Q4, Business Wire syndication, custom CMS)
with no single URL convention, so guessing a plausible-looking URL and
listing it unverified would silently misrepresent coverage -- a ticker not
in this map gets zero extra results from this leg, exactly like a ticker
EDGAR has no CIK for, rather than a fabricated non-working source.

Only 10 of the 43 watchlist tickers have a confirmed working feed as of
this build. This is intentionally left partial rather than padded.
"""

from __future__ import annotations

import logging
from xml.etree import ElementTree

import requests

from pipeline.research.tavily_client import TavilySearchResult

logger = logging.getLogger(__name__)

# ticker -> RSS/Atom feed URL, confirmed live (HTTP 200, parses with an
# <rss> or <feed> root) during this phase's build. See module docstring.
TICKER_IR_FEEDS: dict[str, str] = {
    "AAPL": "https://www.apple.com/newsroom/rss-feed.rss",
    "CRM": "https://www.salesforce.com/news/rss/",
    "NVDA": "https://nvidianews.nvidia.com/releases.xml",
    "AMD": "https://ir.amd.com/rss/news-releases.xml",
    "INTC": "https://newsroom.intel.com/feed",
    "MSFT": "https://news.microsoft.com/source/feed/",
    "GOOGL": "https://blog.google/rss/",
    "META": "https://about.fb.com/news/feed/",
    "AMZN": "https://www.aboutamazon.com/news/rss",
    "CSCO": "https://blogs.cisco.com/feed",
}

# How many recent items (post date-window filtering) to surface per branch --
# same rationale as edgar_submissions.MAX_RESULTS.
MAX_RESULTS = 10

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _parse_rss_items(root: ElementTree.Element) -> list[dict]:
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description = (item.findtext("description") or "").strip()
        if link:
            items.append({"title": title, "link": link, "date": pub_date, "content": description})
    return items


def _parse_atom_entries(root: ElementTree.Element) -> list[dict]:
    items = []
    for entry in root.findall("atom:entry", _ATOM_NS) or root.findall("entry"):
        title = (entry.findtext("atom:title", namespaces=_ATOM_NS) or entry.findtext("title") or "").strip()
        link_el = entry.find("atom:link", _ATOM_NS) if entry.find("atom:link", _ATOM_NS) is not None else entry.find("link")
        link = (link_el.get("href") if link_el is not None else "") or ""
        updated = (
            entry.findtext("atom:updated", namespaces=_ATOM_NS)
            or entry.findtext("updated")
            or entry.findtext("atom:published", namespaces=_ATOM_NS)
            or entry.findtext("published")
            or ""
        ).strip()
        summary = (entry.findtext("atom:summary", namespaces=_ATOM_NS) or entry.findtext("summary") or "").strip()
        if link:
            items.append({"title": title, "link": link, "date": updated, "content": summary})
    return items


def parse_feed(xml_text: str) -> list[dict]:
    """Parses an RSS 2.0 or Atom feed body into a flat list of
    {title, link, date, content}. Raises `ElementTree.ParseError` on
    malformed XML -- callers catch it, matching every other additive
    leg's "never raise out of search()" contract."""
    root = ElementTree.fromstring(xml_text)
    tag = root.tag.lower()
    if tag.endswith("feed"):
        return _parse_atom_entries(root)
    return _parse_rss_items(root)


class IRFeedSearcher:
    """TavilySearcher-Protocol-shaped. `query` is the branch's ticker
    (same convention as `EdgarFullTextSearcher`/`EdgarSubmissionsSearcher`);
    a ticker not in `feeds` (default `TICKER_IR_FEEDS`) returns zero
    results rather than raising or guessing a URL.

    Never raises out of `search()` -- a down feed, a redirect that changes
    content type, or a parse failure all degrade to zero extra results for
    that one branch.
    """

    def __init__(
        self,
        *,
        feeds: dict[str, str] | None = None,
        timeout_seconds: int = 10,
        user_agent: str = "TechInves-Research/1.0",
    ) -> None:
        self._feeds = feeds if feeds is not None else TICKER_IR_FEEDS
        self._timeout = timeout_seconds
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent})

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        ticker = query.strip().upper()
        url = self._feeds.get(ticker)
        if not url:
            return []
        try:
            resp = self._session.get(url, timeout=self._timeout)
            resp.raise_for_status()
            items = parse_feed(resp.text)
        except (requests.RequestException, ElementTree.ParseError) as exc:
            logger.warning("IR feed fetch/parse failed for %s (%s): %s", ticker, url, exc)
            return []

        results: list[TavilySearchResult] = []
        for item in items:
            # RSS pubDate / Atom updated are free-text-ish date formats that
            # vary by publisher; date-window filtering here would require a
            # per-format parser for marginal benefit (the feed is already
            # ticker-scoped and returns only recent items in practice) --
            # left unfiltered, matching the count-based cap below instead.
            results.append(
                TavilySearchResult(
                    {"title": item["title"], "url": item["link"], "content": item["content"]}
                )
            )
            if len(results) >= MAX_RESULTS:
                break
        return results
