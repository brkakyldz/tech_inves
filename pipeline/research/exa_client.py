"""Exa search fallback (R15).

Invoked when Tavily raises or returns too few results for a branch's query
-- the observed real-run failure mode is single-provider burst
rate-limiting once a full-watchlist fan-out (dozens of branches) hits
Tavily concurrently, not an overall quality gap. `FallbackSearcher` wraps a
primary searcher behind the same `TavilySearcher` Protocol
(pipeline/research/tavily_client.py), so pipeline/research/agent.py needs
no awareness of which provider actually served a given branch.
"""

from __future__ import annotations

import logging

import requests

from pipeline.config import EXA_FALLBACK_MIN_RESULTS, TRUSTED_DOMAINS, get_exa_api_key
from pipeline.research.tavily_client import TavilySearcher, TavilySearchResult

logger = logging.getLogger(__name__)

EXA_SEARCH_URL = "https://api.exa.ai/search"


class LiveExaSearcher:
    """Real Exa `/search` call, restricted to the same TRUSTED_DOMAINS
    Tavily uses -- the fallback provider must not relax the source-quality
    bar just because the primary provider is unavailable."""

    def __init__(self, max_results: int = 8) -> None:
        api_key = get_exa_api_key()
        if not api_key:
            raise RuntimeError("EXA_API_KEY is not set")
        self._api_key = api_key
        self._max_results = max_results

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        payload: dict = {
            "query": query,
            "numResults": self._max_results,
            "includeDomains": TRUSTED_DOMAINS,
            "type": "keyword",
        }
        if start_date is not None:
            payload["startPublishedDate"] = start_date
        if end_date is not None:
            payload["endPublishedDate"] = end_date

        response = requests.post(
            EXA_SEARCH_URL,
            json=payload,
            headers={"x-api-key": self._api_key, "Content-Type": "application/json"},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return [
            TavilySearchResult(
                {
                    "title": item.get("title") or "",
                    "url": item.get("url") or "",
                    "content": item.get("text") or item.get("highlight") or "",
                }
            )
            for item in data.get("results", [])
            if item.get("url")
        ]


class FallbackSearcher:
    """Wraps a primary searcher; falls back to a secondary when the primary
    raises or returns fewer than `min_results`. Never raises itself unless
    the fallback also fails -- pipeline/research/agent.py's retry loop
    expects a single searcher.search() call it can catch as one failure.

    A thin (not failed) primary result set is kept and merged with the
    fallback's results (de-duplicated by URL) rather than discarded --
    every result found is still a legitimate, grounded citation candidate.
    """

    def __init__(
        self,
        primary: TavilySearcher,
        fallback: TavilySearcher,
        *,
        min_results: int = EXA_FALLBACK_MIN_RESULTS,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._min_results = min_results

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        try:
            results = list(self._primary.search(query, start_date=start_date, end_date=end_date))
        except Exception as exc:  # noqa: BLE001 - deliberate: try the fallback provider
            logger.warning("primary searcher failed (%s); falling back to Exa", exc)
            return self._fallback.search(query, start_date=start_date, end_date=end_date)

        if len(results) >= self._min_results:
            return results

        logger.info(
            "primary searcher returned %d result(s) for %r (thin, min=%d); "
            "supplementing with Exa",
            len(results),
            query,
            self._min_results,
        )
        seen = {r.get("url") for r in results}
        for r in self._fallback.search(query, start_date=start_date, end_date=end_date):
            if r.get("url") not in seen:
                results.append(r)
                seen.add(r.get("url"))
        return results
