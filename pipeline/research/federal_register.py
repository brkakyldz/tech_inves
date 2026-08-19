"""Federal Register API (ADR 0010 §8 item 4 / plan §8 Faz 7b), Tier 1.

Keyless, public-domain, and the primary source for the Regulation macro
topic -- the exact topic where a citation was fabricated three runs running
(`reports/backlog/citation-fabrication-under-thin-research.md`). The
Federal Register is the government's own publication of the rule the
research prompt would otherwise have to take on faith from a secondary
news summary.

Docs: https://www.federalregister.gov/developers/documentation/api/v1
"""

from __future__ import annotations

import logging

import requests

from pipeline.research.tavily_client import TavilySearchResult

logger = logging.getLogger(__name__)

FEDERAL_REGISTER_ARTICLES_URL = "https://www.federalregister.gov/api/v1/articles.json"

# Matches macro branches whose topic is (or clearly concerns) the
# Regulation spine topic in pipeline.config.MACRO_TOPICS -- deliberately a
# substring test, not an exact-string match, so a reworded topic string
# doesn't silently stop routing to this leg.
REGULATION_TOPIC_MARKER = "regulation"

# Document types worth surfacing as research leads -- proposed/final rules
# and notices are the primary-event forms; skip presidential documents
# (proclamations etc.), which are almost never tech-sector relevant.
RELEVANT_DOCUMENT_TYPES = ("RULE", "PRORULE", "NOTICE")

MAX_RESULTS = 10


def is_regulation_topic(topic: str | None) -> bool:
    return bool(topic) and REGULATION_TOPIC_MARKER in topic.lower()


class FederalRegisterSearcher:
    """TavilySearcher-Protocol-shaped. Intended to be invoked only for the
    macro branch whose topic matches `is_regulation_topic()` -- the caller
    (`pipeline.research.agent.run_research_branch`) is responsible for that
    gating; this class itself will happily search on any query.

    Never raises out of `search()` -- matches every other additive
    leg's contract.
    """

    def __init__(
        self,
        *,
        base_url: str = FEDERAL_REGISTER_ARTICLES_URL,
        timeout_seconds: int = 15,
        user_agent: str = "TechInves-Research/1.0",
    ) -> None:
        self._base_url = base_url
        self._timeout = timeout_seconds
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        params: list[tuple[str, str]] = [
            ("conditions[term]", query),
            ("order", "relevance"),
            ("per_page", str(MAX_RESULTS)),
            ("fields[]", "title"),
            ("fields[]", "html_url"),
            ("fields[]", "abstract"),
            ("fields[]", "publication_date"),
            ("fields[]", "type"),
        ]
        for doc_type in RELEVANT_DOCUMENT_TYPES:
            params.append(("conditions[type][]", doc_type))
        if start_date is not None:
            params.append(("conditions[publication_date][gte]", start_date))
        if end_date is not None:
            params.append(("conditions[publication_date][lte]", end_date))

        try:
            resp = self._session.get(self._base_url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Federal Register search failed for %r: %s", query, exc)
            return []

        results: list[TavilySearchResult] = []
        for doc in data.get("results") or []:
            url = doc.get("html_url")
            if not url:
                continue
            results.append(
                TavilySearchResult(
                    {
                        "title": doc.get("title") or query,
                        "url": url,
                        "content": doc.get("abstract")
                        or f"Federal Register {doc.get('type', 'document')} published {doc.get('publication_date', '')}.",
                    }
                )
            )
            if len(results) >= MAX_RESULTS:
                break
        return results
