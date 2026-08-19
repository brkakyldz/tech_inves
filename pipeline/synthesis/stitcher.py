"""Deterministic report assembly (R20).

Every pure-data structural element -- the deep-dive heading, the score
block, the full watchlist table, the disclaimers -- is rendered here from
`scores`/`financials` directly, not asked of an LLM. `synthesize_company_section`
(`pipeline/synthesis/section_synthesis.py`) supplies only the narrative
prose that goes between the heading and the score block.

Rendering the score block with real values (not `{{ticker.field}}`
placeholders resolved later) is a deliberate choice over the old
placeholder convention: there is no "typo'd field name" failure mode for
data this module already holds directly.
"""

from __future__ import annotations

from pipeline.schemas import Finding
from pipeline.synthesis.render import (
    ZERO_YIELD_NOTE_PREFIX,
    _cell,
    _flatten_score_block,
    render_highlights_lead_in,
    render_watchlist_table,
)

OPENING_DISCLAIMER = (
    "*This report is a screening and ranking tool based on financial-statement "
    "data. Nothing in this report is investment advice -- no recommendation to "
    "buy, sell, or hold any security is made or implied.*"
)
CLOSING_DISCLAIMER = (
    "*This is not investment advice. See [the methodology](/legal) for how "
    "these scores are computed. Narrative sections are drafted by an AI "
    "research/writing pipeline from the scores above and cited sources, and "
    "reviewed before publication.*"
)
LOW_RELIABILITY_COVERAGE_THRESHOLD = 60
# ADR 0010 §2: the weekly calendar is retired as an identity, so the report's
# own headings no longer claim one. The research window is still a rolling 7
# days ending on `as_of` (pipeline/research/agent.py's `_search_window`) --
# that is a window, not a week, and the prose says so. These constants are the
# single source of the two heading strings; the verifier and the synthesis
# prompt are written against the same names.
HIGHLIGHTS_HEADING = "## Highlights"
COVERAGE_NOTES_HEADING = "## Coverage Notes"


def render_score_block(ticker: str, scores: dict[str, dict]) -> str:
    """The fenced score block REPORT_SPEC.md §5.1/§9 mandates, rendered
    directly from `scores[ticker]` -- the same four label strings the
    verifier's rule-based prescreen matches literally
    (pipeline/verifier/rules.py's SCORE_BLOCK_MARKERS), guaranteed present
    and correctly filled by construction rather than by prompt compliance.
    """
    block = scores.get(ticker) or {}
    flat = _flatten_score_block(block)
    lines = [
        "```",
        f"COMPOSITE SCORE: {_cell(block.get('composite_score'))} "
        f"(band: {_cell(block.get('composite_band'))})",
        f"  Valuation               : {_cell(flat.get('valuation_score'))} "
        f"(weight {_cell(flat.get('valuation_weight'))})",
        f"  Growth                  : {_cell(flat.get('growth_score'))} "
        f"(weight {_cell(flat.get('growth_weight'))})",
        f"  Profitability & Quality : {_cell(flat.get('quality_score'))} "
        f"(weight {_cell(flat.get('quality_weight'))})",
        f"  Financial Health        : {_cell(flat.get('financial_health_score'))} "
        f"(weight {_cell(flat.get('financial_health_weight'))})",
        f"RISK INDICATOR: {_cell(block.get('risk_score'))} (band: {_cell(block.get('risk_band'))})",
        f"  Altman Z-zone: {_cell(flat.get('altman_zone'))}",
        f"  Piotroski F-Score: {_cell(flat.get('piotroski_f'))}",
        f"SECTOR PERCENTILE: {_cell(block.get('sector_percentile'))} "
        f"(Cohort {_cell(block.get('cohort'))})",
        f"DATA COVERAGE: {_cell(block.get('coverage_pct'))} | "
        f"Warnings applied: {_cell(block.get('warnings'))}",
        "```",
    ]
    return "\n".join(lines)


def render_company_section(
    ticker: str, *, narrative: str, scores: dict[str, dict], company_names: dict[str, str]
) -> str:
    """One deep-dive section: heading (machine-detected downstream by its
    first word being the bare ticker, per REPORT_SPEC.md), the LLM's
    narrative prose, then the deterministic score block."""
    name = company_names.get(ticker)
    heading = f"### {ticker} -- {name}" if name else f"### {ticker}"
    low_reliability = ""
    coverage = (scores.get(ticker) or {}).get("coverage_pct")
    if coverage is not None and coverage < LOW_RELIABILITY_COVERAGE_THRESHOLD:
        low_reliability = "\n\n**Low reliability** (coverage below 60%)."
    return "\n\n".join([heading, narrative.strip() + low_reliability, render_score_block(ticker, scores)])


def render_macro_section(macro_findings: list[Finding]) -> str:
    """Deterministic bullet-per-finding macro section -- no LLM call. Each
    finding is already narrative prose (`Finding.narrative`), cited from its
    own `source_urls`; stitching bullets together needs no additional
    generation."""
    if not macro_findings:
        return ""
    lines = []
    for f in macro_findings:
        citation = f" ([{f.source_urls[0].split('/')[2]}]({f.source_urls[0]}))" if f.source_urls else ""
        lines.append(f"- **{f.topic or f.event_title}**: {f.narrative}{citation}")
    return "\n".join(lines)


def render_coverage_notes(
    scores: dict[str, dict],
    watchlist_tickers: list[str],
    zero_yield_tickers: list[str] | None = None,
) -> str:
    """Data gaps, plus the gap the scores cannot show: a ticker that was
    selected for a deep-dive, ran its research branch, and came back with
    nothing. It has a score and is badged as a highlight, so nothing else in
    the report tells the reader why it has no section."""
    missing = sorted(t for t, b in scores.items() if isinstance(b, dict) and b.get("missing"))
    unscored = sorted(t for t in watchlist_tickers if t not in scores)
    gaps = missing + [t for t in unscored if t not in missing]
    if gaps:
        note = "Tickers with no usable data for this run: " + ", ".join(gaps) + "."
    else:
        note = f"All {len(watchlist_tickers)} tickers had usable data for this run."
    if zero_yield_tickers:
        note += (
            f" {ZERO_YIELD_NOTE_PREFIX} " + ", ".join(zero_yield_tickers) + "."
        )
    return note


def stitch_report(
    *,
    as_of: str,
    company_sections: dict[str, str],
    macro_findings: list[Finding],
    scores: dict[str, dict],
    watchlist_tickers: list[str],
    zero_yield_tickers: list[str] | None = None,
) -> str:
    """Assembles the final report body from already-synthesized company
    narratives (`company_sections`: ticker -> narrative prose, in the
    intended display order) plus everything else, rendered directly.
    `{{FULL_WATCHLIST_TABLE}}` is not used here -- the table is rendered
    inline, since this module has `scores` in hand already."""
    from pipeline.config import load_watchlist_company_names

    company_names = load_watchlist_company_names()

    parts = [
        f"# TechInves Sector Report — {as_of}",
        "",
        OPENING_DISCLAIMER,
        "",
        HIGHLIGHTS_HEADING,
        "",
    ]
    # The Highlights heading is a container for the `###` deep-dives that
    # follow it, which means it has no body of its own unless one is written
    # -- and the storage layer persists that as a section whose entire body is
    # its own heading (run 20260819T112959-a883d9). Give it a body.
    lead_in = render_highlights_lead_in(
        covered_tickers=list(company_sections),
        zero_yield_tickers=zero_yield_tickers,
        as_of=as_of,
    )
    if lead_in:
        parts += [lead_in, ""]
    for ticker, narrative in company_sections.items():
        parts.append(
            render_company_section(
                ticker, narrative=narrative, scores=scores, company_names=company_names
            )
        )
        parts.append("")

    parts += [
        "## Full Watchlist -- Score Summary",
        "",
        render_watchlist_table(scores, watchlist_tickers),
        "",
    ]

    macro_section = render_macro_section(macro_findings)
    if macro_section:
        parts += ["## Sector & Macro", "", macro_section, ""]

    parts += [
        COVERAGE_NOTES_HEADING,
        "",
        render_coverage_notes(scores, watchlist_tickers, zero_yield_tickers),
        "",
        CLOSING_DISCLAIMER,
    ]
    return "\n".join(parts)
