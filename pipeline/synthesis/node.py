"""Synthesis/writer node: turns research_findings + scores/financials into a
draft report (ARCHITECTURE_PROPOSAL.md §2.2e)."""

from __future__ import annotations

import logging

from langchain_core.language_models.chat_models import BaseChatModel

from pipeline.schemas import Finding, ReportState
from pipeline.synthesis.prompts import (
    SYNTHESIS_SYSTEM_PROMPT,
    SYNTHESIS_USER_PROMPT_TEMPLATE,
    format_citable_urls_block,
    format_failures_block,
    format_financials_block,
    format_findings_block,
    format_scores_block,
)
from pipeline.synthesis.render import (
    apply_highlights_lead_in,
    apply_macro_spine,
    apply_zero_yield_coverage_note,
    build_citation_vocabulary,
    expand_citation_ids,
    expand_watchlist_table,
    strip_ungrounded_citations,
)

logger = logging.getLogger(__name__)


def synthesize_report(state: ReportState, *, llm: BaseChatModel) -> str:
    findings: list[Finding] = state.get("research_findings", [])
    company_findings = [f for f in findings if f.scope == "company"]
    macro_findings = [f for f in findings if f.scope == "macro"]
    # The writer cites by id, so the findings blocks must name the same ids
    # the "Citable sources" block does -- otherwise the writer is handed
    # sources in one vocabulary and told to cite in another.
    vocabulary = build_citation_vocabulary(state.get("retrieved_urls", set()))

    # The de-dup context the verifier's judge already sees (ADR 0010 §9). The
    # writer was never shown it, so a theme carried over from a prior run read
    # as a fresh development every time -- the 2026-08-19 judge pass called
    # that out on the macro section specifically. Tagging happens inside
    # `format_findings_block`.
    covered_events = state.get("covered_events", [])

    prompt = SYNTHESIS_USER_PROMPT_TEMPLATE.format(
        as_of=state.get("as_of", "unknown"),
        scores_block=format_scores_block(state.get("scores", {})),
        financials_block=format_financials_block(state.get("financials", {})),
        failures_block=format_failures_block(state.get("failures", [])),
        company_findings_block=format_findings_block(
            company_findings, vocabulary, covered_events=covered_events
        ),
        macro_findings_block=format_findings_block(
            macro_findings, vocabulary, covered_events=covered_events
        ),
        citable_urls_block=format_citable_urls_block(state.get("retrieved_urls", set())),
    )

    response = llm.invoke(
        [
            ("system", SYNTHESIS_SYSTEM_PROMPT),
            ("user", prompt),
        ]
    )
    return response.content


def synthesis_node(state: ReportState, *, llm: BaseChatModel) -> dict:
    """LangGraph node wrapper: returns a partial state update.

    The full-watchlist table is expanded here, not by the LLM and not in the
    post-verifier render step: it is pure data, and the verifier's
    completeness check must still see every ticker in the draft it judges.
    """
    draft = synthesize_report(state, llm=llm)
    # The table's second list is the "never silently omitted" universe
    # (REPORT_SPEC.md §6): every scoring-eligible watchlist ticker, so one
    # with no `scores` row at all still gets a row. Passing
    # `highlight_tickers` here -- the 3-4-ticker deep-dive subset -- meant
    # that fallback only ever considered those few, so an unscored ticker
    # vanished from the table entirely and was then flagged absent by the
    # verifier, which (R31) correctly measures against the full universe.
    # Same false-negative shape R31 fixed on the verifier side; the render
    # side was missed.
    watchlist_tickers = state.get("scoring_eligible_tickers") or []
    draft = expand_watchlist_table(draft, state.get("scores", {}), watchlist_tickers)
    # R28: the quantitative macro spine -- real numbers, not LLM narrative,
    # inserted into (or adding) the "Sector & Macro" section.
    draft = apply_macro_spine(draft, state.get("macro_spine", []))
    # A highlight ticker whose research branch returned nothing is invisible
    # everywhere else in the report: it is badged as a highlight, has a score
    # row like any other ticker, and recorded no failure -- because nothing
    # failed, the branch simply found no news. The reader is left to infer
    # from an absent section. Both fills below are deterministic for the same
    # reason the watchlist table is: the writer is never handed the fact that
    # a branch it received no findings from ever ran.
    highlight_tickers = list(state.get("highlight_tickers") or [])
    tickers_with_findings = {
        f.ticker
        for f in state.get("research_findings", [])
        if f.scope == "company" and f.ticker
    }
    covered_tickers = [t for t in highlight_tickers if t in tickers_with_findings]
    zero_yield_tickers = [t for t in highlight_tickers if t not in tickers_with_findings]
    # The Highlights heading is a container for the per-company `###` headings
    # under it, so the writer routinely leaves it bodiless -- and
    # `split_into_sections` then persists a section whose whole body is its own
    # heading (run 20260819T112959-a883d9, 25 characters). Filled only when
    # empty; a writer that did produce a lead-in keeps it.
    as_of = state.get("as_of")
    draft = apply_highlights_lead_in(
        draft,
        covered_tickers=covered_tickers,
        zero_yield_tickers=zero_yield_tickers,
        as_of=str(as_of) if as_of else None,
    )
    draft = apply_zero_yield_coverage_note(draft, zero_yield_tickers)
    retrieved_urls = state.get("retrieved_urls", set())
    # The writer cites `[S<n>]` ids, never URLs (see prompts.py's hard
    # rules); this is where those ids become real links. Deterministic
    # expansion from the same closed vocabulary the prompt listed, so a URL
    # in a finished report is one a research branch actually retrieved -- by
    # construction, not by after-the-fact detection. An id the writer
    # invented expands to nothing and is reported instead.
    draft, fabricated_ids = expand_citation_ids(
        draft, build_citation_vocabulary(retrieved_urls)
    )
    # Then drop any raw URL the writer wrote anyway, so a single hallucinated
    # link doesn't leave a dead citation in the text. Runs after the
    # deterministic inserts above -- none of them emit external links.
    #
    # Both `dropped` and `fabricated_ids` are measured on the PRE-strip draft
    # and returned on the state, because this node runs *before* the verifier
    # (pipeline/graph.py's synthesis -> verifier edge). Reporting them is
    # what keeps the verifier's citation gate alive: without it the verifier
    # only ever sees the cleaned draft and a fabrication silently degrades a
    # `block` into a `pass` with the claim published unsourced.
    draft, dropped = strip_ungrounded_citations(draft, retrieved_urls)
    for citation_id in fabricated_ids:
        logger.warning(
            "synthesis: writer cited a citation id that was never in its "
            "source vocabulary: [%s]",
            citation_id,
        )
    for url in dropped:
        logger.warning(
            "synthesis: dropping ungrounded citation the writer invented "
            "(URL never retrieved): %s",
            url,
        )
    return {
        "draft_report": draft,
        "fabricated_citations_dropped": dropped,
        "fabricated_citation_ids": fabricated_ids,
    }
