from __future__ import annotations

import pipeline.research.federal_register as federal_register
from pipeline.research.federal_register import FederalRegisterSearcher, is_regulation_topic


def test_is_regulation_topic_matches_the_macro_spine_string():
    assert is_regulation_topic(
        "Regulation: antitrust / data privacy / AI legislation - big tech impact"
    )
    assert not is_regulation_topic("AI capex cycle / hyperscaler capital expenditure")
    assert not is_regulation_topic(None)


def test_search_builds_results_from_response(monkeypatch):
    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "Antitrust Guidelines for AI Markets",
                        "html_url": "https://www.federalregister.gov/documents/2026/08/15/example",
                        "abstract": "Proposed guidance on AI market competition.",
                        "publication_date": "2026-08-15",
                        "type": "PRORULE",
                    }
                ]
            }

    def fake_get(self, url, params, timeout):
        captured["params"] = params
        return _FakeResponse()

    monkeypatch.setattr(federal_register.requests.Session, "get", fake_get)

    searcher = FederalRegisterSearcher()
    results = searcher.search(
        "antitrust technology", start_date="2026-08-09", end_date="2026-08-15"
    )

    assert len(results) == 1
    assert results[0]["url"].startswith("https://www.federalregister.gov/documents/")
    assert results[0]["title"] == "Antitrust Guidelines for AI Markets"
    param_keys = [k for k, _ in captured["params"]]
    assert ("conditions[publication_date][gte]", "2026-08-09") in captured["params"]
    assert ("conditions[publication_date][lte]", "2026-08-15") in captured["params"]
    assert param_keys.count("conditions[type][]") == 3


def test_search_returns_empty_on_request_failure(monkeypatch):
    def fake_get(self, url, params, timeout):
        raise federal_register.requests.RequestException("network down")

    monkeypatch.setattr(federal_register.requests.Session, "get", fake_get)

    searcher = FederalRegisterSearcher()
    assert searcher.search("antitrust") == []


def test_search_skips_results_missing_url(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [{"title": "No URL here"}]}

    monkeypatch.setattr(
        federal_register.requests.Session,
        "get",
        lambda self, url, params, timeout: _FakeResponse(),
    )

    searcher = FederalRegisterSearcher()
    assert searcher.search("antitrust") == []


def test_search_caps_at_max_results(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": f"Doc {i}",
                        "html_url": f"https://www.federalregister.gov/documents/{i}",
                    }
                    for i in range(20)
                ]
            }

    monkeypatch.setattr(
        federal_register.requests.Session,
        "get",
        lambda self, url, params, timeout: _FakeResponse(),
    )

    searcher = FederalRegisterSearcher()
    results = searcher.search("antitrust")
    assert len(results) == federal_register.MAX_RESULTS
