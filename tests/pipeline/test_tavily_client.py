from __future__ import annotations

import pytest

from pipeline.research.tavily_client import LiveTavilySearcher


class _FakeTool:
    def __init__(self, response):
        self._response = response
        self.payloads: list[dict] = []

    def invoke(self, payload):
        self.payloads.append(payload)
        return self._response


def _searcher(response) -> LiveTavilySearcher:
    """Bypass __init__ so the test needs no API key or network."""
    searcher = LiveTavilySearcher.__new__(LiveTavilySearcher)
    searcher._tool = _FakeTool(response)
    return searcher


def test_quota_error_raises_instead_of_looking_like_an_empty_week():
    """langchain_tavily reports quota/auth failures as an `error` key, not
    an exception. Reading `results` off that dict gave [], so an exhausted
    plan produced `findings=0 failed=False` on every branch and a report
    written as though the week had been quiet."""
    searcher = _searcher(
        {"error": "Error 432: This request exceeds your plan's set usage limit."}
    )
    with pytest.raises(RuntimeError, match="Tavily search failed"):
        searcher.search("Nvidia AI chip")


def test_ordinary_empty_result_set_is_still_just_empty():
    """A genuinely empty result set must not be mistaken for a failure --
    only an explicit `error` is one."""
    assert _searcher({"results": []}).search("q") == []


def test_results_are_returned_and_date_window_is_passed_through():
    searcher = _searcher(
        {"results": [{"url": "https://techcrunch.com/a", "title": "t", "content": "c"}]}
    )
    results = searcher.search("q", start_date="2026-08-10", end_date="2026-08-17")
    assert [r["url"] for r in results] == ["https://techcrunch.com/a"]
    assert searcher._tool.payloads[0] == {
        "query": "q",
        "start_date": "2026-08-10",
        "end_date": "2026-08-17",
    }


def test_a_bare_list_response_is_still_accepted():
    assert _searcher([{"url": "https://reuters.com/x"}]).search("q")[0]["url"] == (
        "https://reuters.com/x"
    )


def test_cheap_highlight_searcher_shares_the_same_error_handling():
    """The quota-swallow bug existed twice, because this parsing was
    duplicated in highlight_selection.CheapTavilySearcher. Both now go
    through parse_search_response, so a fix cannot land in only one."""
    from pipeline.research.highlight_selection import CheapTavilySearcher

    searcher = CheapTavilySearcher.__new__(CheapTavilySearcher)
    searcher._tool = _FakeTool({"error": "Error 432: usage limit"})
    with pytest.raises(RuntimeError, match="Tavily search failed"):
        searcher.search("q")

    searcher._tool = _FakeTool({"results": [{"url": "https://techcrunch.com/a"}]})
    assert searcher.search("q")[0]["url"] == "https://techcrunch.com/a"
