"""Thin wrapper around langchain_tavily.TavilySearch.

Kept as a narrow Protocol + one real implementation so tests can inject a
fake searcher instead of hitting the live API (mirrors the FMPClient
injection pattern used in src/techinves's own tests).
"""

from __future__ import annotations

from typing import Protocol

from pipeline.config import TRUSTED_DOMAINS, get_tavily_api_key


class TavilySearchResult(dict):
    """A single Tavily search hit: {title, url, content, ...}."""


class TavilySearcher(Protocol):
    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]: ...


def build_search_payload(
    query: str, *, start_date: str | None, end_date: str | None
) -> dict:
    payload: dict = {"query": query}
    if start_date is not None:
        payload["start_date"] = start_date
    if end_date is not None:
        payload["end_date"] = end_date
    return payload


def parse_search_response(raw) -> list[TavilySearchResult]:
    """Turn a `langchain_tavily` response into results, raising on failure.

    langchain_tavily reports quota, auth and transport problems as an
    `error` key on an otherwise well-formed dict rather than by raising.
    Reading `results` off such a dict yields `[]`, which is
    indistinguishable from "this query genuinely found nothing" -- so an
    exhausted API plan surfaced as a clean run with `findings=0
    failed=False` on every branch. Observed live on 2026-08-16: "Error 432:
    This request exceeds your plan's set usage limit".

    Shared by every Tavily-backed searcher rather than reimplemented per
    class: this parsing was duplicated in `LiveTavilySearcher` and
    `highlight_selection.CheapTavilySearcher`, and the bug therefore
    existed twice, in two places that had to be found separately.
    """
    if isinstance(raw, dict) and raw.get("error"):
        raise RuntimeError(f"Tavily search failed: {raw['error']}")
    results = raw.get("results", []) if isinstance(raw, dict) else raw
    return [TavilySearchResult(r) for r in results]


class LiveTavilySearcher:
    """Real TavilySearch call: topic=news, advanced depth,
    include_domains=TRUSTED_DOMAINS (ARCHITECTURE_PROPOSAL.md §2.2).

    No fixed `time_range` at construction time (R7): that filter is relative
    to "now", so a rerun or backfill of a past week would silently retrieve
    the *current* week's news instead. `start_date`/`end_date` are passed
    per-call instead, pinned to the run's `as_of`
    (`pipeline/research/agent.py`).
    """

    def __init__(self, max_results: int = 8) -> None:
        from langchain_tavily import TavilySearch

        self._tool = TavilySearch(
            tavily_api_key=get_tavily_api_key(),
            max_results=max_results,
            topic="news",
            search_depth="advanced",
            include_domains=TRUSTED_DOMAINS,
        )

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        # Raising on an error response puts the failure back on the paths
        # built for a failing provider: FallbackSearcher switches to Exa,
        # research/agent.py's retry loop backs off, and a branch that still
        # can't be served is recorded as a FailureNote, not an empty success.
        return parse_search_response(
            self._tool.invoke(
                build_search_payload(query, start_date=start_date, end_date=end_date)
            )
        )
