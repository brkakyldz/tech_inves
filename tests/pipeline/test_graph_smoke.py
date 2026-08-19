from __future__ import annotations

from pipeline.fixtures.mock_data import MOCK_FINANCIALS, MOCK_SCORES
from pipeline.graph import build_graph
from pipeline.research.agent import FindingsBatch
from pipeline.schemas import Finding
from tests.pipeline.conftest import FakeChatModel, FakeSearcher


class ScriptedLLM(FakeChatModel):
    """Returns a canned FindingsBatch for research calls, then canned text
    for synthesis, then a clean LLMConsistencyReview for the verifier --
    driven purely by call order, since structured_output vs plain invoke
    tells them apart."""

    def __init__(self, findings_batch: FindingsBatch, report_text: str, review):
        super().__init__(text_response=report_text)
        self._findings_batch = findings_batch
        self._review = review

    def with_structured_output(self, schema):
        if schema.__name__ == "FindingsBatch":
            return _Canned(self._findings_batch, self)
        return _Canned(self._review, self)


class _Canned:
    def __init__(self, value, parent):
        self._value = value
        self._parent = parent

    def invoke(self, messages):
        self._parent.invocations.append(messages)
        return self._value


def test_full_graph_runs_end_to_end_with_fakes():
    from pipeline.verifier.node import LLMConsistencyReview

    company_finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="Chip launch",
        event_type="product",
        narrative="NVDA launched a new chip this week.",
        source_urls=["https://reuters.com/nvda"],
    )
    llm = ScriptedLLM(
        findings_batch=FindingsBatch(findings=[company_finding]),
        report_text=(
            "# Weekly Report\n\n"
            "*This report is a screening and ranking tool based on financial-statement "
            "data. This is not investment advice. Narrative sections are drafted by an AI "
            "research/writing pipeline from the scores above and cited sources, and "
            "reviewed before publication.*\n\n"
            "## This Week's Highlights\n\n"
            "### NVDA -- NVIDIA Corp.\n\n"
            "NVDA launched a new chip this week. "
            "[source](https://reuters.com/nvda)\n\n"
            "```\n"
            "COMPOSITE SCORE: {{NVDA.composite_score}}\n"
            "RISK INDICATOR: {{NVDA.risk_score}}\n"
            "SECTOR PERCENTILE: n/a\n"
            "DATA COVERAGE: {{NVDA.coverage_pct}}%\n"
            "```\n\n"
            "## Full Watchlist -- Score Summary\n\n"
            "| Ticker | Composite |\n|---|---|\n"
            "| NVDA | {{NVDA.composite_score}} |\n"
        ),
        review=LLMConsistencyReview(section_scores=[], notes="looks fine"),
    )
    searcher = FakeSearcher(
        results=[{"title": "NVDA", "url": "https://reuters.com/nvda", "content": "..."}]
    )

    graph = build_graph(searcher=searcher, llm=llm)

    result = graph.invoke(
        {
            "run_id": "test-run",
            "as_of": "2026-08-10",
            "highlight_tickers": ["NVDA"],
            "macro_topics": [],
            "covered_events": [],
            "scores": {"NVDA": MOCK_SCORES["NVDA"]},
            "financials": {"NVDA": MOCK_FINANCIALS["NVDA"]},
            # R31: pin the completeness baseline to this fixture's single
            # ticker -- otherwise the verifier measures against the real
            # 40-ticker data/watchlist.yaml and this single-ticker report
            # fails completeness instead of the pass_with_flags asserted
            # below.
            "scoring_eligible_tickers": ["NVDA"],
        }
    )

    assert len(result["research_findings"]) == 1
    assert result["research_findings"][0].ticker == "NVDA"
    assert result["draft_report"].startswith("# Weekly Report")
    # `pass`, not `pass_with_flags` (F8): the deep-dive count is now measured
    # against this run's own `highlight_tickers` rather than a fixed 3-4, so a
    # deliberately single-ticker run that wrote its one section is complete.
    # It used to be flagged for a "gap" the caller had asked for -- which made
    # ADR 0010 §1's single-company trigger permanently non-clean.
    assert result["verifier_report"].verdict == "pass"

    # R1: the research branch's yield is captured even though ScriptedLLM
    # (a plain object, not a real Runnable) has no with_config -- tokens
    # stay 0 rather than the node blowing up.
    assert len(result["branch_yields"]) == 1
    branch_yield = result["branch_yields"][0]
    assert branch_yield.ticker == "NVDA"
    assert branch_yield.findings_count == 1
    assert branch_yield.tokens == 0


_DISCLAIMED_HEADER = (
    "# Weekly Report\n\n"
    "*This report is a screening and ranking tool based on financial-statement "
    "data. This is not investment advice. Narrative sections are drafted by an AI "
    "research/writing pipeline from the scores above and cited sources, and "
    "reviewed before publication.*\n\n"
    "## This Week's Highlights\n\n"
    "### NVDA -- NVIDIA Corp.\n\n"
)
_SCORE_BLOCK = (
    "\n\n```\nCOMPOSITE SCORE: {{NVDA.composite_score}}\n"
    "RISK INDICATOR: {{NVDA.risk_score}}\nSECTOR PERCENTILE: n/a\n"
    "DATA COVERAGE: {{NVDA.coverage_pct}}%\n```\n\n"
    "## Full Watchlist -- Score Summary\n\n"
    "| Ticker | Composite |\n|---|---|\n| NVDA | {{NVDA.composite_score}} |\n"
)


def _graph_with_writer_output(report_text: str):
    from pipeline.verifier.node import LLMConsistencyReview

    company_finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="Chip launch",
        event_type="product",
        narrative="NVDA launched a new chip this week.",
        source_urls=["https://reuters.com/nvda"],
    )
    llm = ScriptedLLM(
        findings_batch=FindingsBatch(findings=[company_finding]),
        report_text=report_text,
        review=LLMConsistencyReview(section_scores=[], notes=""),
    )
    searcher = FakeSearcher(
        results=[{"title": "NVDA", "url": "https://reuters.com/nvda", "content": "..."}]
    )
    return build_graph(searcher=searcher, llm=llm)


def _invoke(graph):
    return graph.invoke(
        {
            "run_id": "test-run",
            "as_of": "2026-08-10",
            "highlight_tickers": ["NVDA"],
            "macro_topics": [],
            "covered_events": [],
            "scores": {"NVDA": MOCK_SCORES["NVDA"]},
            "financials": {"NVDA": MOCK_FINANCIALS["NVDA"]},
            "scoring_eligible_tickers": ["NVDA"],
        }
    )


def test_a_fabricated_url_blocks_the_run_through_the_live_graph():
    """F5/F15, the interaction nothing covered. `strip_ungrounded_citations`
    runs inside `synthesis_node`, before the synthesis -> verifier edge, so
    the verifier only ever saw an already-clean draft: the invented URL was
    removed, the invented *claim* stayed as prose, and the run published as
    `pass`. Unit tests of the strip and of the rule both passed throughout."""
    result = _invoke(
        _graph_with_writer_output(
            _DISCLAIMED_HEADER
            + "NVDA launched a new chip this week. "
            "[Reuters](https://www.reuters.com/invented-2026-08-14)"
            + _SCORE_BLOCK
        )
    )

    assert result["fabricated_citations_dropped"] == [
        "https://www.reuters.com/invented-2026-08-14"
    ]
    verifier = result["verifier_report"]
    assert verifier.verdict == "block"
    assert any(
        v.category == "citation" and v.severity == "compliance_hard"
        for v in verifier.violations
    )


def test_an_invented_citation_id_blocks_the_run_through_the_live_graph():
    result = _invoke(
        _graph_with_writer_output(
            _DISCLAIMED_HEADER
            + "NVDA launched a new chip this week [S8]."
            + _SCORE_BLOCK
        )
    )

    assert result["fabricated_citation_ids"] == ["S8"]
    verifier = result["verifier_report"]
    assert verifier.verdict == "block"
    assert verifier.citation_id_violations == ["S8"]


def test_a_cited_id_from_the_vocabulary_expands_and_passes():
    result = _invoke(
        _graph_with_writer_output(
            _DISCLAIMED_HEADER
            + "NVDA launched a new chip this week [S1]."
            + _SCORE_BLOCK
        )
    )

    assert result["fabricated_citation_ids"] == []
    assert "[S1](https://reuters.com/nvda)" in result["draft_report"]
    assert result["verifier_report"].verdict == "pass"


def test_verifier_uses_judge_llm_not_the_writer_llm():
    """R10: a distinct judge_llm must be the one driving the verifier's LLM
    consistency check, not the writer llm used for research/synthesis."""
    from pipeline.verifier.node import LLMConsistencyReview

    company_finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="Chip launch",
        event_type="product",
        narrative="NVDA launched a new chip this week.",
        source_urls=["https://reuters.com/nvda"],
    )
    writer_llm = ScriptedLLM(
        findings_batch=FindingsBatch(findings=[company_finding]),
        report_text=(
            "# Weekly Report\n\n"
            "*This report is a screening and ranking tool based on financial-statement "
            "data. This is not investment advice. Narrative sections are drafted by an AI "
            "research/writing pipeline from the scores above and cited sources, and "
            "reviewed before publication.*\n\n"
            "## This Week's Highlights\n\n"
            "### NVDA -- NVIDIA Corp.\n\n"
            "NVDA launched a new chip this week. "
            "[source](https://reuters.com/nvda)\n\n"
            "```\nCOMPOSITE SCORE: {{NVDA.composite_score}}\nRISK INDICATOR: {{NVDA.risk_score}}\n"
            "SECTOR PERCENTILE: n/a\nDATA COVERAGE: {{NVDA.coverage_pct}}%\n```\n\n"
            "## Full Watchlist -- Score Summary\n\n"
            "| Ticker | Composite |\n|---|---|\n| NVDA | {{NVDA.composite_score}} |\n"
        ),
        # If the writer's own llm were used for the LLM review, this
        # low-confidence score would surface -- the judge_llm below returns
        # a clean review instead, so the test asserts on which one won.
        review=LLMConsistencyReview(section_scores=[], notes="writer should not be used as judge"),
    )
    judge_llm = ScriptedLLM(
        findings_batch=FindingsBatch(findings=[]),
        report_text="unused",
        review=LLMConsistencyReview(section_scores=[], notes="judged independently"),
    )
    searcher = FakeSearcher(
        results=[{"title": "NVDA", "url": "https://reuters.com/nvda", "content": "..."}]
    )

    graph = build_graph(searcher=searcher, llm=writer_llm, judge_llm=judge_llm)
    result = graph.invoke(
        {
            "run_id": "test-run",
            "as_of": "2026-08-10",
            "highlight_tickers": ["NVDA"],
            "macro_topics": [],
            "covered_events": [],
            "scores": {"NVDA": MOCK_SCORES["NVDA"]},
            "financials": {"NVDA": MOCK_FINANCIALS["NVDA"]},
            # R31: pin the completeness baseline to this fixture's single
            # ticker -- otherwise the verifier measures against the real
            # 40-ticker data/watchlist.yaml and this single-ticker report
            # fails completeness instead of the pass_with_flags asserted
            # below.
            "scoring_eligible_tickers": ["NVDA"],
        }
    )

    assert "judged independently" in result["verifier_report"].notes
    assert "writer should not be used as judge" not in result["verifier_report"].notes
