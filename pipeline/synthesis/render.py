"""Deterministic rendering for the parts of a report that are pure data.

Resolves the `{{ticker.field}}` placeholders the synthesis prompt
requires (`pipeline/synthesis/prompts.py`'s system prompt hard rules) into
real numbers from `scores`/`financials`. The LLM never writes a raw number
itself -- this is the one place that substitution happens, right before a
draft is persisted (`pipeline/storage/report_store.py`), so the numbers in
a stored report always trace back to the deterministic scoring engine's
output, never to LLM generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\.(\w+)\}\}")
# ADR 0010 §2 retired the weekly calendar as an identity; the report's own
# language follows. A run's research window is the rolling 7 days ending on
# `as_of` (pipeline/research/agent.py's `_search_window`), so anything the
# reader sees says "this run" / "the 7-day window", never "this week".
UNAVAILABLE = "data unavailable for this run"

# Same shape as pipeline/verifier/rules.py's MARKDOWN_LINK_RE, duplicated
# rather than imported to keep this module free of a verifier dependency
# (the verifier already imports *from* here, via find_deep_dive_sections).
_MARKDOWN_LINK_RE = re.compile(r"\[(?P<text>[^\]]*)\]\((?P<url>[^)]+)\)")

# The citation vocabulary the writer is given (`[S1]`, `[S2]`, ...). The
# negative lookahead skips a marker that is *already* a markdown link, which
# makes `expand_citation_ids` idempotent -- it can run on a draft that was
# partially expanded (or run twice) without doubling the URL.
CITATION_ID_RE = re.compile(r"\[(S\d+)\](?!\()")

# R5: below this fraction of placeholders resolved, a draft is a degenerate
# "data unavailable for this run" report that a verifier checking only prose
# structure won't catch.
MIN_RESOLUTION_RATE = 0.8


@dataclass
class ResolutionStats:
    """R5: what resolve_placeholders actually did, not just its output text.

    `unknown_field` (ticker, field) pairs are placeholders whose field name
    never appears in *any* ticker's score/financials block -- distinguishing
    "the writer typo'd a field name" from "this ticker legitimately has no
    value for a real field" (`resolved_via_unavailable`), which look
    identical in the rendered text alone.
    """

    resolved: int = 0
    unavailable: int = 0
    unknown_field: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.resolved + self.unavailable

    @property
    def resolution_rate(self) -> float:
        return self.resolved / self.total if self.total else 1.0

    @property
    def below_threshold(self) -> bool:
        return self.total > 0 and self.resolution_rate < MIN_RESOLUTION_RATE


WATCHLIST_TABLE_MARKER = "{{FULL_WATCHLIST_TABLE}}"
_TABLE_HEADER = (
    "| Ticker | Composite | Band | Sector %ile | Risk | Coverage % | Flags |\n"
    "|---|---:|---|---:|---:|---:|---|"
)


def _cell(value) -> str:
    """Render one table cell without inventing a number.

    Values are passed through exactly as the scoring engine produced them
    (already rounded at the DB boundary by scores_repository) -- no
    reformatting, since a derived figure would read as a fabricated number
    to the verifier's number-leak scan and, worse, would be one.
    """
    if value is None or value == "":
        return "--"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "--"
    return str(value)


def render_watchlist_table(
    scores: dict[str, dict], watchlist_tickers: list[str] | None = None
) -> str:
    """The full watchlist score table, grouped by cohort and sorted by
    composite score within cohort (REPORT_SPEC.md §2).

    Generated here rather than written by the LLM: it is pure data with no
    judgment in it, and asking one generation to transcribe 40+ rows loses
    rows non-deterministically -- observed dropping CRM on one run and
    NOW/SNOW on the next, each time hard-failing the completeness check.

    `watchlist_tickers` covers the tickers that have no `scores` entry at
    all. A ticker that never scored (an EUR-only filer, a company with no
    10-K yet) has no DB row, so it is absent from `scores` rather than
    marked `missing` -- it still must appear in the report
    (REPORT_SPEC.md §6, "never silently omitted").
    """
    by_cohort: dict[str, list[tuple[str, dict]]] = {}
    for ticker, block in scores.items():
        if not isinstance(block, dict):
            continue
        cohort = block.get("cohort") or "?"
        by_cohort.setdefault(cohort, []).append((ticker, block))

    lines: list[str] = []
    for cohort in sorted(by_cohort):
        rows = sorted(
            by_cohort[cohort],
            key=lambda kv: (
                kv[1].get("composite_score") is None,
                -(kv[1].get("composite_score") or 0),
                kv[0],
            ),
        )
        lines.append(f"**Cohort {cohort}**")
        lines.append("")
        lines.append(_TABLE_HEADER)
        for ticker, block in rows:
            if block.get("missing"):
                flags = block.get("reason") or "no score for this run"
                lines.append(f"| {ticker} | -- | -- | -- | -- | -- | {_cell(flags)} |")
                continue
            lines.append(
                "| {t} | {comp} | {band} | {pct} | {risk} | {cov} | {flags} |".format(
                    t=ticker,
                    comp=_cell(block.get("composite_score")),
                    band=_cell(block.get("composite_band")),
                    pct=_cell(block.get("sector_percentile")),
                    risk=_cell(block.get("risk_score")),
                    cov=_cell(block.get("coverage_pct")),
                    flags=_cell(block.get("warnings")),
                )
            )
        lines.append("")

    unscored = [t for t in (watchlist_tickers or []) if t not in scores]
    if unscored:
        lines.append("**Not scored in this run**")
        lines.append("")
        lines.append(_TABLE_HEADER)
        for ticker in sorted(unscored):
            lines.append(
                f"| {ticker} | -- | -- | -- | -- | -- | no score for this run |"
            )
        lines.append("")

    return "\n".join(lines).strip()


def expand_watchlist_table(
    text: str, scores: dict[str, dict], watchlist_tickers: list[str] | None = None
) -> str:
    """Substitute `{{FULL_WATCHLIST_TABLE}}` with the rendered table.

    Runs before the verifier (unlike `resolve_placeholders`, which runs after
    it) so the completeness check still sees a report containing every
    ticker -- the check stays meaningful instead of being bypassed.
    """
    if WATCHLIST_TABLE_MARKER not in text:
        return text
    return text.replace(
        WATCHLIST_TABLE_MARKER, render_watchlist_table(scores, watchlist_tickers)
    )


def _flatten_score_block(block: dict) -> dict:
    """Expose the nested `categories`/`risk` detail as flat
    `{{TICKER.field}}`-addressable names.

    `pipeline/data/scores_repository.py` returns category scores as a list of
    dicts and risk detail as a nested dict, but the score block the synthesis
    prompt mandates is written in terms of flat fields
    (`valuation_score`, `altman_zone`, `piotroski_f`, ...). Without this,
    every one of those placeholders silently resolved to UNAVAILABLE while
    the report still looked well-formed.
    """
    flat: dict = {}
    for category in block.get("categories") or []:
        name = category.get("category_name")
        if not name:
            continue
        flat[f"{name}_score"] = category.get("score")
        flat[f"{name}_weight"] = category.get("weight")
    risk = block.get("risk") or {}
    for field, value in risk.items():
        # risk.score/risk.band are already exposed as risk_score/risk_band.
        if field in ("score", "band"):
            continue
        flat.setdefault(field, value)
    return flat


def _format_value(value) -> str:
    """Render a resolved value as prose, not as a Python repr.

    A list field (`warnings`) went into the report verbatim as `[]`, which is
    what a reader saw on the published page.
    """
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _known_fields(scores: dict[str, dict], financials: dict[str, dict]) -> set[str]:
    """Every field name that resolves to a real value for *some* ticker --
    the reference set R5's typo-vs-missing distinction is checked against."""
    known: set[str] = set()
    for block in scores.values():
        if isinstance(block, dict):
            known.update(k for k, v in block.items() if v is not None)
            known.update(k for k, v in _flatten_score_block(block).items() if v is not None)
    for block in financials.values():
        if isinstance(block, dict):
            known.update(k for k, v in block.items() if v is not None)
    return known


def render_macro_spine(items: list) -> str:
    """R28: the quantitative macro spine as a deterministic markdown table
    -- `items` is `list[pipeline.macro_spine.MacroSpineItem]`. A series with
    no value (fetch failed, or the client wasn't configured) renders `n/a`
    rather than being silently dropped, mirroring the watchlist table's
    "never silently omit a row" convention."""
    if not items:
        return ""
    lines = ["| Indicator | Value | As of |", "|---|---:|---|"]
    for item in items:
        value = f"{item.value:g}{item.units}" if item.value is not None else "n/a"
        as_of = item.as_of or "n/a"
        lines.append(f"| {item.label} | {value} | {as_of} |")
    return "\n".join(lines)


def apply_macro_spine(body_markdown: str, items: list) -> str:
    """Inserts the spine table right after the "## Sector & Macro" heading
    if one exists; otherwise appends a new "## Sector & Macro" section
    (R28 closes "a sector report with zero macro numbers" even on a week
    where no qualitative macro finding produced that heading)."""
    table = render_macro_spine(items)
    if not table:
        return body_markdown
    marker = "## Sector & Macro"
    if marker in body_markdown:
        return body_markdown.replace(marker, f"{marker}\n\n{table}\n", 1)
    return f"{body_markdown.rstrip()}\n\n{marker}\n\n{table}\n"


# --- Section-level deterministic fills -------------------------------------
#
# `pipeline/storage/report_store.split_into_sections()` persists one row per
# `##`/`###` heading, so a heading the writer emits with nothing under it
# becomes a stored section whose body is its own heading and nothing else.
# That is what shipped on run 20260819T112959-a883d9: "This Week's
# Highlights" persisted at 25 characters -- the heading -- because the writer
# treated it as a container for the `###` deep-dives that follow it. The
# heading level is matched here the same way the storage layer matches it, so
# "which headings become sections" means one thing across both modules.
_SECTION_HEADING_RE = re.compile(r"^(#{2,3})[ \t]+(.+?)[ \t]*$", re.MULTILINE)

# Written by `apply_zero_yield_coverage_note` and recognised by it on a second
# pass, so the note is inserted at most once however many times the pipeline
# re-runs this over the same draft.
ZERO_YIELD_NOTE_PREFIX = (
    "Selected for deep-dive coverage but returned no research findings in "
    "this window:"
)


def _is_highlights_heading(text: str) -> bool:
    return text.strip().rstrip(":").lower().endswith("highlights")


def _is_coverage_notes_heading(text: str) -> bool:
    return text.strip().rstrip(":").lower().endswith("coverage notes")


def _section_span(body_markdown: str, matches_heading) -> tuple[re.Match, int, int] | None:
    """`(heading_match, body_start, body_end)` for the first `##`/`###`
    section whose heading text satisfies `matches_heading`, or None.

    `body_end` is the start of the next heading of *any* matched level, which
    is deliberately how the storage layer slices too: a `##` container
    followed immediately by a `###` has an empty body, and pretending
    otherwise is exactly the bug this exists to close.
    """
    matches = list(_SECTION_HEADING_RE.finditer(body_markdown))
    for i, match in enumerate(matches):
        if not matches_heading(match.group(2)):
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body_markdown)
        return match, start, end
    return None


def render_highlights_lead_in(
    *,
    covered_tickers: list[str],
    zero_yield_tickers: list[str] | None = None,
    as_of: str | None = None,
) -> str:
    """The Highlights section's own body: which tickers this run deep-dives,
    and which it selected but could not cover.

    Tickers only -- no company names, no finding titles. A company name or a
    headline can carry a digit ("3M", "raises capacity 30%"), and this text is
    inserted *before* the verifier's number-leak scan, so borrowing prose from
    the findings here would turn a research headline into a compliance_hard
    block. The section's job is to say what the reader is about to see; the
    deep-dives themselves carry the substance.
    """
    zero_yield = list(zero_yield_tickers or [])
    if not covered_tickers and not zero_yield:
        return ""
    # Spelled out, never "7": this text is inserted before the verifier's
    # number-leak scan, which cannot tell a window length from a fabricated
    # financial figure and hard-blocks the run on the digit.
    window = f"the seven days ending {as_of}" if as_of else "the last seven days"
    parts: list[str] = []
    if covered_tickers:
        parts.append(
            "Deep-dive coverage below, selected before research by news volume "
            f"over {window}: " + ", ".join(covered_tickers) + "."
        )
    if zero_yield:
        # Deliberately *not* ZERO_YIELD_NOTE_PREFIX's wording: that string is
        # the Coverage Notes note's idempotency marker, and reusing it here
        # would make the lead-in suppress the Coverage Notes entry.
        parts.append(
            "Also selected but returning no findings in this window: "
            + ", ".join(zero_yield)
            + " -- no deep-dive section follows for them; see Coverage Notes."
        )
    return "\n\n".join(parts)


def apply_highlights_lead_in(
    body_markdown: str,
    *,
    covered_tickers: list[str],
    zero_yield_tickers: list[str] | None = None,
    as_of: str | None = None,
) -> str:
    """Fill an empty Highlights section with `render_highlights_lead_in`, or
    drop the heading entirely when there is nothing to say under it.

    Only ever writes into a section whose body is already blank, so a writer
    that *did* produce a lead-in keeps it: this is the floor, not a rewrite.
    An empty-bodied heading is never left standing either way -- an empty
    section must not be persisted (the storage layer will happily store one).
    """
    span = _section_span(body_markdown, _is_highlights_heading)
    if span is None:
        return body_markdown
    match, start, end = span
    if body_markdown[start:end].strip():
        return body_markdown
    lead_in = render_highlights_lead_in(
        covered_tickers=covered_tickers,
        zero_yield_tickers=zero_yield_tickers,
        as_of=as_of,
    )
    if not lead_in:
        # Nothing to put under it: remove the heading rather than persist a
        # section that is only its own title.
        return body_markdown[: match.start()] + body_markdown[end:].lstrip("\n")
    return body_markdown[:start] + f"\n\n{lead_in}\n" + body_markdown[end:]


def apply_zero_yield_coverage_note(
    body_markdown: str, zero_yield_tickers: list[str]
) -> str:
    """Name the selected-but-zero-yield tickers in Coverage Notes.

    A ticker that reached the fan-out and came back with nothing is a coverage
    gap the reader cannot otherwise see: it is badged as a highlight on the
    site, has no deep-dive, and no failure was recorded for it because nothing
    actually failed. Written deterministically rather than asked of the
    writer, which has no reliable way to know a branch it was never handed
    findings for even existed.
    """
    if not zero_yield_tickers:
        return body_markdown
    note = f"{ZERO_YIELD_NOTE_PREFIX} " + ", ".join(zero_yield_tickers) + "."
    if ZERO_YIELD_NOTE_PREFIX in body_markdown:
        return body_markdown
    span = _section_span(body_markdown, _is_coverage_notes_heading)
    if span is None:
        return f"{body_markdown.rstrip()}\n\n## Coverage Notes\n\n{note}\n"
    _match, start, end = span
    section = body_markdown[start:end].rstrip()
    return body_markdown[:start] + f"{section}\n\n{note}\n\n" + body_markdown[end:]


def build_citation_vocabulary(retrieved_urls: set[str] | None) -> dict[str, str]:
    """`{"S1": url, "S2": url, ...}` -- the closed citation vocabulary the
    writer is allowed to cite from (F5's structural fix for citation
    fabrication).

    The writer never sees a URL it could adapt into a plausible-looking one:
    it is handed opaque ids and told to write `[S3]`, and this mapping is what
    turns those back into links deterministically (`expand_citation_ids`)
    after generation. An id that is not a key here cannot be silently
    "corrected" into a URL the way an invented slug used to read as a real
    citation -- it is a parse failure with nothing to expand to, which the
    verifier then treats as a compliance_hard violation.

    Ids are assigned in sorted-URL order for the same reason
    `format_citable_urls_block` sorts: the writer runs at temperature 0, so a
    prompt that reordered between runs would be the only source of
    run-to-run drift.
    """
    return {f"S{i}": url for i, url in enumerate(sorted(retrieved_urls or []), start=1)}


def _citation_id_order(citation_id: str) -> tuple[int, str]:
    """Sort `S9` before `S10` (lexicographic order would not), so a
    fabricated-id list reads in the order the vocabulary numbers them."""
    return (int(citation_id[1:]) if citation_id[1:].isdigit() else 0, citation_id)


def expand_citation_ids(
    text: str, vocabulary: dict[str, str]
) -> tuple[str, list[str]]:
    """Expand every `[S<n>]` citation marker into a markdown link to the URL
    that id stands for. Returns `(text, sorted_unknown_ids)`.

    Deterministic post-processing, the same shape as `expand_watchlist_table`
    above: the LLM writes the marker, this writes the URL, so a cited URL in a
    finished report is by construction one a research branch actually
    retrieved.

    An id the vocabulary does not contain is left in the text exactly as the
    writer wrote it and returned in the second element. It is deliberately
    *not* silently deleted: the run is going to block on it (see
    `pipeline/verifier/rules.py`'s `citation` category), and the blocked draft
    a reader/operator sees should still show the marker the writer invented.
    """
    unknown: set[str] = set()

    def _sub(match: re.Match) -> str:
        citation_id = match.group(1)
        url = vocabulary.get(citation_id)
        if url is None:
            unknown.add(citation_id)
            return match.group(0)
        return f"[{citation_id}]({url})"

    return CITATION_ID_RE.sub(_sub, text), sorted(unknown, key=_citation_id_order)


def strip_ungrounded_citations(
    text: str, retrieved_urls: set[str]
) -> tuple[str, list[str]]:
    """Remove http(s) markdown links whose URL was never returned by a
    research branch, keeping the link's anchor text as plain prose. Returns
    `(text, sorted_dropped_urls)`.

    This is the synthesis-level twin of the drop `pipeline/research/agent.py`
    already performs one layer up, where a research LLM's claimed
    `source_urls` are filtered down to what the searcher actually returned.
    The writer had no equivalent guard: it could name any URL it liked, and
    on the 2026-08-17 runs it reliably invented a plausible Reuters slug for
    the Regulation macro paragraph -- discarding an otherwise-sound report
    (and the whole run's spend) on a single fabricated link.

    Deliberately *not* a way to launder a fabrication into a pass -- and
    until the citation-id vocabulary landed it was exactly that, because this
    runs inside `synthesis_node`, i.e. *before* the verifier's
    `find_citation_violations` ever sees the draft, so the gate was
    structurally always handed an already-clean document. What the drop
    returns is therefore pre-strip *evidence*: the caller puts it on the
    state (`fabricated_citations_dropped`) and `pipeline/verifier/node.py`
    raises a compliance_hard violation from it. This function stays as
    defense-in-depth behind that -- removing the link is the conservative
    outcome for the text itself (the claim loses a source it never had,
    rather than keeping one that points nowhere) -- but it is no longer what
    decides whether fabrication is visible.
    """
    dropped: set[str] = set()

    def _sub(match: re.Match) -> str:
        url = match.group("url").strip()
        if not url.lower().startswith(("http://", "https://")):
            # Relative/internal links are document cross-references, not
            # source claims -- the mandated methodology link to /legal is
            # one, and no research branch can ever "retrieve" it.
            return match.group(0)
        if url in retrieved_urls:
            return match.group(0)
        dropped.add(url)
        return match.group("text")

    return _MARKDOWN_LINK_RE.sub(_sub, text), sorted(dropped)


def resolve_placeholders_with_stats(
    text: str, scores: dict[str, dict], financials: dict[str, dict]
) -> tuple[str, ResolutionStats]:
    """Same substitution as `resolve_placeholders`, plus R5's resolution
    stats: how many placeholders resolved vs fell back to UNAVAILABLE, and
    which of the UNAVAILABLE ones used a field name that's never valid for
    any ticker (a likely typo, not just missing data for this one)."""
    stats = ResolutionStats()
    known_fields = _known_fields(scores, financials)

    def _sub(match: re.Match) -> str:
        ticker, field_name = match.group(1), match.group(2)
        block = scores.get(ticker) or {}
        if field_name in block and block[field_name] is not None:
            stats.resolved += 1
            return _format_value(block[field_name])
        flat = _flatten_score_block(block)
        if flat.get(field_name) is not None:
            stats.resolved += 1
            return _format_value(flat[field_name])
        block = financials.get(ticker) or {}
        if field_name in block and block[field_name] is not None:
            stats.resolved += 1
            return _format_value(block[field_name])
        stats.unavailable += 1
        if field_name not in known_fields:
            stats.unknown_field.append((ticker, field_name))
        return UNAVAILABLE

    resolved_text = PLACEHOLDER_RE.sub(_sub, text)
    return resolved_text, stats


def render_degraded_publish_banner(gap_messages: list[str]) -> str:
    """R22: the reduced-coverage banner prepended to a `degraded_publish`
    report's body -- named gaps (each `structural_hard` violation's
    message) instead of the report being discarded outright. Markdown
    blockquote so it renders distinctly wherever the body is shown; no
    front-end change needed since it's part of body_markdown itself."""
    lines = [
        "> **Reduced coverage.** This report published with the following "
        "structural gap(s), which did not affect data accuracy or citation "
        "integrity:",
        ">",
    ]
    lines.extend(f"> - {g}" for g in gap_messages)
    return "\n".join(lines)


def apply_degraded_publish_banner(body_markdown: str, gap_messages: list[str]) -> str:
    """Inserts the banner right after the title line (the first line), or
    at the very top if the body doesn't start with a heading."""
    if not gap_messages:
        return body_markdown
    banner = render_degraded_publish_banner(gap_messages)
    lines = body_markdown.split("\n", 1)
    if lines[0].startswith("#"):
        rest = lines[1] if len(lines) > 1 else ""
        return f"{lines[0]}\n\n{banner}\n{rest}"
    return f"{banner}\n\n{body_markdown}"


def resolve_placeholders(text: str, scores: dict[str, dict], financials: dict[str, dict]) -> str:
    resolved_text, _stats = resolve_placeholders_with_stats(text, scores, financials)
    return resolved_text


# The same four literal markers the verifier's rule-based prescreen matches
# (pipeline/verifier/rules.py's SCORE_BLOCK_MARKERS). Imported lazily inside
# fence_bare_score_blocks() rather than at module level, matching the
# existing lazy-import convention in apply_score_attribution_appendix()
# above (pipeline.verifier.rules doesn't currently import back into this
# module, but keeping the import lazy avoids relying on that staying true).
_SCORE_BLOCK_START = "COMPOSITE SCORE"
_SCORE_BLOCK_END = "DATA COVERAGE"


def fence_bare_score_blocks(body_markdown: str) -> str:
    """Wrap every unfenced score block in a ``` code fence.

    REPORT_SPEC.md §5.1/§9 mandates the deterministic score block
    (COMPOSITE SCORE .. DATA COVERAGE, see `render_score_block()` in
    `pipeline/synthesis/stitcher.py` for the exact layout) be fenced. The
    live synthesis path (`pipeline/synthesis/node.py`) produces the whole
    deep-dive -- narrative prose and score block together -- from a single
    LLM call; `prompts.py` instructs the model to add the fence, but that is
    prompt compliance, not a guarantee, and production reports have shipped
    with the block unfenced. In HTML (`front-end/app/reports/[slug]/page.tsx`
    renders `bodyMarkdown` through ReactMarkdown) an unfenced block collapses
    into one unreadable run-on line, since `<p>` collapses whitespace runs
    and drops newlines.

    Normalising here -- deterministic post-processing, the same pattern
    `apply_degraded_publish_banner` already uses -- makes the fence a
    property of construction rather than of model behaviour, independent of
    what the model actually did.

    Idempotent: a block already inside a fence is left exactly as-is (the
    fence-tracking scan below just toggles past ``` lines without touching
    their contents), so this is safe to run on a report where some deep-dive
    blocks are already fenced and some aren't, and safe to run twice.

    A report has multiple deep-dive sections, each with its own block; every
    occurrence is fenced, not just the first.

    If a COMPOSITE SCORE line has no following DATA COVERAGE line before the
    document ends, that region is left untouched rather than fenced to EOF --
    fencing to the end would swallow any prose that follows as part of the
    "block".
    """
    from pipeline.verifier.rules import SCORE_BLOCK_MARKERS

    start_marker, end_marker = SCORE_BLOCK_MARKERS[0], SCORE_BLOCK_MARKERS[-1]
    assert start_marker == _SCORE_BLOCK_START and end_marker == _SCORE_BLOCK_END, (
        "pipeline/verifier/rules.py's SCORE_BLOCK_MARKERS layout changed; "
        "update _SCORE_BLOCK_START/_SCORE_BLOCK_END above to match"
    )

    lines = body_markdown.split("\n")
    out: list[str] = []
    in_fence = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append(line)
            i += 1
            continue
        if not in_fence and line.lstrip().startswith(_SCORE_BLOCK_START):
            end = i
            while end < len(lines) and not lines[end].lstrip().startswith(_SCORE_BLOCK_END):
                end += 1
            if end < len(lines):
                out.append("```")
                out.extend(lines[i : end + 1])
                out.append("```")
                i = end + 1
                continue
            # No DATA COVERAGE line found before EOF: not the score block
            # layout we know how to fence safely. Leave as-is.
        out.append(line)
        i += 1
    return "\n".join(out)
