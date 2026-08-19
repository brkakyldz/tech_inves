from __future__ import annotations

from pipeline.schemas import Finding
from pipeline.synthesis.stitcher import (
    render_coverage_notes,
    render_macro_section,
    render_score_block,
    stitch_report,
)
from pipeline.verifier.rules import SCORE_BLOCK_MARKERS, find_deep_dive_sections

SCORES = {
    "NVDA": {
        "composite_score": 87.5,
        "composite_band": "Strong",
        "cohort": "B",
        "sector_percentile": 97,
        "risk_score": 62.0,
        "risk_band": "Adequate",
        "coverage_pct": 91,
        "warnings": [],
        "categories": [{"category_name": "valuation", "score": 59, "weight": 0.25}],
        "risk": {"altman_zone": "Safe", "piotroski_f": 7},
    },
    "PLTR": {
        "composite_score": 71.0,
        "cohort": "A",
        "coverage_pct": 55,
    },
}


def test_render_score_block_contains_all_required_markers():
    block = render_score_block("NVDA", SCORES)
    for marker in SCORE_BLOCK_MARKERS:
        assert marker in block
    assert "87.5" in block
    assert block.startswith("```")
    assert block.endswith("```")


def test_render_score_block_missing_ticker_uses_placeholders_not_crash():
    block = render_score_block("UNKNOWN", SCORES)
    assert "COMPOSITE SCORE: --" in block


def test_render_coverage_notes_all_usable():
    notes = render_coverage_notes(SCORES, ["NVDA", "PLTR"])
    assert "usable data" in notes


def test_render_coverage_notes_names_missing_tickers():
    scores = dict(SCORES, GAP={"missing": True})
    notes = render_coverage_notes(scores, ["NVDA", "PLTR", "GAP"])
    assert "GAP" in notes


def test_render_macro_section_empty_without_findings():
    assert render_macro_section([]) == ""


def test_render_macro_section_bullets_each_finding():
    findings = [
        Finding(
            scope="macro",
            topic="Fed policy",
            event_title="Rates held",
            event_type="macro",
            narrative="The Fed held rates steady.",
            source_urls=["https://reuters.com/fed"],
        )
    ]
    section = render_macro_section(findings)
    assert "Fed policy" in section
    assert "reuters.com" in section


def test_stitch_report_produces_a_deep_dive_detected_by_the_verifier(monkeypatch):
    monkeypatch.setattr(
        "pipeline.config.load_watchlist_company_names",
        lambda: {"NVDA": "NVIDIA Corp.", "PLTR": "Palantir"},
    )
    report = stitch_report(
        as_of="2026-08-10",
        company_sections={"NVDA": "NVDA had a strong week.", "PLTR": "PLTR was mixed."},
        macro_findings=[],
        scores=SCORES,
        watchlist_tickers=["NVDA", "PLTR"],
    )

    sections = find_deep_dive_sections(report, ["NVDA", "PLTR"])
    assert set(sections) == {"NVDA", "PLTR"}
    assert "NVDA had a strong week." in sections["NVDA"]
    assert all(marker in sections["NVDA"] for marker in SCORE_BLOCK_MARKERS)


def test_stitch_report_includes_disclaimers_and_watchlist_table():
    report = stitch_report(
        as_of="2026-08-10",
        company_sections={"NVDA": "n"},
        macro_findings=[],
        scores=SCORES,
        watchlist_tickers=["NVDA", "PLTR"],
    )
    assert "not investment advice" in report.lower()
    assert "Full Watchlist" in report
    assert "PLTR" in report  # present in table even without a deep-dive


def test_stitch_report_gives_the_highlights_section_a_body():
    """A `##` heading with only `###` headings under it is persisted as a
    section whose body is its own heading (run 20260819T112959-a883d9)."""
    report = stitch_report(
        as_of="2026-08-10",
        company_sections={"NVDA": "n"},
        macro_findings=[],
        scores=SCORES,
        watchlist_tickers=["NVDA", "PLTR"],
    )
    body = report.split("## Highlights", 1)[1].split("### NVDA", 1)[0]
    assert body.strip()
    assert "NVDA" in body


def test_stitch_report_headings_carry_no_weekly_language():
    report = stitch_report(
        as_of="2026-08-10",
        company_sections={"NVDA": "n"},
        macro_findings=[],
        scores=SCORES,
        watchlist_tickers=["NVDA", "PLTR"],
    )
    assert "# TechInves Sector Report — 2026-08-10" in report
    assert "## Highlights" in report and "## Coverage Notes" in report
    assert "Weekly" not in report and "This Week" not in report


def test_stitch_report_names_zero_yield_tickers_in_coverage_notes():
    report = stitch_report(
        as_of="2026-08-10",
        company_sections={"NVDA": "n"},
        macro_findings=[],
        scores=SCORES,
        watchlist_tickers=["NVDA", "PLTR"],
        zero_yield_tickers=["ADBE", "NOW"],
    )
    notes = report.split("## Coverage Notes", 1)[1]
    assert "ADBE" in notes and "NOW" in notes


def test_render_coverage_notes_names_zero_yield_tickers_even_with_no_data_gaps():
    notes = render_coverage_notes(SCORES, ["NVDA", "PLTR"], ["ADBE"])
    assert "usable data" in notes
    assert "ADBE" in notes


def test_stitched_report_has_no_empty_sections():
    from pipeline.verifier.rules import find_empty_sections

    report = stitch_report(
        as_of="2026-08-10",
        company_sections={"NVDA": "n"},
        macro_findings=[],
        scores=SCORES,
        watchlist_tickers=["NVDA", "PLTR"],
        zero_yield_tickers=["ADBE"],
    )
    assert find_empty_sections(report) == []
