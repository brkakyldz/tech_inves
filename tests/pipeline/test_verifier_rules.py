from __future__ import annotations

from pipeline.verifier.rules import (
    check_completeness,
    classify_violations,
    find_absent_watchlist_tickers,
    expected_highlight_range,
    find_citation_violations,
    find_empty_sections,
    find_number_leaks,
    find_uncovered_missing_tickers,
    has_ai_disclosure,
    has_disclaimer,
    missing_low_reliability_labels,
)

SCORES = {
    "NVDA": {"composite_score": 87.5, "risk_score": 62.0, "coverage_pct": 91},
    "PLTR": {"composite_score": 71.0, "risk_score": 48.5, "coverage_pct": 55},
}
FINANCIALS = {"NVDA": {"forward_pe": 34.2}}


def test_number_leak_detects_web_sourced_figure():
    text = "NVDA reportedly grew revenue 42% year over year according to sources."
    leaks = find_number_leaks(text, SCORES, FINANCIALS)
    assert "42%" in leaks


def test_number_leak_allows_scored_values():
    text = "NVDA's composite score is {{NVDA.composite_score}} (87.5) this week."
    leaks = find_number_leaks(text, SCORES, FINANCIALS)
    assert leaks == []


def test_number_leak_ignores_placeholder_and_url_content():
    text = (
        "See {{NVDA.composite_score}} for details. "
        "[source](https://reuters.com/article-2026-week-3)"
    )
    leaks = find_number_leaks(text, SCORES, FINANCIALS)
    assert leaks == []


def test_number_leak_does_not_flag_filing_form_reference():
    text = "NVDA's composite score is {{NVDA.composite_score}}. See its 10-K for detail."
    leaks = find_number_leaks(text, SCORES, FINANCIALS)
    assert leaks == []


def test_number_leak_still_flags_fabricated_number_alongside_filing_reference():
    text = "Per its 10-K, NVDA reportedly grew revenue 42% this quarter."
    leaks = find_number_leaks(text, SCORES, FINANCIALS)
    assert "42%" in leaks
    assert "10" not in leaks


def test_number_leak_does_not_launder_unrelated_digit_via_filing_form_mention():
    """R24's filing-form stripping is positional -- mentioning "20-F" must not
    whitelist an unrelated fabricated "20%" figure elsewhere in the report."""
    text = "As disclosed in its 20-F. Operating margin expanded to 20% this quarter."
    leaks = find_number_leaks(text, SCORES, FINANCIALS)
    assert "20%" in leaks


def test_number_leak_does_not_launder_unrelated_digit_via_quarter_label():
    text = "Guidance was reaffirmed for Q3. Revenue grew 3% year over year."
    leaks = find_number_leaks(text, SCORES, FINANCIALS)
    assert "3%" in leaks


def test_number_leak_does_not_flag_digits_inside_a_metric_identifier():
    """The score-attribution appendix names metrics by engine id; the digits
    in `revenue_cagr_3y`/`rule_of_40_fcf` are part of the name."""
    text = "| revenue_cagr_3y | 0.16 | 57.7 | 0.24 |\n| rule_of_40_fcf | 0.38 | 29.2 | 0.29 |"
    leaks = find_number_leaks(
        text,
        {
            "NVDA": {
                "categories": [
                    {
                        "metrics": [
                            {"raw_value": 0.1612, "percentile": 57.692, "weight_used": 0.24},
                            {"raw_value": 0.3797, "percentile": 29.166, "weight_used": 0.29},
                        ]
                    }
                ]
            }
        },
        {},
    )
    assert leaks == []


def test_number_leak_does_not_launder_a_figure_via_a_metric_identifier():
    """Identifier stripping is positional, like R24's -- naming
    `rule_of_40_fcf` must not whitelist a bare fabricated 40%."""
    text = "rule_of_40_fcf is the metric. Revenue grew 40% this year."
    leaks = find_number_leaks(text, SCORES, FINANCIALS)
    assert "40%" in leaks


def test_citation_violation_for_unretrieved_url():
    text = "Claim here [source](https://fake-site.example/made-up)."
    retrieved = {"https://reuters.com/real-article"}
    violations = find_citation_violations(text, retrieved)
    assert violations == ["https://fake-site.example/made-up"]


def test_citation_ok_for_retrieved_url():
    text = "Claim here [source](https://reuters.com/real-article)."
    retrieved = {"https://reuters.com/real-article"}
    assert find_citation_violations(text, retrieved) == []


def test_number_leak_ignores_dates_and_years():
    """The report's own week_of line is prose, not a fabricated figure."""
    text = "TechInves Weekly -- Week of 2026-08-17. Compared with fiscal 2025."
    assert find_number_leaks(text, SCORES, FINANCIALS) == []


def test_citation_ignores_relative_methodology_link():
    """The synthesis prompt mandates a closing methodology link, which no
    research branch can return -- it must not read as a fabricated citation."""
    text = (
        "Claim here [source](https://reuters.com/real-article). "
        "See the [methodology](report_scoring_metadology.md) for details."
    )
    retrieved = {"https://reuters.com/real-article"}
    assert find_citation_violations(text, retrieved) == []


def test_citation_violation_still_caught_alongside_relative_links():
    text = (
        "[methodology](report_scoring_metadology.md) and "
        "[source](https://fake-site.example/made-up)."
    )
    retrieved = {"https://reuters.com/real-article"}
    assert find_citation_violations(text, retrieved) == ["https://fake-site.example/made-up"]


def test_disclaimer_detection():
    assert has_disclaimer("Text. This is not investment advice.") is True
    assert has_disclaimer("Text without the phrase.") is False


def test_ai_disclosure_detection():
    assert has_ai_disclosure("Narrative sections are drafted by an AI pipeline.") is True
    assert has_ai_disclosure("Text without the phrase.") is False


def test_missing_low_reliability_label():
    text = "PLTR had a strong week."
    missing = missing_low_reliability_labels(text, SCORES)
    assert missing == ["PLTR"]


def test_low_reliability_label_present_clears_flag():
    text = "PLTR (low reliability) had a strong week."
    assert missing_low_reliability_labels(text, SCORES) == []


COMPLETE_REPORT = """# TechInves Weekly

*This report is a screening and ranking tool based on financial-statement \
data. This is not investment advice.*

## Highlights

Deep-dive coverage below: NVDA, PLTR, AMD.

### NVDA -- NVIDIA Corp.

NVDA had a strong week ([reuters.com](https://reuters.com/nvda)).

```
COMPOSITE SCORE: {{NVDA.composite_score}}
RISK INDICATOR: {{NVDA.risk_score}}
SECTOR PERCENTILE: 90th
DATA COVERAGE: 91%
```

### PLTR -- Palantir

PLTR had a mixed week ([reuters.com](https://reuters.com/pltr)) (low reliability).

```
COMPOSITE SCORE: {{PLTR.composite_score}}
RISK INDICATOR: {{PLTR.risk_score}}
SECTOR PERCENTILE: 60th
DATA COVERAGE: 55%
```

### AMD -- Advanced Micro Devices

AMD launched a new chip ([reuters.com](https://reuters.com/amd)).

```
COMPOSITE SCORE: {{AMD.composite_score}}
RISK INDICATOR: {{AMD.risk_score}}
SECTOR PERCENTILE: 70th
DATA COVERAGE: 88%
```

## Full Watchlist -- Score Summary

| Ticker | Composite |
|---|---|
| NVDA | {{NVDA.composite_score}} |
| PLTR | {{PLTR.composite_score}} |
| AMD | {{AMD.composite_score}} |
"""

WATCHLIST = ["NVDA", "PLTR", "AMD"]


def test_check_completeness_passes_on_well_formed_report():
    compliance_hard, structural_hard, soft = check_completeness(
        COMPLETE_REPORT, scores=SCORES, watchlist_tickers=WATCHLIST
    )
    assert compliance_hard == []
    assert structural_hard == []
    assert soft == []


def test_check_completeness_structural_hard_fails_on_absent_watchlist_ticker():
    compliance_hard, structural_hard, soft = check_completeness(
        COMPLETE_REPORT, scores=SCORES, watchlist_tickers=WATCHLIST + ["MSFT"]
    )
    assert any("MSFT" in v for v in structural_hard)


def test_check_completeness_structural_hard_fails_on_uncovered_missing_ticker():
    scores = {**SCORES, "GAP": {"missing": True, "reason": "data unavailable this week"}}
    compliance_hard, structural_hard, soft = check_completeness(
        COMPLETE_REPORT, scores=scores, watchlist_tickers=WATCHLIST
    )
    assert any("GAP" in v for v in structural_hard)


def test_check_completeness_compliance_hard_fails_on_missing_opening_disclaimer():
    text = COMPLETE_REPORT.replace("screening and ranking tool", "something else entirely")
    compliance_hard, structural_hard, soft = check_completeness(
        text, scores=SCORES, watchlist_tickers=WATCHLIST
    )
    assert any("disclaimer" in v for v in compliance_hard)


def test_check_completeness_compliance_hard_fails_on_non_english_text():
    text = COMPLETE_REPORT.replace("Weekly", "Haftalık Rapor")
    compliance_hard, structural_hard, soft = check_completeness(
        text, scores=SCORES, watchlist_tickers=WATCHLIST
    )
    assert any("English" in v for v in compliance_hard)


def test_check_completeness_structural_hard_fails_when_no_deep_dive_sections_exist():
    text = "# Report\n\n*This is a screening and ranking tool. This is not investment advice.*\n\nNVDA PLTR AMD mentioned in prose only."
    compliance_hard, structural_hard, soft = check_completeness(
        text, scores=SCORES, watchlist_tickers=WATCHLIST
    )
    assert any("no company has a deep-dive section" in v for v in structural_hard)


def test_check_completeness_soft_flags_missing_citation():
    text = COMPLETE_REPORT.replace(
        "NVDA had a strong week ([reuters.com](https://reuters.com/nvda)).",
        "NVDA had a strong week.",
    )
    compliance_hard, structural_hard, soft = check_completeness(
        text, scores=SCORES, watchlist_tickers=WATCHLIST
    )
    assert compliance_hard == []
    assert structural_hard == []
    assert any("citation" in v for v in soft)


def test_check_completeness_scales_the_deep_dive_count_to_this_runs_highlights():
    """F8: 3-4 describes a weekly watchlist run. ADR 0010 §1's single-company
    trigger runs the same graph with one highlight, and a fixed range made it
    permanently `pass_with_flags`."""
    from pipeline.verifier.rules import expected_highlight_range

    assert expected_highlight_range(["NVDA"]) == (1, 1)
    assert expected_highlight_range(["NVDA", "AMD"]) == (2, 2)
    # No declared scope falls back to the spec constants rather than
    # disabling the check.
    assert expected_highlight_range([]) == (3, 4)
    assert expected_highlight_range(None) == (3, 4)


def test_check_completeness_flags_fewer_sections_than_the_run_researched():
    text = COMPLETE_REPORT.replace("### AMD -- Advanced Micro Devices", "#### AMD notes")
    compliance_hard, structural_hard, soft = check_completeness(
        text, scores=SCORES, watchlist_tickers=WATCHLIST, highlight_tickers=WATCHLIST
    )
    assert compliance_hard == []
    assert any("2 deep-dive sections (this run expects 3)" in v for v in soft)


def test_check_completeness_accepts_a_single_company_runs_one_section():
    text = (
        "# Report\n\n"
        "*This is a screening and ranking tool. This is not investment advice.*\n\n"
        "### NVDA -- NVIDIA Corp.\n\n"
        "NVDA had a strong week ([reuters.com](https://reuters.com/nvda)).\n\n"
        "```\nCOMPOSITE SCORE: 1\nRISK INDICATOR: 1\nSECTOR PERCENTILE: 1\n"
        "DATA COVERAGE: 1\n```\n"
    )
    compliance_hard, structural_hard, soft = check_completeness(
        text, scores={}, watchlist_tickers=["NVDA"], highlight_tickers=["NVDA"]
    )
    assert compliance_hard == []
    assert structural_hard == []
    assert soft == []


def test_find_citation_id_violations_finds_unexpanded_markers_only():
    from pipeline.verifier.rules import find_citation_id_violations

    text = (
        "An expanded one [S1](https://reuters.com/nvda), an invented one [S9], "
        "and another [S10]."
    )
    # numeric, not lexicographic, ordering
    assert find_citation_id_violations(text) == ["S9", "S10"]
    assert find_citation_id_violations("nothing to see here") == []


def test_number_leaks_ignore_citation_marker_indices():
    """A citation marker's `<n>` is a vocabulary index, not a figure. The
    link-stripping keeps anchor text, so an expanded `[S1](https://...)` left
    a bare "1" behind and every cited sentence scanned as a fabricated
    number."""
    from pipeline.verifier.rules import find_number_leaks

    text = "NVDA shipped a chip [S1](https://reuters.com/nvda) and another [S12]."
    assert find_number_leaks(text, {}, {}) == []


def test_find_citation_violations_ignores_whitespace_padding():
    """F12: the strip compares the *stripped* URL, so a padded-but-grounded
    link survived it and then tripped the verifier -- a compliance_hard block
    on a legitimate citation."""
    from pipeline.verifier.rules import find_citation_violations

    text = "a [x]( https://reuters.com/nvda ) b"
    assert find_citation_violations(text, {"https://reuters.com/nvda"}) == []


def test_classify_violations_citation_id_is_compliance_hard():
    from pipeline.verifier.rules import classify_violations

    violations = classify_violations(
        COMPLETE_REPORT,
        watchlist_tickers=WATCHLIST,
        number_leaks=[],
        citation_violations=[],
        citation_id_violations=["S9"],
        missing_disclaimer=False,
        missing_ai_disclosure=False,
        compliance_hard_completeness=[],
        structural_hard_completeness=[],
        soft_completeness=[],
        missing_labels=[],
    )
    assert [v.severity for v in violations] == ["compliance_hard"]
    assert violations[0].category == "citation"
    assert "[S9]" in violations[0].message


def test_classify_violations_number_leak_is_compliance_hard_and_section_scoped():
    violations = classify_violations(
        COMPLETE_REPORT,
        watchlist_tickers=WATCHLIST,
        number_leaks=["999"],
        citation_violations=[],
        missing_disclaimer=False,
        missing_ai_disclosure=False,
        compliance_hard_completeness=[],
        structural_hard_completeness=[],
        soft_completeness=[],
        missing_labels=[],
    )
    assert len(violations) == 1
    v = violations[0]
    assert v.severity == "compliance_hard"
    assert v.category == "number_leak"
    assert v.section is None  # "999" appears nowhere in COMPLETE_REPORT


def test_classify_violations_number_leak_attributed_to_containing_section():
    violations = classify_violations(
        COMPLETE_REPORT,
        watchlist_tickers=WATCHLIST,
        number_leaks=["strong"],  # deliberately a substring that IS in the NVDA section
        citation_violations=[],
        missing_disclaimer=False,
        missing_ai_disclosure=False,
        compliance_hard_completeness=[],
        structural_hard_completeness=[],
        soft_completeness=[],
        missing_labels=[],
    )
    assert violations[0].section == "NVDA"


def test_classify_violations_structural_completeness_is_structural_hard():
    violations = classify_violations(
        COMPLETE_REPORT,
        watchlist_tickers=WATCHLIST,
        number_leaks=[],
        citation_violations=[],
        missing_disclaimer=False,
        missing_ai_disclosure=False,
        compliance_hard_completeness=[],
        structural_hard_completeness=["watchlist tickers absent from report: GAP"],
        soft_completeness=[],
        missing_labels=[],
    )
    assert violations[0].severity == "structural_hard"
    assert violations[0].category == "completeness"


def test_classify_violations_compliance_completeness_is_compliance_hard():
    """Opening-disclaimer/non-English completeness violations are
    compliance_hard, not structural_hard -- REPORT_SPEC.md §10.1 lists them
    as always-block, unlike an absent ticker (structural_hard, degradable)."""
    violations = classify_violations(
        COMPLETE_REPORT,
        watchlist_tickers=WATCHLIST,
        number_leaks=[],
        citation_violations=[],
        missing_disclaimer=False,
        missing_ai_disclosure=False,
        compliance_hard_completeness=["opening disclaimer sentence missing"],
        structural_hard_completeness=[],
        soft_completeness=[],
        missing_labels=[],
    )
    assert violations[0].severity == "compliance_hard"
    assert violations[0].category == "completeness"


def test_classify_violations_disclaimer_is_compliance_hard_report_wide():
    violations = classify_violations(
        COMPLETE_REPORT,
        watchlist_tickers=WATCHLIST,
        number_leaks=[],
        citation_violations=[],
        missing_disclaimer=True,
        missing_ai_disclosure=False,
        compliance_hard_completeness=[],
        structural_hard_completeness=[],
        soft_completeness=[],
        missing_labels=[],
    )
    assert violations[0].severity == "compliance_hard"
    assert violations[0].section is None


def test_classify_violations_ai_disclosure_is_compliance_hard_report_wide():
    violations = classify_violations(
        COMPLETE_REPORT,
        watchlist_tickers=WATCHLIST,
        number_leaks=[],
        citation_violations=[],
        missing_disclaimer=False,
        missing_ai_disclosure=True,
        compliance_hard_completeness=[],
        structural_hard_completeness=[],
        soft_completeness=[],
        missing_labels=[],
    )
    assert violations[0].severity == "compliance_hard"
    assert violations[0].category == "disclaimer"
    assert violations[0].section is None


## -- Mutation suite: each detector must actually *fire* on the exact defect
## it claims to catch, not merely run without crashing
## (reports/backlog/verifier-checks-are-degenerate.md, "what closing this
## looks like"). Each test below constructs a report where a ticker is
## genuinely absent, or present only as a substring of another word, and
## asserts the detector fires.


def test_find_absent_watchlist_tickers_fires_on_genuinely_absent_ticker():
    """Recall check: a ticker that never appears anywhere in the report must
    be reported absent."""
    text = "NVDA and AMD both had a strong week. No mention of the third one."
    absent = find_absent_watchlist_tickers(text, ["NVDA", "AMD", "MSFT"])
    assert absent == ["MSFT"]


def test_find_absent_watchlist_tickers_does_not_false_positive_on_substring():
    """Precision check, short tickers that are also common English
    substrings: "ON" inside "condition", "IT" inside "with", "AI" inside
    "said", "NOW" inside "know" must NOT count as the ticker being present --
    the pre-fix `t not in report_text` substring check passed all of these
    trivially."""
    text = (
        "Under this condition, cloud spend rose sharply. "
        "Investors agreed with the outlook. "
        "The CFO said margins would hold. "
        "Analysts know demand is strong this quarter."
    )
    absent = find_absent_watchlist_tickers(text, ["ON", "IT", "AI", "NOW"])
    assert sorted(absent) == ["AI", "IT", "NOW", "ON"]


def test_find_absent_watchlist_tickers_true_positive_for_short_ticker_as_whole_word():
    """The word-boundary fix must not become a false negative in the other
    direction -- a short ticker genuinely present as its own token/symbol
    (not just inside a longer word) must still be found present."""
    text = "ON Semiconductor rallied, and NOW (ServiceNow) also gained; AI spend guidance was raised."
    absent = find_absent_watchlist_tickers(text, ["ON", "NOW", "AI"])
    assert absent == []


def test_find_absent_watchlist_tickers_true_positive_around_punctuation():
    """Tickers commonly appear inside markdown tables/parens/commas -- `\\b`
    must anchor correctly against non-word punctuation on both sides, not
    just whitespace."""
    text = "| Ticker | Composite |\n|---|---|\n| NOW | 84 |\n(AI) posted gains, ON up 2%."
    absent = find_absent_watchlist_tickers(text, ["NOW", "AI", "ON"])
    assert absent == []


def test_find_uncovered_missing_tickers_fires_on_genuinely_unnamed_ticker():
    scores = {"NVDA": {"missing": True, "reason": "data unavailable this week"}}
    text = "This week's report covers AMD and MSFT only."
    assert find_uncovered_missing_tickers(text, scores) == ["NVDA"]


def test_find_uncovered_missing_tickers_does_not_false_positive_on_substring():
    """A missing ticker "ON" must not be cleared just because "ON" occurs
    inside "condition" in the Coverage Notes prose -- it must be named as its
    own token."""
    scores = {"ON": {"missing": True, "reason": "data unavailable this week"}}
    text = "Under this condition, no other watchlist tickers were affected this week."
    assert find_uncovered_missing_tickers(text, scores) == ["ON"]


def test_find_uncovered_missing_tickers_true_positive_when_named_as_a_word():
    scores = {"ON": {"missing": True, "reason": "data unavailable this week"}}
    text = "ON: data unavailable this week (insufficient EDGAR coverage)."
    assert find_uncovered_missing_tickers(text, scores) == []


def test_check_completeness_flags_a_smoke_runs_narrow_subset_against_the_real_watchlist():
    """Regression test for the false-negative generator itself: completeness
    must be checked against the real/full watchlist, not the run's own
    researched subset. A report that only ever mentions the smoke run's
    tickers must show every *other* watchlist ticker as absent, not pass by
    construction."""
    real_watchlist = ["AAPL", "NVDA", "MSFT", "GOOGL"]
    smoke_run_report = COMPLETE_REPORT  # only ever mentions NVDA/PLTR/AMD
    compliance_hard, structural_hard, soft = check_completeness(
        smoke_run_report, scores=SCORES, watchlist_tickers=real_watchlist
    )
    joined = " ".join(structural_hard)
    assert "AAPL" in joined
    assert "MSFT" in joined
    assert "GOOGL" in joined


def test_classify_violations_soft_items_stay_soft():
    violations = classify_violations(
        COMPLETE_REPORT,
        watchlist_tickers=WATCHLIST,
        number_leaks=[],
        citation_violations=[],
        missing_disclaimer=False,
        missing_ai_disclosure=False,
        compliance_hard_completeness=[],
        structural_hard_completeness=[],
        soft_completeness=["3 deep-dive sections (spec requires 3-8)"],
        missing_labels=["PLTR"],
    )
    assert all(v.severity == "soft" for v in violations)
    assert any(v.section == "PLTR" for v in violations)


# --- Empty sections / yield-scaled deep-dive expectation --------------------


def test_find_empty_sections_flags_a_heading_with_no_body():
    """Run 20260819T112959-a883d9's defect: the writer used the Highlights
    heading as a bare container for the deep-dives beneath it, and the storage
    layer persisted a section whose whole body was its own heading."""
    text = "## Highlights\n\n### NVDA -- NVIDIA\n\nProse.\n"
    assert find_empty_sections(text) == ["Highlights"]


def test_find_empty_sections_accepts_a_heading_with_a_lead_in():
    text = "## Highlights\n\nDeep-dive coverage below: NVDA.\n\n### NVDA -- NVIDIA\n\nProse.\n"
    assert find_empty_sections(text) == []


def test_find_empty_sections_flags_a_trailing_empty_heading():
    assert find_empty_sections("## Coverage Notes\n\nNotes.\n\n## Appendix\n\n  \n") == ["Appendix"]


def test_check_completeness_reports_an_empty_section_as_soft():
    text = COMPLETE_REPORT.replace(
        "Deep-dive coverage below: NVDA, PLTR, AMD.\n\n", ""
    )
    _compliance, _structural, soft = check_completeness(
        text, scores=SCORES, watchlist_tickers=WATCHLIST
    )
    assert any("empty body" in v for v in soft)


def test_expected_highlight_range_excludes_zero_yield_tickers():
    """A ticker selected by news volume whose research branch returned nothing
    has no material for a deep-dive. Counting it made run
    20260819T112959-a883d9 `pass_with_flags` for a research-yield fact the
    synthesis layer could do nothing about."""
    assert expected_highlight_range(
        ["MSFT", "CRM", "ADBE", "NOW"], tickers_with_findings={"MSFT", "CRM"}
    ) == (2, 2)


def test_expected_highlight_range_keeps_the_selection_when_nothing_yielded():
    """Scaling the bar to zero would call a run that wrote no deep-dive at all
    complete."""
    assert expected_highlight_range(["MSFT", "CRM"], tickers_with_findings=set()) == (2, 2)


def test_expected_highlight_range_unchanged_without_yield_information():
    assert expected_highlight_range(["MSFT", "CRM"]) == (2, 2)
    assert expected_highlight_range([]) == (3, 4)


def test_check_completeness_does_not_flag_a_deep_dive_a_branch_had_no_findings_for():
    """The three-section report is complete when only three of four selected
    tickers yielded findings."""
    _compliance, _structural, soft = check_completeness(
        COMPLETE_REPORT,
        scores=SCORES,
        watchlist_tickers=WATCHLIST,
        highlight_tickers=["NVDA", "PLTR", "AMD", "MSFT"],
        tickers_with_findings={"NVDA", "PLTR", "AMD"},
    )
    assert not any("deep-dive sections" in v for v in soft)
