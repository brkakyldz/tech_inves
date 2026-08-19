from __future__ import annotations

from pipeline.fixtures.mock_data import MOCK_FINANCIALS, MOCK_SCORES
from pipeline.schemas import Finding
from pipeline.synthesis.node import synthesis_node, synthesize_report
from tests.pipeline.conftest import FakeChatModel


def test_synthesis_builds_prompt_and_returns_llm_text():
    findings = [
        Finding(
            scope="company",
            ticker="NVDA",
            event_title="Chip launch",
            event_type="product",
            narrative="NVDA launched a new chip.",
            source_urls=["https://reuters.com/nvda"],
        ),
        Finding(
            scope="macro",
            ticker=None,
            event_title="Fed holds rates",
            event_type="macro",
            narrative="The Fed held rates steady.",
            source_urls=["https://reuters.com/fed"],
        ),
    ]
    llm = FakeChatModel(text_response="# Weekly Report\n...not investment advice...")

    state = {
        "as_of": "2026-08-10",
        "scores": MOCK_SCORES,
        "financials": MOCK_FINANCIALS,
        "failures": [],
        "research_findings": findings,
    }

    report = synthesize_report(state, llm=llm)

    assert report == "# Weekly Report\n...not investment advice..."
    sent_prompt = llm.invocations[0][1][1]
    assert "NVDA launched a new chip." in sent_prompt
    assert "The Fed held rates steady." in sent_prompt
    assert "composite_score=87.5" in sent_prompt
    assert "forward_pe=34.2" in sent_prompt
    assert "ev_ebitda=28.9" in sent_prompt


def test_synthesis_handles_missing_financials():
    llm = FakeChatModel(text_response="# Weekly Report")
    state = {
        "as_of": "2026-08-10",
        "scores": MOCK_SCORES,
        "failures": [],
        "research_findings": [],
    }

    synthesize_report(state, llm=llm)

    sent_prompt = llm.invocations[0][1][1]
    assert "(no financials provided)" in sent_prompt


def test_synthesis_prompt_lists_the_closed_citable_source_vocabulary():
    """F5: the writer is handed citation *ids*, not a list of URLs it could
    adapt into a plausible-looking one."""
    llm = FakeChatModel(text_response="# Weekly Report")
    state = {
        "as_of": "2026-08-10",
        "scores": MOCK_SCORES,
        "financials": MOCK_FINANCIALS,
        "failures": [],
        "research_findings": [],
        "retrieved_urls": {"https://reuters.com/b", "https://reuters.com/a"},
    }
    synthesize_report(state, llm=llm)
    sent_prompt = llm.invocations[0][1][1]
    assert "Citable sources" in sent_prompt
    # sorted, so a temperature=0 writer sees a stable prompt across runs
    assert "- [S1] https://reuters.com/a\n- [S2] https://reuters.com/b" in sent_prompt


def test_synthesis_prompt_gives_findings_the_same_ids_as_the_source_list():
    """The findings block and the citable-source block must speak one
    vocabulary -- otherwise the writer is shown sources as URLs and told to
    cite them as ids."""
    llm = FakeChatModel(text_response="# Weekly Report")
    state = {
        "as_of": "2026-08-10",
        "scores": MOCK_SCORES,
        "failures": [],
        "research_findings": [
            Finding(
                scope="company",
                ticker="NVDA",
                event_title="Chip launch",
                event_type="product",
                narrative="NVDA launched a new chip.",
                source_urls=["https://reuters.com/nvda"],
            )
        ],
        "retrieved_urls": {"https://reuters.com/nvda"},
    }
    synthesize_report(state, llm=llm)
    sent_prompt = llm.invocations[0][1][1]
    assert "(sources: [S1])" in sent_prompt


def test_synthesis_prompt_says_so_when_nothing_was_retrieved():
    llm = FakeChatModel(text_response="# Weekly Report")
    state = {
        "as_of": "2026-08-10",
        "scores": MOCK_SCORES,
        "failures": [],
        "research_findings": [],
        "retrieved_urls": set(),
    }
    synthesize_report(state, llm=llm)
    assert "no URLs retrieved for this run" in llm.invocations[0][1][1]


def test_synthesis_node_strips_a_citation_the_writer_invented():
    llm = FakeChatModel(
        text_response=(
            "# Weekly Report\n\nA claim "
            "[Reuters](https://www.reuters.com/invented-2026-08-14) and a real one "
            "[CNBC](https://cnbc.com/real)."
        )
    )
    state = {
        "as_of": "2026-08-10",
        "scores": MOCK_SCORES,
        "failures": [],
        "research_findings": [],
        "retrieved_urls": {"https://cnbc.com/real"},
    }
    out = synthesis_node(state, llm=llm)
    assert out["fabricated_citations_dropped"] == [
        "https://www.reuters.com/invented-2026-08-14"
    ]
    assert "invented-2026-08-14" not in out["draft_report"]
    assert "[CNBC](https://cnbc.com/real)" in out["draft_report"]


def test_synthesis_node_expands_citation_ids_into_real_urls():
    """F5's structural half: the writer emits `[S<n>]`, the node emits the
    URL. A cited link in a finished report is one a research branch actually
    retrieved by construction, not by after-the-fact detection."""
    llm = FakeChatModel(
        text_response="# Weekly Report\n\nA claim [S2] and another [S1].\n"
    )
    state = {
        "as_of": "2026-08-10",
        "scores": MOCK_SCORES,
        "failures": [],
        "research_findings": [],
        "retrieved_urls": {"https://cnbc.com/real", "https://reuters.com/real"},
    }
    out = synthesis_node(state, llm=llm)
    assert "[S1](https://cnbc.com/real)" in out["draft_report"]
    assert "[S2](https://reuters.com/real)" in out["draft_report"]
    assert out["fabricated_citation_ids"] == []
    # The expanded links are grounded, so the defense-in-depth strip behind
    # the expansion has nothing to remove.
    assert out["fabricated_citations_dropped"] == []


def test_synthesis_node_records_an_invented_citation_id():
    """An id outside the closed vocabulary expands to nothing. It is recorded
    on the state (the pre-strip evidence the verifier blocks on) and left in
    the draft verbatim, so the blocked report shows what the writer wrote."""
    llm = FakeChatModel(
        text_response="# Weekly Report\n\nAn invented claim [S7] and a real one [S1].\n"
    )
    state = {
        "as_of": "2026-08-10",
        "scores": MOCK_SCORES,
        "failures": [],
        "research_findings": [],
        "retrieved_urls": {"https://cnbc.com/real"},
    }
    out = synthesis_node(state, llm=llm)
    assert out["fabricated_citation_ids"] == ["S7"]
    assert "[S7]" in out["draft_report"]
    assert "[S1](https://cnbc.com/real)" in out["draft_report"]


def test_synthesis_node_watchlist_table_covers_the_full_scoring_universe():
    """The table's fallback list must be every scoring-eligible ticker, not
    this run's 3-4 highlights: a ticker with no `scores` row at all is only
    rescued by that list, and the verifier (R31) measures completeness
    against the full universe either way."""
    llm = FakeChatModel(text_response="# Weekly Report\n\n{{FULL_WATCHLIST_TABLE}}")
    state = {
        "as_of": "2026-08-10",
        "scores": {"NVDA": {"composite_score": 80, "cohort": "B"}},
        "failures": [],
        "research_findings": [],
        "highlight_tickers": ["NVDA"],
        "scoring_eligible_tickers": ["NVDA", "MSFT", "ASML"],
        "retrieved_urls": set(),
    }
    draft = synthesis_node(state, llm=llm)["draft_report"]
    assert "Not scored in this run" in draft
    # MSFT/ASML have no score row and are not highlights -- they would have
    # vanished from the report entirely before this fix.
    assert "| MSFT |" in draft
    assert "| ASML |" in draft


# --- Empty Highlights / zero-yield / continuing context ---------------------


def _finding(ticker="MSFT", title="Azure expansion"):
    return Finding(
        scope="company",
        ticker=ticker,
        event_title=title,
        event_type="product",
        narrative=f"{ticker} did something.",
        source_urls=["https://reuters.com/x"],
    )


CONTAINER_DRAFT = (
    "# TechInves Sector Report\n\n"
    "Opening.\n\n"
    "## Highlights\n\n"
    "### MSFT -- Microsoft\n\n"
    "Prose.\n\n"
    "## Coverage Notes\n\n"
    "All tickers had usable data for this run.\n"
)


def test_synthesis_node_fills_a_highlights_section_the_writer_left_empty():
    """The live defect on run 20260819T112959-a883d9: the writer treats the
    Highlights heading as a container for the `###` deep-dives under it, and
    the storage layer then persists a section whose body is only its heading."""
    llm = FakeChatModel(text_response=CONTAINER_DRAFT)
    state = {
        "as_of": "2026-08-19",
        "scores": {},
        "failures": [],
        "research_findings": [_finding("MSFT")],
        "highlight_tickers": ["MSFT"],
        "retrieved_urls": set(),
    }
    draft = synthesis_node(state, llm=llm)["draft_report"]
    highlights_body = draft.split("## Highlights", 1)[1].split("### MSFT", 1)[0]
    assert highlights_body.strip()
    assert "MSFT" in highlights_body


def test_synthesis_node_names_zero_yield_tickers_in_coverage_notes():
    """A selected ticker whose branch returned nothing is otherwise invisible:
    it is badged as a highlight, has a normal score row, and records no
    failure, because nothing failed."""
    llm = FakeChatModel(text_response=CONTAINER_DRAFT)
    state = {
        "as_of": "2026-08-19",
        "scores": {},
        "failures": [],
        "research_findings": [_finding("MSFT")],
        "highlight_tickers": ["MSFT", "ADBE", "NOW"],
        "retrieved_urls": set(),
    }
    draft = synthesis_node(state, llm=llm)["draft_report"]
    notes = draft.split("## Coverage Notes", 1)[1]
    assert "ADBE" in notes and "NOW" in notes


def test_synthesis_node_inserts_no_bare_digit_the_number_leak_scan_would_flag():
    """The deterministic fills run before the verifier, which cannot tell a
    window length from a fabricated figure -- so they must carry no digit
    outside the as-of date."""
    from pipeline.verifier.rules import find_number_leaks

    llm = FakeChatModel(text_response=CONTAINER_DRAFT)
    state = {
        "as_of": "2026-08-19",
        "scores": {},
        "failures": [],
        "research_findings": [_finding("MSFT")],
        "highlight_tickers": ["MSFT", "ADBE"],
        "retrieved_urls": set(),
    }
    draft = synthesis_node(state, llm=llm)["draft_report"]
    assert find_number_leaks(draft, {}, {}) == []


def test_synthesis_prompt_tags_a_finding_a_prior_run_already_covered():
    """The judge flagged macro themes carried over from earlier runs being
    written up as new. The de-dup context was on the state; only the verifier
    was being shown it."""
    from pipeline.schemas import CoveredEvent

    finding = Finding(
        scope="macro",
        topic="Regulation",
        event_title="EU AI Act enforcement timeline",
        event_type="regulation",
        narrative="Enforcement dates were confirmed.",
        source_urls=[],
    )
    covered = [
        CoveredEvent(
            scope="macro",
            topic="Regulation",
            event_type="regulation",
            event_title="EU AI Act enforcement timeline confirmed",
            first_covered_run="run-1",
            last_updated_run="run-1",
        )
    ]
    llm = FakeChatModel(text_response="# Report")
    synthesize_report(
        {
            "as_of": "2026-08-19",
            "scores": {},
            "failures": [],
            "research_findings": [finding],
            "covered_events": covered,
            "retrieved_urls": set(),
        },
        llm=llm,
    )
    assert "[CONTINUING]" in llm.invocations[0][1][1]


def test_synthesis_prompt_leaves_a_genuinely_new_finding_untagged():
    llm = FakeChatModel(text_response="# Report")
    synthesize_report(
        {
            "as_of": "2026-08-19",
            "scores": {},
            "failures": [],
            "research_findings": [_finding("MSFT")],
            "covered_events": [],
            "retrieved_urls": set(),
        },
        llm=llm,
    )
    assert "[CONTINUING]" not in llm.invocations[0][1][1]
