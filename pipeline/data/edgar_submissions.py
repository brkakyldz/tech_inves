"""SEC EDGAR `submissions` feed per CIK (ADR 0010 §8 item 3 / plan §8 Faz 7b).

Answers "what has this company filed recently" directly, which the
full-text search leg in `pipeline/research/edgar_search.py` does not: that
leg searches EDGAR's text index for a keyword match, while this one reads
the registrant's own filing history straight off `data.sec.gov`.

Reuses `techinves.data.cik_map` (ticker -> CIK resolution) and
`techinves.data.edgar_client.EdgarClient` (the User-Agent/10-req/s
throttle/retry/cache discipline SEC requires) rather than re-implementing
either -- `pipeline/data/scores_repository.py` already establishes the
precedent that `pipeline/data/**` (unlike `pipeline/config.py`) may import
from `techinves.db`/`techinves.data` when the alternative is a second,
undisciplined HTTP client hitting the same rate-limited host.
"""

from __future__ import annotations

import logging

import requests

from techinves.data.cik_map import load_cik_map, resolve_cik
from techinves.data.edgar_client import EdgarClient

from pipeline.research.tavily_client import TavilySearchResult

logger = logging.getLogger(__name__)

SEC_SUBMISSIONS_BASE_URL = "https://data.sec.gov/submissions"
SUBMISSIONS_ENDPOINT = "edgar_submissions"

# Same primary-event forms edgar_search.py's full-text leg surfaces --
# material events and periodic reports, not every exhibit/amendment SEC
# indexes under a registrant.
PRIMARY_FILING_FORMS = ("8-K", "10-Q", "10-K")

# How many recent filings (post date/form filtering) to surface per branch --
# keeps a company with a very active filer history from swamping the
# research prompt's context budget with old, already-covered filings.
MAX_RESULTS = 10


class EdgarSubmissionsSearcher:
    """TavilySearcher-Protocol-shaped. `query` is the branch's ticker (the
    same convention `EdgarFullTextSearcher.search()` uses); everything else
    about `query` is ignored since the submissions feed is a per-CIK filing
    list, not a keyword search.

    Never raises out of `search()` -- matches every other additive research
    leg's contract (`edgar_search.EdgarFullTextSearcher`,
    `ir_feeds.IRFeedSearcher`): a failed or unresolved lookup degrades to
    zero extra results rather than failing the branch that's using it.
    """

    def __init__(
        self,
        *,
        client: EdgarClient | None = None,
        cik_map: dict[str, str] | None = None,
    ) -> None:
        self._client = client or EdgarClient()
        # Resolved lazily (not in __init__) so constructing a searcher never
        # itself makes a network call -- callers that build one but never
        # search (e.g. a macro-only run) pay nothing.
        self._cik_map = cik_map

    def _resolve_cik(self, ticker: str) -> str | None:
        if self._cik_map is None:
            self._cik_map = load_cik_map(self._client)
        return resolve_cik(ticker, self._cik_map)

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        ticker = query.strip().upper()
        try:
            cik = self._resolve_cik(ticker)
            if cik is None:
                return []
            data = self._client.get_json(
                f"{SEC_SUBMISSIONS_BASE_URL}/CIK{cik}.json", SUBMISSIONS_ENDPOINT, ticker
            )
        except requests.RequestException as exc:
            logger.warning("EDGAR submissions fetch failed for %s: %s", ticker, exc)
            return []
        if not data:
            return []

        company_name = data.get("name") or ticker
        recent = (data.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        accessions = recent.get("accessionNumber") or []
        primary_docs = recent.get("primaryDocument") or []

        results: list[TavilySearchResult] = []
        for form, filing_date, accession, primary_doc in zip(forms, dates, accessions, primary_docs):
            if form not in PRIMARY_FILING_FORMS:
                continue
            if start_date is not None and filing_date < start_date:
                continue
            if end_date is not None and filing_date > end_date:
                continue
            accession_nodash = accession.replace("-", "")
            doc = primary_doc or ""
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_nodash}/{doc}".rstrip("/")
            results.append(
                TavilySearchResult(
                    {
                        "title": f"{company_name} {form} filed {filing_date}",
                        "url": url,
                        "content": f"{form} filing by {company_name}, filed {filing_date}.",
                    }
                )
            )
            if len(results) >= MAX_RESULTS:
                break
        return results
