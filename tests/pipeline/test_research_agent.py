from __future__ import annotations

from datetime import date

from pipeline.research.agent import FindingsBatch, _search_window, run_research_branch
from pipeline.research.prompts import (
    COMPANY_USER_PROMPT_TEMPLATE,
    MACRO_USER_PROMPT_TEMPLATE,
    RESEARCH_SYSTEM_PROMPT,
)
from pipeline.schemas import Finding, ResearchBranchInput
from tests.pipeline.conftest import FakeChatModel, FakeSearcher


def test_company_branch_returns_findings_and_urls():
    searcher = FakeSearcher(
        results=[{"title": "NVDA news", "url": "https://reuters.com/nvda", "content": "..."}]
    )
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="New product launch",
        event_type="product",
        narrative="NVDA announced a new chip.",
        source_urls=["https://reuters.com/nvda"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    findings, failures, urls = run_research_branch(branch, searcher=searcher, llm=llm)

    assert failures == []
    assert len(findings) == 1
    assert findings[0].ticker == "NVDA"
    assert urls == {"https://reuters.com/nvda"}
    assert "NVDA" in searcher.queries[0]


def test_macro_branch_forces_ticker_none():
    finding = Finding(
        scope="macro",
        ticker="SHOULD_BE_CLEARED",
        event_title="Fed holds rates",
        event_type="macro",
        narrative="The Fed held rates steady.",
        source_urls=["https://reuters.com/fed"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    searcher = FakeSearcher(results=[{"title": "Fed", "url": "https://reuters.com/fed"}])
    branch = ResearchBranchInput(scope="macro", topic="Fed policy")

    findings, failures, urls = run_research_branch(branch, searcher=searcher, llm=llm)

    assert failures == []
    assert findings[0].ticker is None
    assert findings[0].scope == "macro"


def test_branch_failure_produces_failure_note_not_exception():
    searcher = FakeSearcher(raise_error=True)
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    findings, failures, urls = run_research_branch(
        branch, searcher=searcher, llm=llm, sleep_fn=lambda _seconds: None
    )

    assert findings == []
    assert urls == set()
    assert len(failures) == 1
    assert failures[0].ticker == "NVDA"
    assert "simulated Tavily failure" in failures[0].reason


def test_branch_retries_with_exponential_backoff_then_fails():
    searcher = FakeSearcher(raise_error=True)
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")
    sleeps: list[float] = []

    findings, failures, urls = run_research_branch(
        branch,
        searcher=searcher,
        llm=llm,
        max_retries=2,
        backoff_seconds=1.0,
        sleep_fn=sleeps.append,
    )

    assert len(searcher.queries) == 3  # 1 initial attempt + 2 retries
    assert sleeps == [1.0, 2.0]  # backoff before each retry, none after the last attempt
    assert "research failed after 3 attempt(s)" in failures[0].reason


def test_branch_succeeds_on_retry_without_further_backoff():
    searcher = FakeSearcher(raise_error=True)
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="Recovered on retry",
        event_type="product",
        narrative="Succeeded on the second attempt.",
        source_urls=["https://reuters.com/nvda"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")
    sleeps: list[float] = []

    call_count = {"n": 0}

    def flaky_search(query: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("transient failure")
        return [{"title": "NVDA news", "url": "https://reuters.com/nvda", "content": "..."}]

    searcher.search = flaky_search  # type: ignore[method-assign]

    findings, failures, urls = run_research_branch(
        branch, searcher=searcher, llm=llm, sleep_fn=sleeps.append
    )

    assert failures == []
    assert len(findings) == 1
    assert sleeps == [1.0]  # one backoff before the successful retry, none after


def test_ungrounded_citation_is_dropped_from_the_finding():
    """R4: a finding claiming a source_url that Tavily never returned must
    have that URL stripped -- otherwise the LLM's own claim is what defines
    "retrieved", which the verifier's citation check can never catch."""
    searcher = FakeSearcher(
        results=[{"title": "NVDA news", "url": "https://reuters.com/nvda", "content": "..."}]
    )
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="New product launch",
        event_type="product",
        narrative="NVDA announced a new chip.",
        source_urls=["https://reuters.com/nvda", "https://fabricated.example.com/nvda"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    findings, failures, urls = run_research_branch(branch, searcher=searcher, llm=llm)

    assert failures == []
    assert findings[0].source_urls == ["https://reuters.com/nvda"]
    assert urls == {"https://reuters.com/nvda"}


def test_retrieved_urls_come_from_tavily_results_not_llm_claims():
    """Even if every finding is dropped/empty, retrieved_urls must reflect
    what the search actually returned."""
    searcher = FakeSearcher(
        results=[
            {"title": "NVDA news", "url": "https://reuters.com/nvda"},
            {"title": "NVDA news 2", "url": "https://cnbc.com/nvda"},
        ]
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    _, _, urls = run_research_branch(branch, searcher=searcher, llm=llm)

    assert urls == {"https://reuters.com/nvda", "https://cnbc.com/nvda"}


def test_search_window_pinned_to_as_of():
    """R7: an explicit start/end window is passed to the searcher, anchored
    to the branch's week_of rather than Tavily's relative time_range."""
    searcher = FakeSearcher(results=[])
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA", as_of=date(2026, 8, 10))

    run_research_branch(branch, searcher=searcher, llm=llm)

    assert searcher.last_window == ("2026-08-04", "2026-08-10")


def test_search_window_length_is_configurable():
    """PIPELINE_RESEARCH_LOOKBACK_DAYS: the rolling window is a knob, not a
    hardcoded `timedelta(days=6)`. It counts both endpoints, so 7 reproduces
    the historical window exactly and 14 reaches back a fortnight."""
    assert _search_window(date(2026, 8, 10), lookback_days=7) == ("2026-08-04", "2026-08-10")
    assert _search_window(date(2026, 8, 10), lookback_days=14) == ("2026-07-28", "2026-08-10")
    assert _search_window(date(2026, 8, 10), lookback_days=1) == ("2026-08-10", "2026-08-10")
    # Nonsense values clamp to a same-day window rather than inverting the range.
    assert _search_window(date(2026, 8, 10), lookback_days=0) == ("2026-08-10", "2026-08-10")
    assert _search_window(None, lookback_days=30) == (None, None)


def test_finding_gets_best_domain_tier_from_surviving_citations():
    searcher = FakeSearcher(
        results=[
            {"title": "SEC filing", "url": "https://sec.gov/filing/1"},
            {"title": "Reuters piece", "url": "https://reuters.com/article/1"},
        ]
    )
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="8-K filed",
        event_type="filing",
        narrative="NVDA filed an 8-K.",
        source_urls=["https://reuters.com/article/1", "https://sec.gov/filing/1"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    findings, _, _ = run_research_branch(branch, searcher=searcher, llm=llm)

    assert findings[0].source_tier == 1  # sec.gov beats reuters.com


def test_finding_with_no_surviving_citations_has_no_tier():
    searcher = FakeSearcher(results=[])
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="Rumor",
        event_type="other",
        narrative="Unsourced.",
        source_urls=["https://fabricated.example.com"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    findings, _, _ = run_research_branch(branch, searcher=searcher, llm=llm)

    assert findings[0].source_urls == []
    assert findings[0].source_tier is None


def test_company_branch_issues_a_second_query_for_the_resolved_company_name():
    searcher = FakeSearcher(
        results=[{"title": "NVDA news", "url": "https://reuters.com/nvda"}]
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    run_research_branch(branch, searcher=searcher, llm=llm, company_names={"NVDA": "NVIDIA Corp."})

    assert searcher.queries == [
        "NVDA stock news this week",
        "NVIDIA Corp. earnings OR acquisition OR product launch this week",
    ]


def test_company_branch_falls_back_to_single_query_without_a_resolved_name():
    searcher = FakeSearcher(results=[])
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="company", ticker="UNKNOWNTICKER")

    run_research_branch(branch, searcher=searcher, llm=llm, company_names={})

    assert searcher.queries == ["UNKNOWNTICKER stock news this week"]


def test_macro_branch_issues_two_angle_queries():
    searcher = FakeSearcher(results=[])
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="macro", topic="Fed policy")

    run_research_branch(branch, searcher=searcher, llm=llm)

    assert searcher.queries == [
        "Fed policy technology sector impact",
        "Fed policy latest developments this week",
    ]


def test_multi_query_results_are_deduped_by_url():
    searcher = FakeSearcher(results=[{"title": "dup", "url": "https://reuters.com/nvda"}])
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="e",
        event_type="product",
        narrative="n",
        source_urls=["https://reuters.com/nvda"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    _, _, urls = run_research_branch(
        branch, searcher=searcher, llm=llm, company_names={"NVDA": "NVIDIA Corp."}
    )

    assert urls == {"https://reuters.com/nvda"}  # not counted twice despite 2 queries


class _StubEdgarSearcher:
    def __init__(self, results):
        self._results = results
        self.calls: list[str] = []

    def search(self, query, *, start_date=None, end_date=None):
        self.calls.append(query)
        return self._results


def test_edgar_leg_supplements_company_branch_results():
    searcher = FakeSearcher(
        results=[{"title": "NVDA news", "url": "https://reuters.com/nvda"}]
    )
    edgar_searcher = _StubEdgarSearcher(
        [{"title": "8-K", "url": "https://sec.gov/nvda-8k"}]
    )
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="8-K filed",
        event_type="filing",
        narrative="NVDA filed an 8-K.",
        source_urls=["https://reuters.com/nvda", "https://sec.gov/nvda-8k"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    findings, _, urls = run_research_branch(
        branch, searcher=searcher, llm=llm, edgar_searcher=edgar_searcher
    )

    assert urls == {"https://reuters.com/nvda", "https://sec.gov/nvda-8k"}
    assert findings[0].source_urls == ["https://reuters.com/nvda", "https://sec.gov/nvda-8k"]
    assert edgar_searcher.calls == ["NVDA"]


def test_edgar_leg_not_invoked_for_macro_branches():
    searcher = FakeSearcher(results=[{"title": "Fed", "url": "https://reuters.com/fed"}])
    edgar_searcher = _StubEdgarSearcher([])
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="macro", topic="Fed policy")

    run_research_branch(branch, searcher=searcher, llm=llm, edgar_searcher=edgar_searcher)

    assert edgar_searcher.calls == []


def test_ir_feed_leg_supplements_company_branch_results():
    searcher = FakeSearcher(
        results=[{"title": "NVDA news", "url": "https://reuters.com/nvda"}]
    )
    ir_feed_searcher = _StubEdgarSearcher(
        [{"title": "Press release", "url": "https://nvidianews.nvidia.com/x"}]
    )
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="Press release",
        event_type="product",
        narrative="NVDA announced something.",
        source_urls=["https://reuters.com/nvda", "https://nvidianews.nvidia.com/x"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    findings, _, urls = run_research_branch(
        branch, searcher=searcher, llm=llm, ir_feed_searcher=ir_feed_searcher
    )

    assert urls == {"https://reuters.com/nvda", "https://nvidianews.nvidia.com/x"}
    assert ir_feed_searcher.calls == ["NVDA"]


def test_ir_feed_leg_not_invoked_for_macro_branches():
    searcher = FakeSearcher(results=[{"title": "Fed", "url": "https://reuters.com/fed"}])
    ir_feed_searcher = _StubEdgarSearcher([])
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="macro", topic="Fed policy")

    run_research_branch(branch, searcher=searcher, llm=llm, ir_feed_searcher=ir_feed_searcher)

    assert ir_feed_searcher.calls == []


def test_edgar_submissions_leg_supplements_company_branch_results():
    searcher = FakeSearcher(
        results=[{"title": "NVDA news", "url": "https://reuters.com/nvda"}]
    )
    edgar_submissions_searcher = _StubEdgarSearcher(
        [{"title": "10-Q filed", "url": "https://sec.gov/nvda-10q"}]
    )
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="10-Q filed",
        event_type="filing",
        narrative="NVDA filed a 10-Q.",
        source_urls=["https://reuters.com/nvda", "https://sec.gov/nvda-10q"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    findings, _, urls = run_research_branch(
        branch,
        searcher=searcher,
        llm=llm,
        edgar_submissions_searcher=edgar_submissions_searcher,
    )

    assert urls == {"https://reuters.com/nvda", "https://sec.gov/nvda-10q"}
    assert edgar_submissions_searcher.calls == ["NVDA"]


def test_edgar_submissions_leg_not_invoked_for_macro_branches():
    searcher = FakeSearcher(results=[{"title": "Fed", "url": "https://reuters.com/fed"}])
    edgar_submissions_searcher = _StubEdgarSearcher([])
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="macro", topic="Fed policy")

    run_research_branch(
        branch, searcher=searcher, llm=llm, edgar_submissions_searcher=edgar_submissions_searcher
    )

    assert edgar_submissions_searcher.calls == []


def test_regulation_searcher_invoked_only_for_regulation_macro_topic():
    searcher = FakeSearcher(results=[{"title": "Reg", "url": "https://reuters.com/reg"}])
    regulation_searcher = _StubEdgarSearcher(
        [{"title": "New rule", "url": "https://www.federalregister.gov/documents/x"}]
    )
    finding = Finding(
        scope="macro",
        topic="Regulation: antitrust / data privacy / AI legislation - big tech impact",
        event_title="New antitrust rule",
        event_type="regulatory",
        narrative="A new rule was proposed.",
        source_urls=["https://reuters.com/reg", "https://www.federalregister.gov/documents/x"],
    )
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[finding]))
    branch = ResearchBranchInput(
        scope="macro",
        topic="Regulation: antitrust / data privacy / AI legislation - big tech impact",
    )

    findings, _, urls = run_research_branch(
        branch, searcher=searcher, llm=llm, regulation_searcher=regulation_searcher
    )

    assert urls == {"https://reuters.com/reg", "https://www.federalregister.gov/documents/x"}
    assert regulation_searcher.calls == [
        "Regulation: antitrust / data privacy / AI legislation - big tech impact"
    ]


def test_regulation_searcher_not_invoked_for_non_regulation_macro_topic():
    searcher = FakeSearcher(results=[{"title": "Fed", "url": "https://reuters.com/fed"}])
    regulation_searcher = _StubEdgarSearcher([])
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="macro", topic="Fed / interest rate policy")

    run_research_branch(branch, searcher=searcher, llm=llm, regulation_searcher=regulation_searcher)

    assert regulation_searcher.calls == []


def test_regulation_searcher_not_invoked_for_company_branches():
    searcher = FakeSearcher(results=[{"title": "NVDA", "url": "https://reuters.com/nvda"}])
    regulation_searcher = _StubEdgarSearcher([])
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    run_research_branch(branch, searcher=searcher, llm=llm, regulation_searcher=regulation_searcher)

    assert regulation_searcher.calls == []


def test_search_window_omitted_when_no_as_of():
    searcher = FakeSearcher(results=[])
    llm = FakeChatModel(structured_response=FindingsBatch(findings=[]))
    branch = ResearchBranchInput(scope="company", ticker="NVDA")

    run_research_branch(branch, searcher=searcher, llm=llm)

    assert searcher.last_window == (None, None)


class _RefusingStructuredRunnable:
    def invoke(self, messages):
        raise RuntimeError("Response blocked by content_filter policy")


class _RefusingLLM:
    def with_structured_output(self, schema):
        return _RefusingStructuredRunnable()


def test_refusal_short_circuits_the_retry_loop():
    """R11: a content-policy refusal is unwinnable -- retrying burns quota
    on something guaranteed to fail identically, so the branch should give
    up after one attempt with no backoff sleep."""
    searcher = FakeSearcher(results=[{"title": "NVDA", "url": "https://reuters.com/nvda"}])
    branch = ResearchBranchInput(scope="company", ticker="NVDA")
    sleeps: list[float] = []

    findings, failures, urls = run_research_branch(
        branch,
        searcher=searcher,
        llm=_RefusingLLM(),
        max_retries=2,
        sleep_fn=sleeps.append,
        company_names={},  # isolate from WATCHLIST.md's real content (R17)
    )

    assert findings == []
    assert sleeps == []  # no backoff attempted
    assert len(searcher.queries) == 1  # only the first attempt ran
    assert "1 attempt(s)" in failures[0].reason
    assert "content_filter" in failures[0].reason


def test_covered_events_prompt_orders_a_followup_not_a_silent_drop():
    """The 2026-08-19 macro yield collapse: four macro topics returned zero
    findings, and they were exactly the four whose entire covered-events
    context carried over from the previous same-day run with nothing new
    added. The prompt said "do not re-report as new" and "return an empty
    list if nothing is new", and the model did the obvious thing -- it
    dropped the topic, making a live sector condition look dead.

    The extraction step's contract is now: a covered event is still EMITTED,
    carrying `is_followup_of`. How a follow-up is *presented* to the reader
    is the synthesis stage's decision, and it cannot make it about a finding
    research never handed it. No schema change was needed -- `is_followup_of`
    already exists on `Finding`.
    """
    for template in (COMPANY_USER_PROMPT_TEMPLATE, MACRO_USER_PROMPT_TEMPLATE):
        assert "is_followup_of" in template
        assert "NOT excluded" in template
        assert "do not re-report as new" not in template

    system = RESEARCH_SYSTEM_PROMPT
    assert "is still REPORTED, not dropped" in system
    # The empty-list escape hatch must be about *retrieval*, not novelty.
    assert "is NOT a reason to return an empty list" in system


def test_macro_prompt_still_forbids_inventing_findings():
    """The follow-up instruction widens what counts as reportable; it must
    not become a licence to fabricate when results really are empty --
    that is the failure mode `backlog/citation-fabrication-under-thin-research.md`
    tracks, and loosening the novelty rule is exactly when it would bite."""
    assert "Never invent content to fill a quota" in RESEARCH_SYSTEM_PROMPT
    assert "always return fewer" in RESEARCH_SYSTEM_PROMPT
