from __future__ import annotations

import pytest

from pipeline.research.tavily_client import TavilySearchResult


class FakeSearcher:
    """Deterministic stand-in for TavilySearcher -- no network calls."""

    def __init__(self, results: list[dict] | None = None, raise_error: bool = False):
        self._results = results or []
        self.raise_error = raise_error
        self.queries: list[str] = []
        self.last_window: tuple[str | None, str | None] = (None, None)

    def search(
        self, query: str, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[TavilySearchResult]:
        self.queries.append(query)
        self.last_window = (start_date, end_date)
        if self.raise_error:
            raise RuntimeError("simulated Tavily failure")
        return [TavilySearchResult(r) for r in self._results]


class FakeChatModel:
    """Fake BaseChatModel-shaped object: with_structured_output(schema)
    returns a runnable whose .invoke(...) returns a pre-set canned response,
    regardless of schema, so tests don't need a real LLM/API key."""

    def __init__(self, structured_response=None, text_response: str = ""):
        self._structured_response = structured_response
        self._text_response = text_response
        self.invocations: list = []

    def with_structured_output(self, schema):
        return _FakeStructuredRunnable(self._structured_response, self)

    def invoke(self, messages):
        self.invocations.append(messages)
        return _FakeAIMessage(self._text_response)


class _FakeStructuredRunnable:
    def __init__(self, response, parent: FakeChatModel):
        self._response = response
        self._parent = parent

    def invoke(self, messages):
        self._parent.invocations.append(messages)
        return self._response


class _FakeAIMessage:
    def __init__(self, content: str):
        self.content = content


@pytest.fixture
def fake_searcher() -> FakeSearcher:
    return FakeSearcher()


@pytest.fixture
def fake_llm() -> FakeChatModel:
    return FakeChatModel()
