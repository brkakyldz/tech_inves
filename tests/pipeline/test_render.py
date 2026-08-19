from __future__ import annotations

from pipeline.macro_spine import MacroSpineItem
from pipeline.synthesis.render import (
    UNAVAILABLE,
    apply_degraded_publish_banner,
    apply_highlights_lead_in,
    apply_macro_spine,
    apply_zero_yield_coverage_note,
    build_citation_vocabulary,
    expand_citation_ids,
    expand_watchlist_table,
    fence_bare_score_blocks,
    render_highlights_lead_in,
    render_macro_spine,
    render_watchlist_table,
    resolve_placeholders,
    resolve_placeholders_with_stats,
    strip_ungrounded_citations,
)

SCORED = {
    "NVDA": {
        "composite_score": 74,
        "composite_band": "Good",
        "cohort": "B",
        "sector_percentile": 97,
        "risk_score": 56,
        "coverage_pct": 90.9,
        "warnings": [],
        "categories": [
            {"category_name": "valuation", "score": 59, "weight": 0.25},
            {"category_name": "growth", "score": 92, "weight": 0.2},
        ],
        "risk": {"score": 56, "band": "Adequate", "altman_zone": "Safe", "piotroski_f": 7},
    },
    "MSFT": {
        "composite_score": 61,
        "composite_band": "Moderate",
        "cohort": "A",
        "sector_percentile": 82,
        "risk_score": 40,
        "coverage_pct": 90.9,
        "warnings": ["distress ceiling applied"],
        "categories": [],
        "risk": None,
    },
    "CRM": {"missing": True, "reason": "data unavailable for this run", "cohort": "A"},
}


def test_resolves_placeholder_from_scores():
    text = "NVDA's composite score is {{NVDA.composite_score}}."
    out = resolve_placeholders(text, {"NVDA": {"composite_score": 87.5}}, {})
    assert out == "NVDA's composite score is 87.5."


def test_resolves_placeholder_from_financials():
    text = "Forward P/E: {{NVDA.forward_pe}}"
    out = resolve_placeholders(text, {}, {"NVDA": {"forward_pe": 34.2}})
    assert out == "Forward P/E: 34.2"


def test_missing_field_becomes_unavailable_marker():
    text = "{{NVDA.risk_score}}"
    out = resolve_placeholders(text, {"NVDA": {"composite_score": 87.5}}, {})
    assert out == UNAVAILABLE


def test_missing_ticker_becomes_unavailable_marker():
    text = "{{UNKNOWN.composite_score}}"
    out = resolve_placeholders(text, {"NVDA": {"composite_score": 87.5}}, {})
    assert out == UNAVAILABLE


def test_no_placeholders_returns_text_unchanged():
    text = "Plain text, no placeholders."
    assert resolve_placeholders(text, {}, {}) == text


def test_list_and_bool_values_render_as_prose_not_python_reprs():
    scores = {"NVDA": {"warnings": [], "low_reliability": False}, "MU": {"warnings": ["a", "b"]}}
    assert resolve_placeholders("{{NVDA.warnings}}", scores, {}) == "none"
    assert resolve_placeholders("{{MU.warnings}}", scores, {}) == "a, b"
    assert resolve_placeholders("{{NVDA.low_reliability}}", scores, {}) == "no"


def test_resolves_nested_category_and_risk_fields():
    """The score block the prompt mandates addresses these flat, but
    scores_repository returns them nested -- they must still resolve."""
    text = "{{NVDA.valuation_score}}/{{NVDA.growth_weight}}/{{NVDA.altman_zone}}/{{NVDA.piotroski_f}}"
    assert resolve_placeholders(text, SCORED, {}) == "59/0.2/Safe/7"


def test_watchlist_table_covers_every_ticker_including_unscored():
    table = render_watchlist_table(SCORED, ["NVDA", "MSFT", "CRM", "ARM", "VRT"])
    for ticker in ("NVDA", "MSFT", "CRM", "ARM", "VRT"):
        assert ticker in table, f"{ticker} missing from rendered table"


def test_watchlist_table_groups_by_cohort_and_marks_missing():
    table = render_watchlist_table(SCORED, ["NVDA", "MSFT", "CRM"])
    assert "**Cohort A**" in table and "**Cohort B**" in table
    crm_row = next(line for line in table.splitlines() if line.startswith("| CRM "))
    assert "data unavailable for this run" in crm_row
    assert crm_row.count("--") >= 5


def test_expand_watchlist_table_replaces_marker():
    out = expand_watchlist_table(
        "## Full Watchlist\n\n{{FULL_WATCHLIST_TABLE}}\n", SCORED, ["NVDA", "MSFT", "CRM"]
    )
    assert "{{FULL_WATCHLIST_TABLE}}" not in out
    assert "| NVDA |" in out


def test_expand_watchlist_table_is_a_noop_without_the_marker():
    text = "## Full Watchlist\n\n| Ticker |\n"
    assert expand_watchlist_table(text, SCORED, ["NVDA"]) == text


def test_resolution_stats_counts_resolved_and_unavailable():
    text = "{{NVDA.composite_score}} {{NVDA.risk_score}} {{MSFT.composite_score}}"
    _, stats = resolve_placeholders_with_stats(
        text, {"NVDA": {"composite_score": 74}, "MSFT": {"composite_score": 61}}, {}
    )
    assert stats.resolved == 2
    assert stats.unavailable == 1
    assert stats.resolution_rate == 2 / 3


def test_resolution_stats_flags_unknown_field_as_likely_typo():
    text = "{{NVDA.composite_score}} {{NVDA.compsite_scor}}"
    _, stats = resolve_placeholders_with_stats(
        text, {"NVDA": {"composite_score": 74}, "MSFT": {"risk_score": 40}}, {}
    )
    assert stats.unknown_field == [("NVDA", "compsite_scor")]


def test_resolution_stats_does_not_flag_field_valid_for_another_ticker():
    """A field missing for this ticker but real for another is missing
    *data*, not a typo -- must not land in unknown_field."""
    text = "{{CRM.risk_score}}"
    _, stats = resolve_placeholders_with_stats(
        text, {"CRM": {"composite_score": 50}, "NVDA": {"risk_score": 56}}, {}
    )
    assert stats.unknown_field == []
    assert stats.unavailable == 1


def test_resolution_stats_below_threshold():
    text = "{{NVDA.a}} {{NVDA.b}} {{NVDA.c}} {{NVDA.composite_score}}"
    _, stats = resolve_placeholders_with_stats(text, {"NVDA": {"composite_score": 74}}, {})
    assert stats.resolution_rate == 0.25
    assert stats.below_threshold is True


def test_resolution_stats_empty_text_is_not_below_threshold():
    _, stats = resolve_placeholders_with_stats("no placeholders here", {}, {})
    assert stats.total == 0
    assert stats.below_threshold is False


def test_degraded_publish_banner_inserted_after_title():
    body = "# TechInves Weekly\n\nRest of the report.\n"
    out = apply_degraded_publish_banner(body, ["no company has a deep-dive section"])
    assert out.startswith("# TechInves Weekly\n\n> **Reduced coverage.**")
    assert "no company has a deep-dive section" in out
    assert "Rest of the report." in out


def test_degraded_publish_banner_noop_with_no_gaps():
    body = "# TechInves Weekly\n\nRest.\n"
    assert apply_degraded_publish_banner(body, []) == body


def test_degraded_publish_banner_prepended_without_a_title_heading():
    body = "Rest of the report, no title line.\n"
    out = apply_degraded_publish_banner(body, ["gap"])
    assert out.startswith("> **Reduced coverage.**")
    assert out.endswith(body)


def test_render_macro_spine_table_names_every_item():
    items = [
        MacroSpineItem(series_id="FEDFUNDS", label="Fed Funds Rate", units="%", value=5.33, as_of="2026-08-01"),
        MacroSpineItem(series_id="DGS10", label="10Y Yield", units="%", value=None, as_of=None),
    ]
    table = render_macro_spine(items)
    assert "Fed Funds Rate" in table
    assert "5.33%" in table
    assert "10Y Yield" in table
    assert "n/a" in table


def test_render_macro_spine_empty_is_empty_string():
    assert render_macro_spine([]) == ""


def test_apply_macro_spine_inserts_after_existing_heading():
    body = "# Weekly\n\n## Sector & Macro\n\nSome macro prose.\n"
    items = [MacroSpineItem(series_id="FEDFUNDS", label="Fed Funds", units="%", value=5.33, as_of="2026-08-01")]
    out = apply_macro_spine(body, items)
    assert "## Sector & Macro" in out
    assert "Fed Funds" in out
    assert "Some macro prose." in out


def test_apply_macro_spine_adds_section_when_none_exists():
    body = "# Weekly\n\nNo macro section this week.\n"
    items = [MacroSpineItem(series_id="FEDFUNDS", label="Fed Funds", units="%", value=5.33, as_of="2026-08-01")]
    out = apply_macro_spine(body, items)
    assert "## Sector & Macro" in out
    assert "Fed Funds" in out


_UNFENCED_BLOCK = (
    "COMPOSITE SCORE: 74 (band: Good)\n"
    "  Valuation               : 59 (weight 0.25)\n"
    "  Growth                  : 92 (weight 0.2)\n"
    "RISK INDICATOR: 56 (band: Adequate)\n"
    "  Altman Z-zone: Safe\n"
    "  Piotroski F-Score: 7\n"
    "SECTOR PERCENTILE: 97 (Cohort B)\n"
    "DATA COVERAGE: 90.9 | Warnings applied: []"
)


def test_fence_bare_score_blocks_fences_an_unfenced_block():
    body = f"### NVDA -- NVIDIA\n\nSome narrative prose.\n\n{_UNFENCED_BLOCK}\n"
    out = fence_bare_score_blocks(body)
    assert "```\n" + _UNFENCED_BLOCK + "\n```" in out
    assert "Some narrative prose." in out


def test_fence_bare_score_blocks_leaves_already_fenced_block_unchanged():
    body = f"### NVDA -- NVIDIA\n\n```\n{_UNFENCED_BLOCK}\n```\n"
    assert fence_bare_score_blocks(body) == body


def test_fence_bare_score_blocks_is_idempotent_when_run_twice():
    body = f"### NVDA -- NVIDIA\n\n{_UNFENCED_BLOCK}\n"
    once = fence_bare_score_blocks(body)
    twice = fence_bare_score_blocks(once)
    assert once == twice


def test_fence_bare_score_blocks_fences_multiple_deep_dives():
    other_block = _UNFENCED_BLOCK.replace("74", "61").replace("NVDA", "MSFT")
    body = (
        f"### NVDA -- NVIDIA\n\nProse one.\n\n{_UNFENCED_BLOCK}\n\n"
        f"### MSFT -- Microsoft\n\nProse two.\n\n{other_block}\n"
    )
    out = fence_bare_score_blocks(body)
    assert out.count("```\nCOMPOSITE SCORE") == 2
    assert out.count("DATA COVERAGE") == 2
    assert "Prose one." in out
    assert "Prose two." in out


def test_fence_bare_score_blocks_handles_mixed_fenced_and_unfenced_report():
    body = (
        f"### NVDA -- NVIDIA\n\n```\n{_UNFENCED_BLOCK}\n```\n\n"
        f"### MSFT -- Microsoft\n\n{_UNFENCED_BLOCK}\n"
    )
    out = fence_bare_score_blocks(body)
    # The already-fenced NVDA block is untouched (no double-fencing)...
    assert "```\n```\n" + _UNFENCED_BLOCK not in out
    assert out.count("```\n" + _UNFENCED_BLOCK + "\n```") == 2
    # ...and the previously-bare MSFT block is now fenced too.
    assert out.count("```") == 4


def test_fence_bare_score_blocks_preserves_surrounding_prose():
    body = (
        "# TechInves Weekly -- 2026-08-17\n\n"
        "### NVDA -- NVIDIA\n\nOpening narrative sentence.\n\n"
        f"{_UNFENCED_BLOCK}\n\n"
        "## Full Watchlist -- Score Summary\n\n"
        "| Ticker | Composite |\n|---|---:|\n| NVDA | 74 |\n"
    )
    out = fence_bare_score_blocks(body)
    assert "Opening narrative sentence." in out
    assert "## Full Watchlist -- Score Summary" in out
    assert "| NVDA | 74 |" in out


def test_fence_bare_score_blocks_leaves_truncated_block_untouched():
    body = (
        "### NVDA -- NVIDIA\n\nNarrative.\n\n"
        "COMPOSITE SCORE: 74 (band: Good)\n"
        "  Valuation               : 59 (weight 0.25)\n"
        "\nNo DATA COVERAGE line follows this -- some trailing prose instead.\n"
    )
    assert fence_bare_score_blocks(body) == body


def test_apply_macro_spine_noop_without_items():
    body = "# Weekly\n\nNo macro section.\n"
    assert apply_macro_spine(body, []) == body


RETRIEVED = {
    "https://www.reuters.com/tech/real-story-2026-08-14",
    "https://www.cnbc.com/2026/08/13/another-real-story.html",
}


def test_strip_ungrounded_citations_removes_a_url_never_retrieved():
    text = (
        "Regulatory pressure is broadening. "
        "[Reuters](https://www.reuters.com/business/invented-slug-2026-08-14) "
        "[CNBC](https://www.cnbc.com/2026/08/13/another-real-story.html)"
    )
    out, dropped = strip_ungrounded_citations(text, RETRIEVED)
    assert dropped == ["https://www.reuters.com/business/invented-slug-2026-08-14"]
    assert "invented-slug" not in out
    # the anchor text survives as prose; the grounded link is untouched
    assert "Reuters" in out
    assert "[CNBC](https://www.cnbc.com/2026/08/13/another-real-story.html)" in out


def test_strip_ungrounded_citations_keeps_every_retrieved_url():
    text = "a [x](https://www.reuters.com/tech/real-story-2026-08-14) b"
    out, dropped = strip_ungrounded_citations(text, RETRIEVED)
    assert dropped == []
    assert out == text


def test_strip_ungrounded_citations_leaves_the_internal_methodology_link():
    """The mandated `/legal` link is a document cross-reference, not a
    source claim -- no research branch can ever retrieve it."""
    text = "See the [methodology](/legal) page."
    out, dropped = strip_ungrounded_citations(text, RETRIEVED)
    assert dropped == []
    assert out == text


def test_strip_ungrounded_citations_drops_everything_when_nothing_was_retrieved():
    text = "claim [Reuters](https://www.reuters.com/anything)"
    out, dropped = strip_ungrounded_citations(text, set())
    assert dropped == ["https://www.reuters.com/anything"]
    assert "https://" not in out


def test_build_citation_vocabulary_numbers_sources_in_sorted_order():
    """Stable across runs: the writer is at temperature 0, so a prompt that
    renumbered between runs would be the only source of drift."""
    vocabulary = build_citation_vocabulary(RETRIEVED)
    assert vocabulary == {
        "S1": "https://www.cnbc.com/2026/08/13/another-real-story.html",
        "S2": "https://www.reuters.com/tech/real-story-2026-08-14",
    }
    assert build_citation_vocabulary(set()) == {}
    assert build_citation_vocabulary(None) == {}


def test_expand_citation_ids_turns_markers_into_links():
    vocabulary = build_citation_vocabulary(RETRIEVED)
    text = "A claim [S1] and another [S2]."
    out, unknown = expand_citation_ids(text, vocabulary)
    assert unknown == []
    assert "[S1](https://www.cnbc.com/2026/08/13/another-real-story.html)" in out
    assert "[S2](https://www.reuters.com/tech/real-story-2026-08-14)" in out


def test_expand_citation_ids_reports_an_id_outside_the_vocabulary():
    """An invented id has nothing to expand to. It is reported and left in the
    text verbatim -- the run blocks on it, and the blocked draft a reader sees
    should still show the marker the writer wrote."""
    vocabulary = build_citation_vocabulary(RETRIEVED)
    out, unknown = expand_citation_ids("A fabricated claim [S9].", vocabulary)
    assert unknown == ["S9"]
    assert out == "A fabricated claim [S9]."


def test_expand_citation_ids_sorts_unknown_ids_numerically():
    out, unknown = expand_citation_ids("[S10] [S9] [S10]", {})
    assert unknown == ["S9", "S10"]  # not lexicographic ("S10" < "S9")
    assert out == "[S10] [S9] [S10]"


def test_expand_citation_ids_is_idempotent():
    """Running twice (or on a partially-expanded draft) must not double the
    URL -- the marker is already a link, so it is skipped."""
    vocabulary = build_citation_vocabulary(RETRIEVED)
    once, _ = expand_citation_ids("A claim [S1].", vocabulary)
    twice, unknown = expand_citation_ids(once, vocabulary)
    assert twice == once
    assert unknown == []


# --- Highlights lead-in / zero-yield coverage note --------------------------
#
# Run 20260819T112959-a883d9: the writer emitted "## This Week's Highlights"
# as a bare container for the `###` deep-dives beneath it, and the storage
# layer persisted a section whose entire body was its own heading (25
# characters). Two of the four selected tickers had also returned zero
# findings, which nothing in the report told the reader.

EMPTY_HIGHLIGHTS_DRAFT = (
    "# TechInves Sector Report\n\n"
    "Opening disclaimer.\n\n"
    "## Highlights\n\n"
    "### MSFT -- Microsoft\n\n"
    "Prose.\n\n"
    "## Coverage Notes\n\n"
    "All tickers had usable data for this run.\n"
)


def test_highlights_lead_in_names_the_covered_tickers():
    out = render_highlights_lead_in(covered_tickers=["MSFT", "CRM"], as_of="2026-08-19")
    assert "MSFT" in out and "CRM" in out
    assert "2026-08-19" in out


def test_highlights_lead_in_names_the_zero_yield_tickers_separately():
    out = render_highlights_lead_in(
        covered_tickers=["MSFT"], zero_yield_tickers=["ADBE", "NOW"]
    )
    assert "ADBE" in out and "NOW" in out
    assert "no findings" in out


def test_highlights_lead_in_is_empty_without_any_tickers():
    assert render_highlights_lead_in(covered_tickers=[]) == ""


def test_apply_highlights_lead_in_fills_an_empty_section():
    out = apply_highlights_lead_in(
        EMPTY_HIGHLIGHTS_DRAFT,
        covered_tickers=["MSFT"],
        zero_yield_tickers=["ADBE"],
        as_of="2026-08-19",
    )
    body = out.split("## Highlights", 1)[1].split("### MSFT", 1)[0]
    assert body.strip()
    assert "MSFT" in body and "ADBE" in body


def test_apply_highlights_lead_in_leaves_a_written_lead_in_alone():
    """The deterministic fill is a floor, not a rewrite: a writer that did
    produce a lead-in keeps it."""
    draft = EMPTY_HIGHLIGHTS_DRAFT.replace(
        "## Highlights\n\n", "## Highlights\n\nThe writer's own lead-in.\n\n"
    )
    assert apply_highlights_lead_in(draft, covered_tickers=["MSFT"]) == draft


def test_apply_highlights_lead_in_removes_a_section_it_cannot_fill():
    """An empty-bodied heading must never reach the store -- if there is
    nothing to say under it, the heading goes too."""
    out = apply_highlights_lead_in(EMPTY_HIGHLIGHTS_DRAFT, covered_tickers=[])
    assert "## Highlights" not in out
    assert "### MSFT -- Microsoft" in out


def test_apply_highlights_lead_in_is_idempotent():
    once = apply_highlights_lead_in(
        EMPTY_HIGHLIGHTS_DRAFT, covered_tickers=["MSFT"], zero_yield_tickers=["ADBE"]
    )
    twice = apply_highlights_lead_in(
        once, covered_tickers=["MSFT"], zero_yield_tickers=["ADBE"]
    )
    assert twice == once


def test_zero_yield_note_lands_in_coverage_notes():
    out = apply_zero_yield_coverage_note(EMPTY_HIGHLIGHTS_DRAFT, ["ADBE", "NOW"])
    notes = out.split("## Coverage Notes", 1)[1]
    assert "ADBE" in notes and "NOW" in notes


def test_zero_yield_note_appends_a_section_when_there_is_none():
    body = "# TechInves Sector Report\n\nProse.\n"
    out = apply_zero_yield_coverage_note(body, ["ADBE"])
    assert "## Coverage Notes" in out
    assert "ADBE" in out


def test_zero_yield_note_is_a_no_op_without_zero_yield_tickers():
    assert apply_zero_yield_coverage_note(EMPTY_HIGHLIGHTS_DRAFT, []) == EMPTY_HIGHLIGHTS_DRAFT


def test_zero_yield_note_is_idempotent():
    once = apply_zero_yield_coverage_note(EMPTY_HIGHLIGHTS_DRAFT, ["ADBE"])
    assert apply_zero_yield_coverage_note(once, ["ADBE"]) == once
