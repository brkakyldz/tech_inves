"""Verifier node: rule-based pre-screen + LLM consistency layer, producing a
pass / pass_with_flags / block verdict (ARCHITECTURE_PROPOSAL.md §2.2f).

The rule-based layer is authoritative for `block`: a number leak, a
fabricated citation, or a missing disclaimer forces `block` regardless of
what the LLM layer scores, per §5.2 ("son kapı" / last gate).
"""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, Field

from pipeline.config import load_scoring_eligible_tickers
from pipeline.schemas import Finding, ReportState, VerifierReport, VerifierSectionScore
from pipeline.synthesis.prompts import format_findings_block
from pipeline.verifier.prompts import (
    VERIFIER_SYSTEM_PROMPT,
    VERIFIER_USER_PROMPT_TEMPLATE,
    format_covered_events_block,
)
from pipeline.verifier.rules import (
    check_completeness,
    classify_violations,
    find_citation_id_violations,
    find_citation_violations,
    find_number_leaks,
    has_ai_disclosure,
    has_disclaimer,
    missing_low_reliability_labels,
)


class LLMConsistencyReview(BaseModel):
    section_scores: list[VerifierSectionScore] = Field(default_factory=list)
    notes: str = ""


def run_rule_based_prescreen(state: ReportState) -> VerifierReport:
    report_text = state.get("draft_report") or ""
    scores = state.get("scores", {})
    financials = state.get("financials", {})
    retrieved_urls: set[str] = state.get("retrieved_urls", set())

    # R31 (reports/backlog/verifier-checks-are-degenerate.md item 1): the
    # completeness baseline must be the real "every scoring-eligible
    # watchlist ticker" universe (REPORT_SPEC.md §1/D1, §10 item 1) --
    # data/watchlist.yaml minus `scoring_excluded` (40 of 43 tickers as of
    # D5) -- never this run's own researched-ticker subset. Using
    # `state["highlight_tickers"]` (the 3-4-ticker deep-dive selection,
    # ADR 0006 §3) here was exactly the false-negative generator the backlog
    # item names: a `--tickers AAPL NVDA` smoke run, or a normal run's small
    # highlight set, trivially "covered" itself by construction, since the
    # check had nothing external to fail against.
    #
    # `scoring_eligible_tickers` is plain graph input (same pattern as
    # scores/financials/macro_spine) computed once by pipeline/run.py via
    # `pipeline.config.load_scoring_eligible_tickers()`; falling back to a
    # fresh load here keeps this node correct for any caller (e.g. a test)
    # that omits the key, without forcing every caller to thread it through.
    full_watchlist_tickers = state.get("scoring_eligible_tickers")
    if full_watchlist_tickers is None:
        full_watchlist_tickers = load_scoring_eligible_tickers()

    # This run's actually-researched/highlighted subset is kept, but purely
    # as descriptive metadata on the VerifierReport (`coverage_scope`) --
    # never fed into the completeness predicate above. A reader of a
    # `coverage_scope=["AAPL", "NVDA"]` report can now tell at a glance that
    # this was a narrow run, instead of the completeness check silently
    # measuring against whatever subset the run happened to cover.
    coverage_scope = list(state.get("highlight_tickers", []))

    # F8b: which of those tickers a research branch actually produced anything
    # for. Highlight selection ranks on pre-fan-out mention counts, so a
    # selected ticker can come back with zero findings -- no error, nothing
    # found -- and there is then nothing for the writer to deep-dive. Run
    # 20260819T112959-a883d9 selected 4, two yielded nothing, and the run was
    # flagged "2 deep-dive sections (this run expects 4)" for a gap the
    # research layer created and the report already names in Coverage Notes.
    # See `rules.expected_highlight_range`.
    tickers_with_findings = {
        f.ticker
        for f in state.get("research_findings", [])
        if f.scope == "company" and f.ticker
    }

    # R28: the macro spine's numbers are deterministically rendered (not
    # LLM-written), same as the score block -- allow them the same way.
    # R24's filing-form/quarter-label digits ("10-K", "Q3") are stripped
    # positionally inside find_number_leaks itself, not allowlisted here.
    allowed_extra: set[str] = set()
    for item in state.get("macro_spine", []):
        if item.value is not None:
            allowed_extra.add(f"{item.value:g}")
    number_leaks = find_number_leaks(report_text, scores, financials, allowed_extra=allowed_extra)

    # F5: the citation gate has to measure the draft as the writer wrote it,
    # not as `pipeline/synthesis/render.strip_ungrounded_citations` left it.
    # That strip runs inside `synthesis_node`, i.e. *before* the graph's
    # synthesis -> verifier edge, so scanning `report_text` alone structurally
    # always finds a clean document: a fabricated citation silently degraded a
    # `block` into a `pass` with the invented claim published unsourced.
    # `fabricated_citations_dropped` / `fabricated_citation_ids` are the
    # pre-strip evidence the synthesis node records on the state precisely so
    # this check has something real to fail against; `report_text` is still
    # scanned as well, because a caller that reaches this node without those
    # keys (a test, a repair loop) must still be gated.
    citation_violations = sorted(
        set(find_citation_violations(report_text, retrieved_urls))
        | set(state.get("fabricated_citations_dropped", []))
    )
    citation_id_violations = sorted(
        set(find_citation_id_violations(report_text))
        | set(state.get("fabricated_citation_ids", [])),
        key=lambda cid: (int(cid[1:]) if cid[1:].isdigit() else 0, cid),
    )
    missing_labels = missing_low_reliability_labels(report_text, scores)
    missing_disclaimer = not has_disclaimer(report_text)
    missing_ai_disclosure = not has_ai_disclosure(report_text)
    compliance_hard_completeness, structural_hard_completeness, soft_completeness = check_completeness(
        report_text,
        scores=scores,
        watchlist_tickers=full_watchlist_tickers,
        # F8: the deep-dive count is expected to match *this* run's highlight
        # selection, not a fixed 3-4 -- otherwise ADR 0010 §1's single-company
        # trigger can never return a clean verdict. See
        # `rules.expected_highlight_range`.
        highlight_tickers=coverage_scope,
        tickers_with_findings=tickers_with_findings,
    )
    hard_completeness = compliance_hard_completeness + structural_hard_completeness

    # R19: same violations, reclassified by severity and (best-effort)
    # section -- additive alongside the flat lists above, which stay for
    # backward compatibility with existing consumers.
    violations = classify_violations(
        report_text,
        watchlist_tickers=full_watchlist_tickers,
        number_leaks=number_leaks,
        citation_violations=citation_violations,
        citation_id_violations=citation_id_violations,
        missing_disclaimer=missing_disclaimer,
        missing_ai_disclosure=missing_ai_disclosure,
        compliance_hard_completeness=compliance_hard_completeness,
        structural_hard_completeness=structural_hard_completeness,
        soft_completeness=soft_completeness,
        missing_labels=missing_labels,
    )

    # R22: severity now drives the verdict directly. compliance_hard is
    # still unconditionally terminal (REPORT_SPEC.md §5.2's "son kapı");
    # structural_hard alone -- no compliance issue -- degrades to a
    # publishable state with a banner instead of being discarded outright.
    if any(v.severity == "compliance_hard" for v in violations):
        verdict = "block"
    elif any(v.severity == "structural_hard" for v in violations):
        verdict = "degraded_publish"
    elif missing_labels or soft_completeness:
        verdict = "pass_with_flags"
    else:
        verdict = "pass"

    notes_parts = []
    if missing_labels:
        notes_parts.append(f"Missing 'low reliability' label for: {', '.join(missing_labels)}")
    notes_parts.extend(hard_completeness)
    notes_parts.extend(soft_completeness)
    notes = "; ".join(notes_parts)

    return VerifierReport(
        verdict=verdict,
        number_leak_violations=number_leaks,
        citation_violations=citation_violations,
        citation_id_violations=citation_id_violations,
        missing_disclaimer=missing_disclaimer or missing_ai_disclosure,
        completeness_violations=hard_completeness + soft_completeness,
        violations=violations,
        notes=notes,
        coverage_scope=coverage_scope,
    )


def run_llm_consistency_check(state: ReportState, *, llm: BaseChatModel) -> LLMConsistencyReview:
    findings: list[Finding] = state.get("research_findings", [])
    prompt = VERIFIER_USER_PROMPT_TEMPLATE.format(
        report_text=state.get("draft_report") or "",
        findings_block=format_findings_block(findings),
        covered_events_block=format_covered_events_block(state.get("covered_events", [])),
    )
    structured_llm = llm.with_structured_output(LLMConsistencyReview)
    review = structured_llm.invoke(
        [
            ("system", VERIFIER_SYSTEM_PROMPT),
            ("user", prompt),
        ]
    )
    return review or LLMConsistencyReview()


def verifier_node(state: ReportState, *, llm: BaseChatModel) -> dict:
    """LangGraph node wrapper: combines the rule pre-screen (authoritative
    for block/degraded_publish) with the LLM consistency layer, returns a
    partial state update."""

    rule_report = run_rule_based_prescreen(state)

    if rule_report.verdict == "block":
        # A compliance_hard finding is final and unpublishable regardless of
        # what the LLM layer would say -- running the (paid) LLM judge pass
        # on it buys nothing.
        return {"verifier_report": rule_report}

    llm_review = run_llm_consistency_check(state, llm=llm)

    # Severity order per pipeline/schemas.py's Verdict comment: pass <
    # pass_with_flags < degraded_publish < block. Low LLM confidence should
    # only ever raise the verdict's severity, never lower a rule-layer
    # degraded_publish back down to pass_with_flags.
    verdict = rule_report.verdict
    low_confidence = [s for s in llm_review.section_scores if s.confidence < 5]
    if low_confidence and verdict == "pass":
        verdict = "pass_with_flags"

    # Name the sections that raised the verdict, ahead of the LLM's general
    # notes. Without this, a run flagged purely on low confidence recorded
    # only the judge's prose summary as its reason -- which never mentions
    # the confidence scores -- so `runs.verdict_reason` did not
    # say why the run was flagged. Seen on 2026-08-17: flagged because
    # "This Week's Coverage Notes" scored 4, a fact absent from the reason.
    reason_parts = [rule_report.notes] if rule_report.notes else []
    if low_confidence:
        reason_parts.append(
            "low verifier confidence in: "
            + ", ".join(f"{s.section} ({s.confidence}/10)" for s in low_confidence)
        )
    if llm_review.notes:
        reason_parts.append(llm_review.notes)
    notes = "; ".join(reason_parts)

    final = VerifierReport(
        verdict=verdict,
        number_leak_violations=rule_report.number_leak_violations,
        citation_violations=rule_report.citation_violations,
        citation_id_violations=rule_report.citation_id_violations,
        missing_disclaimer=rule_report.missing_disclaimer,
        completeness_violations=rule_report.completeness_violations,
        section_scores=llm_review.section_scores,
        violations=rule_report.violations,
        notes=notes,
        coverage_scope=rule_report.coverage_scope,
    )
    return {"verifier_report": final}
