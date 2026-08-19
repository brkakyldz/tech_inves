"""SEC EDGAR full-text search (R16): a dedicated per-company query leg
against efts.sec.gov, so a primary filing (8-K, guidance) is directly
findable instead of relying on Tavily's include_domains=sec.gov filter to
happen to surface one -- Tavily crawls news, not EDGAR's own full-text
index, so it rarely does.

Rate-limit/User-Agent/throttle pattern mirrors
src/techinves/data/edgar_client.py's EdgarClient -- duplicated rather than
imported, since pipeline/config.py is deliberately independent of
src/techinves (see its module docstring).
"""

from __future__ import annotations

import logging
import time

import requests

from pipeline.research.tavily_client import TavilySearchResult

logger = logging.getLogger(__name__)

EDGAR_FULL_TEXT_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
# SEC's published fair-access limit (same figure EdgarClient enforces).
EDGAR_MAX_REQUESTS_PER_SECOND = 10.0
# Primary filing forms worth surfacing as research leads -- material events
# and periodic reports, not every exhibit/amendment type EDGAR indexes.
PRIMARY_FILING_FORMS = ("8-K", "10-Q", "10-K")


class EdgarFullTextSearcher:
    """TavilySearcher-Protocol-shaped: one query against EDGAR full-text
    search, restricted to PRIMARY_FILING_FORMS. Never raises out of
    search() -- a failed EDGAR call degrades to zero extra results rather
    than taking down the branch that's using it as a supplementary leg."""

    def __init__(
        self,
        *,
        user_agent: str,
        base_url: str = EDGAR_FULL_TEXT_SEARCH_URL,
        max_requests_per_second: float = EDGAR_MAX_REQUESTS_PER_SECOND,
        timeout_seconds: int = 15,
    ) -> None:
        self._base_url = base_url
        self._min_interval = 1.0 / max_requests_per_second if max_requests_per_second > 0 else 0.0
        self._last_request_at: float | None = None
        self._timeout = timeout_seconds
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})

    def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        now = time.monotonic()
        if self._last_request_at is not None:
            wait = self._min_interval - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at = time.monotonic()

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        params: dict = {"q": query, "forms": ",".join(PRIMARY_FILING_FORMS)}
        if start_date is not None:
            params["dateRange"] = "custom"
            params["startdt"] = start_date
            params["enddt"] = end_date or start_date

        self._throttle()
        try:
            resp = self._session.get(self._base_url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("EDGAR full-text search failed for %r: %s", query, exc)
            return []

        hits = (data.get("hits") or {}).get("hits") or []
        results: list[TavilySearchResult] = []
        for hit in hits:
            source = hit.get("_source") or {}
            ciks = source.get("ciks") or []
            accession = (hit.get("_id") or "").split(":")[0].replace("-", "")
            if not ciks or not accession:
                continue
            url = f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/{accession}"
            display_names = source.get("display_names") or [query]
            results.append(
                TavilySearchResult(
                    {
                        "title": display_names[0],
                        "url": url,
                        "content": source.get("file_description")
                        or " ".join(source.get("forms") or []),
                    }
                )
            )
        return results
