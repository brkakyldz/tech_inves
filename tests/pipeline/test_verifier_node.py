from __future__ import annotations

from pipeline.verifier.node import run_rule_based_prescreen

COMPLETE_REPORT = """# TechInves Weekly

*This report is a screening and ranking tool based on financial-statement \
data. This is not investment advice.*

## Highlights

Deep-dive coverage below: NVDA.

### NVDA -- NVIDIA Corp.

NVDA had a strong week ([reuters.com](https://reuters.com/nvda)).

```
COMPOSITE SCORE: {{NVDA.composite_score}}
RISK INDICATOR: {{NVDA.risk_score}}
SECTOR PERCENTILE: n/a
DATA COVERAGE: 91%
```

*Narrative sections are drafted by an AI research/writing pipeline from the \
scores above and cited sources, and reviewed before publication.*
"""

SCORES = {"NVDA": {"composite_score": 87.5, "risk_score": 62.0, "coverage_pct": 91}}


def _state(**overrides) -> dict:
    base = dict(
        draft_report=COMPLETE_REPORT,
        scores=SCORES,
        financials={},
        retrieved_urls={"https://reuters.com/nvda"},
        highlight_tickers=["NVDA"],
        # R31: the completeness predicate now measures against the real
        # watchlist (`scoring_eligible_tickers`), never `highlight_tickers`
        # -- pinned to a single ticker here so these fixtures stay focused
        # on NVDA instead of accidentally exercising the real 40-ticker
        # data/watchlist.yaml.
        scoring_eligible_tickers=["NVDA"],
    )
    base.update(overrides)
    return base


def test_prescreen_populates_classified_violations_on_block():
    state = _state(
        draft_report=COMPLETE_REPORT.replace(
            "NVDA had a strong week ([reuters.com](https://reuters.com/nvda)).",
            "NVDA had a strong week, up 42% ([reuters.com](https://reuters.com/nvda)).",
        )
    )
    report = run_rule_based_prescreen(state)

    assert report.verdict == "block"
    assert any(v.category == "number_leak" and v.severity == "compliance_hard" for v in report.violations)


def test_prescreen_degraded_publish_on_structural_hard_without_compliance_issue():
    """R22: a structural_hard-only finding (no company deep-dive section at
    all) must degrade to a publishable verdict, not block outright."""
    state = _state(
        draft_report=(
            "# TechInves Weekly\n\n"
            "*This report is a screening and ranking tool. This is not investment advice. "
            "Narrative sections are drafted by an AI research/writing pipeline from the "
            "scores above and cited sources, and reviewed before publication.*\n\n"
            "NVDA and no other tickers mentioned in prose only, no headings.\n"
        ),
        scores={"NVDA": {"composite_score": 87.5}},
    )
    report = run_rule_based_prescreen(state)

    assert report.verdict == "degraded_publish"
    assert any(v.severity == "structural_hard" for v in report.violations)
    assert not any(v.severity == "compliance_hard" for v in report.violations)


def test_prescreen_compliance_hard_still_blocks_even_with_structural_hard_present():
    state = _state(
        draft_report=(
            "# TechInves Weekly\n\n"
            "NVDA up 42% this week, no disclaimer, no headings at all.\n"
        ),
    )
    report = run_rule_based_prescreen(state)

    assert report.verdict == "block"


def test_prescreen_allows_macro_spine_numbers():
    from pipeline.macro_spine import MacroSpineItem

    state = _state(
        draft_report=COMPLETE_REPORT
        + "\n\n## Sector & Macro\n\n| Indicator | Value |\n|---|---:|\n| Fed Funds | 5.33% |\n",
        macro_spine=[
            MacroSpineItem(series_id="FEDFUNDS", label="Fed Funds", units="%", value=5.33, as_of="2026-08-01")
        ],
    )
    report = run_rule_based_prescreen(state)
    assert not any(v.category == "number_leak" and "5.33" in v.message for v in report.violations)


def test_prescreen_violations_empty_on_clean_pass():
    report = run_rule_based_prescreen(_state())
    assert not any(v.severity in ("compliance_hard", "structural_hard") for v in report.violations)


def test_prescreen_blocks_on_a_citation_stripped_before_it_ran():
    """F5, the blocker this fix exists for. `strip_ungrounded_citations` runs
    inside `synthesis_node`, i.e. *before* the synthesis -> verifier edge, so
    scanning the draft alone always found a clean document: an invented
    citation silently turned a `block` into a `pass` with the claim published
    unsourced. The pre-strip evidence on the state is what the gate now
    measures."""
    state = _state(
        fabricated_citations_dropped=["https://www.reuters.com/invented-2026-08-14"]
    )
    report = run_rule_based_prescreen(state)

    assert report.verdict == "block"
    assert "https://www.reuters.com/invented-2026-08-14" in report.citation_violations
    assert any(
        v.category == "citation" and v.severity == "compliance_hard"
        for v in report.violations
    )


def test_prescreen_blocks_on_an_invented_citation_id():
    state = _state(fabricated_citation_ids=["S9"])
    report = run_rule_based_prescreen(state)

    assert report.verdict == "block"
    assert report.citation_id_violations == ["S9"]
    assert any("[S9]" in v.message for v in report.violations)


def test_prescreen_blocks_on_an_unexpanded_id_left_in_the_draft():
    """The text-only backstop: a draft that reached the verifier without the
    synthesis node's state keys is still gated on its bare `[S<n>]` markers,
    since every id in the real vocabulary became a link."""
    state = _state(
        draft_report=COMPLETE_REPORT.replace(
            "NVDA had a strong week", "NVDA had a strong week [S4]"
        )
    )
    report = run_rule_based_prescreen(state)

    assert report.verdict == "block"
    assert report.citation_id_violations == ["S4"]


def test_prescreen_does_not_flag_an_expanded_citation_id():
    state = _state(
        draft_report=COMPLETE_REPORT.replace(
            "([reuters.com](https://reuters.com/nvda))",
            "([S1](https://reuters.com/nvda))",
        )
    )
    report = run_rule_based_prescreen(state)

    assert report.citation_id_violations == []
    assert report.citation_violations == []


def test_prescreen_scales_the_deep_dive_requirement_to_this_runs_highlights():
    """F8: a single-company run (ADR 0010 §1) writes one deep-dive by design.
    Measured against a fixed 3-4 it was permanently `pass_with_flags`, which
    trains the operator to ignore the flag on the trigger they press most."""
    report = run_rule_based_prescreen(_state())
    assert report.verdict == "pass"
    assert not any("deep-dive sections" in v.message for v in report.violations)

    # Still a real check: a run that researched three tickers and wrote one
    # section is flagged.
    short = run_rule_based_prescreen(
        _state(highlight_tickers=["NVDA", "AMD", "MSFT"])
    )
    assert short.verdict == "pass_with_flags"
    assert any("deep-dive sections" in v.message for v in short.violations)


def test_low_llm_confidence_is_named_in_the_verdict_reason():
    """A run flagged purely on low confidence recorded only the judge's
    prose summary, which never mentions the scores -- so the persisted
    verdict_reason did not say why it was flagged."""
    from pipeline.verifier.node import LLMConsistencyReview, verifier_node
    from pipeline.schemas import VerifierSectionScore

    state = _state()

    class _Judge:
        def with_structured_output(self, _model):
            return self

        def invoke(self, _messages):
            return LLMConsistencyReview(
                section_scores=[
                    VerifierSectionScore(section="Coverage Notes", confidence=4, rationale="r"),
                    VerifierSectionScore(section="Closing", confidence=9, rationale="r"),
                ],
                notes="general prose that never mentions confidence",
            )

    report = verifier_node(state, llm=_Judge())["verifier_report"]
    assert report.verdict == "pass_with_flags"
    assert "low verifier confidence in: Coverage Notes (4/10)" in report.notes
    assert "Closing" not in report.notes.split(";")[0]


def test_prescreen_scales_the_deep_dive_expectation_to_tickers_that_yielded():
    """F8b / run 20260819T112959-a883d9: four tickers were selected, two came
    back with zero findings, and the run was flagged "2 deep-dive sections
    (this run expects 4)" for a gap the research layer created. A ticker with
    nothing to write about is not a synthesis defect."""
    from pipeline.schemas import Finding

    state = _state(
        highlight_tickers=["NVDA", "ADBE", "NOW"],
        research_findings=[
            Finding(
                scope="company",
                ticker="NVDA",
                event_title="Chip launch",
                event_type="product",
                narrative="NVDA launched a chip.",
                source_urls=["https://reuters.com/nvda"],
            )
        ],
    )
    report = run_rule_based_prescreen(state)

    assert not any("deep-dive sections" in v.message for v in report.violations)
    # The selection itself is still reported, so a reader can see the run was
    # narrower than it looks.
    assert report.coverage_scope == ["NVDA", "ADBE", "NOW"]


def test_prescreen_flags_a_section_with_an_empty_body():
    state = _state(
        draft_report=COMPLETE_REPORT.replace("Deep-dive coverage below: NVDA.\n\n", "")
    )
    report = run_rule_based_prescreen(state)

    assert report.verdict == "pass_with_flags"
    assert any(
        v.severity == "soft" and "empty body" in v.message for v in report.violations
    )
