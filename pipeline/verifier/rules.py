"""Rule-based (LLM-free) verifier pre-screen (ARCHITECTURE_PROPOSAL.md §2.2f /
§5.2): number-leak scan, citation-fidelity check, disclaimer check, low-
reliability labeling check, and the REPORT_SPEC.md §10 completeness
predicate. Cheap and deterministic -- runs before the LLM consistency layer
and any violation here forces `block` regardless of what the LLM layer says.

Runs on the *unresolved* draft (before `pipeline/synthesis/render.py`
substitutes `{{ticker.field}}` placeholders -- see `pipeline/graph.py`'s
node ordering), so completeness checks here are limited to what's
observable pre-resolution: ticker/heading presence, section structure,
citation presence, disclaimer wording, language. `resolve_placeholders()`
always fully substitutes every well-formed placeholder by construction, so
"no unresolved placeholder" (REPORT_SPEC.md §10 item 3) is a property of
the render step, not something this module re-checks.
"""

from __future__ import annotations

import re

from pipeline.schemas import VerifierViolation

LOW_RELIABILITY_COVERAGE_THRESHOLD = 60
DISCLAIMER_PHRASE = "not investment advice"
OPENING_DISCLAIMER_PHRASE = "screening and ranking tool"
# REPORT_SPEC.md §3 (R25): the AI-generation disclosure is a distinct,
# mandatory requirement from the investment-advice disclaimer above -- both
# must be present. Checked as a substring, same approach as DISCLAIMER_PHRASE.
AI_DISCLOSURE_PHRASE = "drafted by an ai"
LOW_RELIABILITY_LABEL = "low reliability"

# ADR 0006 §2 / R11: 3-8 -> 3-4 -- deep-dive sections are now a small,
# high-signal set (mention-count pre-selection, pipeline/research/
# highlight_selection.py) rather than a near-full-watchlist roundup.
COMPLETENESS_MIN_HIGHLIGHTS = 3
COMPLETENESS_MAX_HIGHLIGHTS = 4
SCORE_BLOCK_MARKERS = (
    "COMPOSITE SCORE",
    "RISK INDICATOR",
    "SECTOR PERCENTILE",
    "DATA COVERAGE",
)
# Characters that don't occur in English prose but do in Turkish -- a cheap,
# high-precision (if low-recall) proxy for REPORT_SPEC.md §9's English-only
# rule. Not a general language detector; catches exactly the failure mode
# already seen in production (a Turkish title on an English body).
NON_ENGLISH_CHARS = set("ğşıİöüçĞŞÖÜÇ")

PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")
# Dates and bare years are prose, not financial figures. Without this, the
# report's own "As of: 2026-08-17" line scans as three fabricated numbers
# (2026, -08, -17) and hard-blocks the run -- see the 2026-08-17 run.
DATE_RE = re.compile(r"\b(?:19|20)\d{2}(?:-\d{2}-\d{2})?\b")
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\((?P<url>[^)]+)\)")
# A citation-vocabulary marker (`[S3]`) still in its unexpanded form. After
# `pipeline/synthesis/render.expand_citation_ids` has run, every id the writer
# was actually given is a markdown link (`[S3](https://...)`), so anything
# still bare here is an id that expanded to nothing -- i.e. one the writer
# invented. Mirrors `render.CITATION_ID_RE`; duplicated for the same reason
# `find_deep_dive_sections` duplicates the heading split (this module stays
# checkable against the draft text alone).
UNEXPANDED_CITATION_ID_RE = re.compile(r"\[(S\d+)\](?!\()")
# Any citation marker, expanded or not. Its `<n>` is an index into the
# retrieved-source vocabulary, not a financial figure -- see
# `_strip_placeholders_and_links`.
CITATION_MARKER_RE = re.compile(r"\[S\d+\]")
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?%?")
HEADING_WITH_TICKER_RE = re.compile(r"^#{2,3}\s+([A-Z]{1,6})\b", re.MULTILINE)
# Any `##`/`###` heading and its text. Same levels
# `pipeline/storage/report_store.split_into_sections()` turns into stored
# section rows, so "section" means the same thing in the check below as it
# does in the database. Duplicated rather than imported for the same reason
# `find_deep_dive_sections` duplicates the split: this module stays checkable
# against draft text alone, with no storage-layer dependency.
SECTION_HEADING_RE = re.compile(r"^(#{2,3})[ \t]+(.+?)[ \t]*$", re.MULTILINE)

# Cache of compiled word-boundary ticker patterns -- `find_absent_watchlist_
# tickers`/`find_uncovered_missing_tickers` run one regex search per ticker
# per report, so avoid recompiling the same pattern across calls in one run.
_TICKER_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def _ticker_pattern(ticker: str) -> re.Pattern[str]:
    """Word-boundary pattern for one ticker symbol.

    `\\b` is a transition between a word char (`[A-Za-z0-9_]`) and a
    non-word char (or string edge); it does not care what's *inside* the
    matched span. A ticker made only of letters (every entry in
    `data/watchlist.yaml` today) starts and ends on a word char, so `\\b`
    anchors correctly at both ends regardless of what punctuation
    surrounds it in prose ("NOW," / "(NOW)" / "NOW's").

    A ticker containing `.` or `-` (e.g. a hypothetical "BF.B") still works:
    those characters are non-word, but they sit in the *middle* of the
    pattern, not at a boundary-checked edge -- `\\bBF\\.B\\b` still requires
    a word/non-word transition immediately before the leading "B" and
    immediately after the trailing "B", which is exactly the check needed
    to reject "BF.Bx" while accepting "BF.B,". `re.escape` is required here
    only so `.`/`-` are matched literally rather than as regex metacharacters
    -- it does not change boundary behaviour.
    """
    pattern = _TICKER_PATTERN_CACHE.get(ticker)
    if pattern is None:
        pattern = re.compile(rf"\b{re.escape(ticker)}\b")
        _TICKER_PATTERN_CACHE[ticker] = pattern
    return pattern


def _ticker_present(ticker: str, text: str) -> bool:
    """Whether `ticker` appears in `text` as a whole word/symbol, not merely
    as a substring of a longer token (REPORT_SPEC.md §10 item 1 / §6:
    `t not in report_text` previously matched "ON" inside "reasON", "IT"
    inside "with", "AI" inside "said", "NOW" inside "know")."""
    return _ticker_pattern(ticker).search(text) is not None


# R24: ordinary prose numerals that happen to contain a digit but aren't a
# financial figure -- a filing-form name ("10-K") or a quarter label
# ("Q3") -- previously hard-blocked a spec-compliant report the same way a
# genuinely fabricated number would. Extracted from the report text itself
# (not a fixed list), since these can appear anywhere in prose.
FILING_FORM_RE = re.compile(r"\b(10-K|10-Q|8-K|6-K|20-F|S-1)\b", re.IGNORECASE)
QUARTER_LABEL_RE = re.compile(r"\bQ[1-4]\b", re.IGNORECASE)
# Same idea for a metric identifier: a report that names a metric by its
# engine id (`revenue_cagr_3y`, `rule_of_40_fcf`) carries digits that are
# part of the name,
# not figures. Restricted to lower-case snake_case tokens (at least one
# underscore), which no English prose figure looks like, and stripped
# positionally so it can't launder a fabricated "40%" elsewhere.
METRIC_IDENTIFIER_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def _strip_placeholders_and_links(text: str) -> str:
    text = PLACEHOLDER_RE.sub(" ", text)
    text = MARKDOWN_LINK_RE.sub(lambda m: m.group(0).split("](")[0] + "](URL)", text)
    # F5: a citation marker's number is a vocabulary index, not a figure. The
    # link substitution above keeps the *anchor text*, so an expanded citation
    # (`[S1](https://...)`) leaves a bare "1" behind and every cited sentence
    # in a spec-compliant report scans as a fabricated number. Stripped
    # positionally (only the marker itself), same as the filing-form and
    # quarter-label cases below, so it cannot launder an unrelated figure.
    text = CITATION_MARKER_RE.sub(" ", text)
    text = DATE_RE.sub(" ", text)
    # R24: a filing-form name ("10-K") or a quarter label ("Q3") contains a
    # digit that isn't a financial figure. Stripped positionally -- only the
    # digit inside the matched form/label text is removed -- rather than via
    # a report-wide allowlist, so mentioning "20-F" elsewhere in the report
    # can't launder an unrelated fabricated "20%" figure.
    text = FILING_FORM_RE.sub(" ", text)
    text = QUARTER_LABEL_RE.sub(" ", text)
    text = METRIC_IDENTIFIER_RE.sub(" ", text)
    return text


def _flatten_numbers(*blocks: dict) -> set[str]:
    """All numeric values (as normalized strings) found anywhere in the given
    scores/financials dicts -- the only numbers a report is allowed to
    contain."""
    numbers: set[str] = set()

    def walk(value) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, (list, tuple, set)):
            for v in value:
                walk(v)
        elif isinstance(value, bool):
            return
        elif isinstance(value, (int, float)):
            numbers.add(str(value))
            numbers.add(f"{value:.1f}")
            numbers.add(f"{value:.2f}")
            numbers.add(str(int(value)) if float(value).is_integer() else str(value))

    for block in blocks:
        walk(block)
    return numbers


def find_number_leaks(
    report_text: str, scores: dict, financials: dict, *, allowed_extra: set[str] | None = None
) -> list[str]:
    """Return the list of numeric tokens in report_text that don't match any
    value in scores/financials. Placeholders ({{...}}) and URLs are excluded
    from the scan since they're not narrative prose."""

    allowed = _flatten_numbers(scores, financials)
    if allowed_extra:
        allowed |= allowed_extra

    scanned = _strip_placeholders_and_links(report_text)
    violations = []
    for match in NUMBER_RE.finditer(scanned):
        token = match.group(0)
        bare = token.rstrip("%")
        if bare in allowed or token in allowed:
            continue
        violations.append(token)
    return violations


def find_citation_violations(report_text: str, retrieved_urls: set[str]) -> list[str]:
    """External URLs cited in the report that were never returned by a
    research branch -- a fabricated or mismatched citation.

    Only http(s) links are citations. A relative/internal link is a document
    cross-reference, not a source claim: the synthesis prompt itself requires
    a closing "methodology link" (to report_scoring_metadology.md), which no
    research branch can ever return. Counting that as a fabricated citation
    made every spec-compliant report block -- see the 2026-08-17 run.
    """
    cited = {
        m.group("url").strip()
        for m in MARKDOWN_LINK_RE.finditer(report_text)
        if m.group("url").strip().lower().startswith(("http://", "https://"))
    }
    return sorted(cited - retrieved_urls)


def find_citation_id_violations(report_text: str) -> list[str]:
    """Citation ids (`[S7]`) still unexpanded in the draft -- ids the writer
    invented, since every id in the closed vocabulary it was handed became a
    markdown link in `pipeline/synthesis/render.expand_citation_ids`.

    Text-only, so it holds for any caller that hands this module a draft
    without the synthesis node's state. `run_rule_based_prescreen`
    (pipeline/verifier/node.py) unions this with the ids the expansion step
    itself recorded on `fabricated_citation_ids`, which is the authoritative
    pre-strip evidence -- this is the backstop for a draft that reached the
    verifier some other way.
    """
    return sorted(
        {m.group(1) for m in UNEXPANDED_CITATION_ID_RE.finditer(report_text)},
        key=lambda cid: (int(cid[1:]), cid),
    )


def has_disclaimer(report_text: str) -> bool:
    return DISCLAIMER_PHRASE in report_text.lower()


def has_ai_disclosure(report_text: str) -> bool:
    return AI_DISCLOSURE_PHRASE in report_text.lower()


def missing_low_reliability_labels(report_text: str, scores: dict) -> list[str]:
    """Tickers below the coverage threshold whose report is missing the
    "low reliability" label anywhere in the text."""
    if LOW_RELIABILITY_LABEL in report_text.lower():
        return []
    return [
        ticker
        for ticker, block in scores.items()
        if isinstance(block, dict)
        and block.get("coverage_pct") is not None
        and block["coverage_pct"] < LOW_RELIABILITY_COVERAGE_THRESHOLD
    ]


def find_missing_tickers(scores: dict) -> list[str]:
    """Tickers with no score row for this run (W2's `missing` marker)."""
    return sorted(
        ticker
        for ticker, block in scores.items()
        if isinstance(block, dict) and block.get("missing")
    )


def find_uncovered_missing_tickers(report_text: str, scores: dict) -> list[str]:
    """Missing tickers (REPORT_SPEC.md §6) that aren't named anywhere in the
    report text -- the "never silently omitted" rule.

    Word-boundary match, not substring: a bare `t not in report_text` check
    passes trivially for a short ticker like "ON" or "IT" the moment it
    occurs inside an ordinary English word ("reasON", "w-IT-h"), which is a
    false pass -- the ticker was never actually named."""
    return [t for t in find_missing_tickers(scores) if not _ticker_present(t, report_text)]


def find_absent_watchlist_tickers(report_text: str, watchlist_tickers: list[str]) -> list[str]:
    """Watchlist tickers that don't appear anywhere in the report text --
    REPORT_SPEC.md §10 item 1: every ticker must be in the Full Watchlist
    table (or, if missing for this run, in Coverage Notes -- either way, the
    ticker string must appear).

    Word-boundary match (see `_ticker_present`): short tickers like "ON",
    "IT", "AI", "NOW" previously passed by matching inside ordinary English
    prose rather than actually appearing as a ticker."""
    return [t for t in watchlist_tickers if not _ticker_present(t, report_text)]


def find_deep_dive_sections(report_text: str, watchlist_tickers: list[str]) -> dict[str, str]:
    """Best-effort split of the draft into per-company chunks, keyed by
    ticker, using the same `##`/`###` heading convention
    `pipeline/storage/report_store.split_into_sections()` uses. Duplicated
    rather than imported to keep this module dependency-free of the storage
    layer; detects "which tickers got a deep-dive section" directly from
    document structure rather than requiring a separately-tracked highlight
    selection, which isn't available yet at verifier time (§ module
    docstring)."""
    watchlist = set(watchlist_tickers)
    matches = list(HEADING_WITH_TICKER_RE.finditer(report_text))
    sections: dict[str, str] = {}
    for i, m in enumerate(matches):
        ticker = m.group(1)
        if ticker not in watchlist:
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        sections[ticker] = report_text[start:end]
    return sections


def find_empty_sections(report_text: str) -> list[str]:
    """Headings with nothing under them -- the section body is empty or
    whitespace-only up to the next `##`/`###` heading.

    Run 20260819T112959-a883d9 shipped "This Week's Highlights" as a section
    whose entire stored body was its own heading: the writer treated it as a
    container for the deep-dives beneath it, `split_into_sections` stored the
    container as a row anyway, and nothing in this module looked at whether a
    heading had a body. A reader of the site sees a titled, empty block; the
    verifier called the run `pass_with_flags` for unrelated reasons and never
    mentioned it.

    Soft, not hard: an empty section is a quality defect in a report whose
    numbers and citations may be perfectly sound, and hard-blocking a run over
    a stray heading would discard the whole spend. The synthesis layer fills
    or removes the section deterministically
    (`pipeline/synthesis/render.apply_highlights_lead_in`); this is the check
    that says so when it did not.
    """
    matches = list(SECTION_HEADING_RE.finditer(report_text))
    empty: list[str] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        if not report_text[start:end].strip():
            empty.append(match.group(2).strip())
    return empty


def expected_highlight_range(
    highlight_tickers: list[str] | None,
    *,
    tickers_with_findings: set[str] | list[str] | None = None,
) -> tuple[int, int]:
    """How many deep-dive sections this run's draft is expected to have.

    The spec's 3-4 (`COMPLETENESS_MIN/MAX_HIGHLIGHTS`, ADR 0006 §2) describes
    a full-watchlist run, whose highlight selection is that size by
    construction. ADR 0010 §1's single-company action runs the same graph
    narrowed to one ticker (`highlight_tickers=[ticker]`), so a fixed 3-4
    made every company run a permanent `pass_with_flags` -- flagging the
    operator on exactly the trigger they press most, for a "gap" the trigger
    itself asked for. Scaling to the run's own selection keeps the check
    meaningful (a run that researched 3 tickers and wrote 1 section is still
    flagged) while letting a deliberately narrow run come back clean.

    An empty/absent selection falls back to the spec constants: that is a
    caller with no declared scope, and weakening the check to "anything goes"
    there would delete it for the full-watchlist path too.

    `tickers_with_findings` narrows the expectation further, to the selected
    tickers that actually have something to write about. Highlight selection
    (`pipeline/research/highlight_selection.py`) ranks on pre-fan-out mention
    counts, so a ticker can be selected and then have its research branch
    return zero findings -- no error, just nothing found. Run
    20260819T112959-a883d9 selected four and two came back empty, and the
    writer was flagged for the two sections it had no material to write. The
    absence is real and the report must name it (Coverage Notes does, via
    `pipeline/synthesis/render.apply_zero_yield_coverage_note`), but it is a
    research-yield fact, not a synthesis defect, and this check measures
    synthesis.

    If *nothing* yielded, the original selection stands as the expectation:
    that run wrote no deep-dive at all, which is a real gap, and scaling the
    bar to zero would call it clean.
    """
    if not highlight_tickers:
        return COMPLETENESS_MIN_HIGHLIGHTS, COMPLETENESS_MAX_HIGHLIGHTS
    count = len(highlight_tickers)
    if tickers_with_findings is not None:
        yielded = set(tickers_with_findings)
        count = len([t for t in highlight_tickers if t in yielded]) or count
    return count, count


def check_completeness(
    report_text: str,
    *,
    scores: dict,
    watchlist_tickers: list[str],
    highlight_tickers: list[str] | None = None,
    tickers_with_findings: set[str] | list[str] | None = None,
) -> tuple[list[str], list[str], list[str]]:
    """REPORT_SPEC.md §10's completeness predicate. Returns
    `(compliance_hard_violations, structural_hard_violations, soft_violations)`.
    Both hard lists force `verdict=block`... except REPORT_SPEC.md §10.1
    draws a further distinction between them: `compliance_hard` (the opening
    disclaimer, English-only) is always-block per §5.2's "son kapı", while
    `structural_hard` (absent tickers, no deep-dive section) can degrade to a
    publishable `degraded_publish` instead (R22) -- see
    `classify_violations`/`run_rule_based_prescreen`. Soft violations alone
    degrade `pass` to `pass_with_flags`, mirroring
    `missing_low_reliability_labels`'s existing severity split."""
    compliance_hard: list[str] = []
    structural_hard: list[str] = []
    soft: list[str] = []

    absent = find_absent_watchlist_tickers(report_text, watchlist_tickers)
    if absent:
        structural_hard.append(f"watchlist tickers absent from report: {', '.join(absent)}")

    uncovered_missing = find_uncovered_missing_tickers(report_text, scores)
    if uncovered_missing:
        structural_hard.append(
            "tickers marked missing but never named in the report: "
            + ", ".join(uncovered_missing)
        )

    sections = find_deep_dive_sections(report_text, watchlist_tickers)
    expected_min, expected_max = expected_highlight_range(
        highlight_tickers, tickers_with_findings=tickers_with_findings
    )
    if not (expected_min <= len(sections) <= expected_max):
        if not sections:
            structural_hard.append("no company has a deep-dive section")
        else:
            expected = (
                str(expected_min)
                if expected_min == expected_max
                else f"{expected_min}-{expected_max}"
            )
            soft.append(f"{len(sections)} deep-dive sections (this run expects {expected})")

    incomplete_blocks = [
        t
        for t, body in sections.items()
        if not all(marker in body for marker in SCORE_BLOCK_MARKERS)
    ]
    if incomplete_blocks:
        soft.append(f"deep-dive sections missing a full score block: {', '.join(incomplete_blocks)}")

    uncited = [t for t, body in sections.items() if "](http" not in body]
    if uncited:
        soft.append(f"deep-dive sections with no citation: {', '.join(uncited)}")

    empty_sections = find_empty_sections(report_text)
    if empty_sections:
        soft.append("sections with an empty body: " + ", ".join(empty_sections))

    if OPENING_DISCLAIMER_PHRASE not in report_text.lower():
        compliance_hard.append("opening disclaimer sentence missing")

    if any(ch in report_text for ch in NON_ENGLISH_CHARS):
        compliance_hard.append("non-English characters found (REPORT_SPEC.md §9: English throughout)")

    return compliance_hard, structural_hard, soft


def _section_containing(token: str, sections: dict[str, str]) -> str | None:
    """Best-effort attribution: the first deep-dive section whose text
    contains `token` (a leaked number or a fabricated URL). None if it
    appears in no section (e.g. it's in report-wide prose, or the report
    has no deep-dive sections at all) -- ambiguous when a token appears in
    more than one section, which is rare enough not to warrant more than
    "first match"."""
    for ticker, body in sections.items():
        if token in body:
            return ticker
    return None


def classify_violations(
    report_text: str,
    *,
    watchlist_tickers: list[str],
    number_leaks: list[str],
    citation_violations: list[str],
    citation_id_violations: list[str] | None = None,
    missing_disclaimer: bool,
    missing_ai_disclosure: bool,
    compliance_hard_completeness: list[str],
    structural_hard_completeness: list[str],
    soft_completeness: list[str],
    missing_labels: list[str],
) -> list[VerifierViolation]:
    """R19: the section-scoped, severity-classified superset of the flat
    violation lists `run_rule_based_prescreen` (verifier/node.py) already
    computes. Section attribution is best-effort via `find_deep_dive_sections`
    -- a violation that can't be attributed to one section (the disclaimer,
    most completeness checks) is report-wide (`section=None`)."""
    sections = find_deep_dive_sections(report_text, watchlist_tickers)
    violations: list[VerifierViolation] = []

    for token in number_leaks:
        violations.append(
            VerifierViolation(
                severity="compliance_hard",
                category="number_leak",
                message=f"number not found in scores/financials: {token!r}",
                section=_section_containing(token, sections),
            )
        )
    for url in citation_violations:
        violations.append(
            VerifierViolation(
                severity="compliance_hard",
                category="citation",
                message=f"fabricated citation (URL never retrieved): {url}",
                section=_section_containing(url, sections),
            )
        )
    for citation_id in citation_id_violations or []:
        violations.append(
            VerifierViolation(
                severity="compliance_hard",
                category="citation",
                message=(
                    f"fabricated citation id (never in the retrieved-source "
                    f"vocabulary): [{citation_id}]"
                ),
                section=_section_containing(f"[{citation_id}]", sections),
            )
        )
    if missing_disclaimer:
        violations.append(
            VerifierViolation(
                severity="compliance_hard",
                category="disclaimer",
                message="report is missing the required disclaimer phrase",
            )
        )
    if missing_ai_disclosure:
        violations.append(
            VerifierViolation(
                severity="compliance_hard",
                category="disclaimer",
                message="report is missing the required AI-generation disclosure (REPORT_SPEC.md §3)",
            )
        )
    for item in compliance_hard_completeness:
        violations.append(
            VerifierViolation(severity="compliance_hard", category="completeness", message=item)
        )
    for item in structural_hard_completeness:
        violations.append(
            VerifierViolation(severity="structural_hard", category="completeness", message=item)
        )
    for item in soft_completeness:
        violations.append(VerifierViolation(severity="soft", category="completeness", message=item))
    for ticker in missing_labels:
        violations.append(
            VerifierViolation(
                severity="soft",
                category="low_reliability_label",
                message=f"missing 'low reliability' label for {ticker}",
                section=ticker,
            )
        )
    return violations
