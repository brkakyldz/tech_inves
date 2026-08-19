"""Typed state for the research/synthesis/verifier pipeline.

Mirrors the conceptual state schema in reports/research/ARCHITECTURE_PROPOSAL.md §4.1,
scoped down to what this package actually owns: `research_findings`,
`retrieved_urls`, `failures`, `draft_report`, `verifier_report`. `financials`/
`scores`/`highlight_tickers` are accepted as plain input (see
pipeline/fixtures/mock_data.py for their shape) -- the real scoring engine
(src/techinves) is out of scope for this package.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Scope = Literal["company", "macro"]
# R22: `degraded_publish` sits between `pass_with_flags` and `block` -- a
# report with a structural_hard violation (e.g. a missing deep-dive) but no
# compliance_hard one is publishable with a reduced-coverage banner instead
# of discarded outright. Previously there was no publishable middle between
# a clean pass and nothing.
Verdict = Literal["pass", "pass_with_flags", "degraded_publish", "block"]


class CoveredEvent(BaseModel):
    """An already-reported event from an earlier run, used for de-dup context.

    `company` is set (ticker) for scope="company" records, `topic` is set
    (macro topic string) for scope="macro" records -- the other is None.

    Keyed on runs, not weeks (ADR 0010 §9). `first_covered_week` /
    `last_updated_week` became `first_covered_run` / `last_updated_run`.
    `run_seq` is the ordinal of the run that last touched this event, and is
    what the trailing-window prune (`COVERED_EVENTS_TRAILING_RUNS`) compares
    against; it is carried in the model rather than derived from the `runs`
    table so the DB store and the JSON-file store enforce the same window
    through the same code. `event_key` is the stable identity used to update
    a row in place when a matched event's headline evolves.
    """

    event_key: str = ""
    scope: Scope
    company: str | None = None
    topic: str | None = None
    event_type: str
    event_title: str
    first_covered_run: str
    last_updated_run: str
    run_seq: int = 0
    source_urls: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    """Structured output of a single research branch (company or macro).

    No numeric fields by design (ARCHITECTURE_PROPOSAL.md §4.2 point 1) --
    this is enforced at the schema level, not just by prompting.
    """

    scope: Scope
    ticker: str | None = None
    topic: str | None = None
    event_title: str
    event_type: str
    narrative: str
    source_urls: list[str] = Field(default_factory=list)
    is_followup_of: str | None = None
    confidence: Literal["low", "medium", "high"] = "medium"
    # R14: the best (lowest-number) domain_tier among source_urls, set by
    # pipeline/research/agent.py after citation grounding -- None only for a
    # finding with no surviving citations.
    source_tier: int | None = None


class FailureNote(BaseModel):
    """A branch/node that failed or returned nothing, surfaced to the reader."""

    scope: Scope | Literal["financial_data", "synthesis", "verifier"]
    ticker: str | None = None
    topic: str | None = None
    reason: str


class BranchYield(BaseModel):
    """Per-branch measurement (R1): what one research branch actually
    produced and cost, so a yield collapse is visible instead of only a
    findings_count drop with nothing to explain it."""

    scope: Scope
    ticker: str | None = None
    topic: str | None = None
    findings_count: int = 0
    degraded: bool = False
    tokens: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0


class VerifierSectionScore(BaseModel):
    section: str
    confidence: int = Field(ge=0, le=10)
    rationale: str


# R19: the old model conflated every rule-based hit into one undifferentiated
# `block` -- a fabricated number and "8 deep-dive sections instead of 3-8"
# were equally terminal, with no way to tell the two apart or say which
# section was responsible. `Severity` splits them so a later stage (R21's
# repair loop, R22's degraded-publish path) can react differently:
#   compliance_hard   -- a trust/safety issue (fabricated number, fabricated
#                        citation, missing legal disclaimer). Always `block`;
#                        this is REPORT_SPEC.md §5.2's "son kapı" and nothing
#                        downstream may override it.
#   structural_hard    -- a structural/formatting defect (missing deep-dive,
#                        absent watchlist ticker). Was unconditionally
#                        `block`; is now something a repair pass or a
#                        degraded-publish banner can address instead.
#   soft               -- already-existing `pass_with_flags` territory
#                        (missing low-reliability label, thin section count).
Severity = Literal["compliance_hard", "structural_hard", "soft"]


class VerifierViolation(BaseModel):
    """One classified, optionally section-scoped rule-based finding.
    `section` is a ticker, a topic, or None for a report-wide issue (e.g.
    the disclaimer) -- best-effort, since not every violation type can be
    attributed to one section (see `pipeline.verifier.rules.classify_violations`).
    """

    severity: Severity
    category: str
    message: str
    section: str | None = None


class VerifierReport(BaseModel):
    verdict: Verdict
    number_leak_violations: list[str] = Field(default_factory=list)
    citation_violations: list[str] = Field(default_factory=list)
    # Citation ids (`[S7]`) the writer used that were not in the closed
    # vocabulary it was given (`pipeline.synthesis.render.
    # build_citation_vocabulary`). Kept apart from `citation_violations`
    # above, which is URL-shaped: an unknown id never became a URL at all,
    # so there is nothing to print as one. Both are compliance_hard.
    citation_id_violations: list[str] = Field(default_factory=list)
    missing_disclaimer: bool = False
    completeness_violations: list[str] = Field(default_factory=list)
    section_scores: list[VerifierSectionScore] = Field(default_factory=list)
    # R19: the classified, section-scoped superset of the flat lists above
    # (which stay for backward compatibility with existing consumers --
    # pipeline/observability.py's log line, pipeline/storage/report_store.py's
    # persisted section_scores). Empty on a VerifierReport built before R19
    # (e.g. old persisted data, or a caller that hasn't adopted it).
    violations: list[VerifierViolation] = Field(default_factory=list)
    notes: str = ""
    # R31 (reports/backlog/verifier-checks-are-degenerate.md item 1): this
    # run's actually-researched/highlighted ticker subset (state's
    # `highlight_tickers`), carried here purely as descriptive metadata --
    # e.g. `coverage_scope=["AAPL", "NVDA"]` on a smoke run. Never consulted
    # by the completeness predicate itself (`pipeline.verifier.rules.
    # check_completeness` is checked against the real watchlist instead,
    # via `ReportState["scoring_eligible_tickers"]`) -- using this as the
    # completeness baseline was exactly the self-supplied-baseline bug this
    # field replaces.
    coverage_scope: list[str] = Field(default_factory=list)


class ResearchBranchInput(BaseModel):
    """One Send() fan-out unit -- one company ticker or one macro topic."""

    scope: Scope
    ticker: str | None = None
    topic: str | None = None
    covered_events_context: str = ""
    # R7: the run's as-of date, threaded through so the search window can be
    # pinned to it explicitly (pipeline/research/agent.py) instead of
    # Tavily's relative time_range="week", which is anchored to "now" --
    # wrong whenever a run is re-issued or backfilled. Named `as_of` rather
    # than `week_of` since ADR 0010 §2: it is the end of this run's research
    # window, not the identity of a weekly product.
    as_of: date | None = None


def research_findings_reducer(
    left: list[Finding], right: list[Finding]
) -> list[Finding]:
    return left + right


# The reset sentinel a node emits to say "replace this channel, don't append
# to it". `None` rather than a dedicated object because a LangGraph node
# returns plain state dicts, and no legitimate producer of these channels
# ever emits None (they emit lists/sets, empty at worst).
RESET = None


def additive_with_reset(left, right):
    """`operator.add` for the fan-in channels, plus a reset.

    The plain `operator.add` reducer made the *graph* non-idempotent under
    thread reuse: `init_node` (pipeline/graph.py) re-emits each accumulator's
    current value, which on a checkpointed thread that already ran is
    appended to itself -- observed live as findings 33 -> 81 -> 162 across
    three attempts. `pipeline/run.py`'s `_resolve_thread` avoids that by
    never reusing a completed thread, but that makes correctness a property
    of one caller: anyone invoking the compiled graph directly on an existing
    thread (a resume endpoint, a repair loop, a notebook) still doubles.

    With the sentinel the reset is a property of the graph instead. `init`
    runs only on a full invoke from START and never on a `None` resume, so
    emitting the sentinel there clears the accumulators exactly when a run
    starts fresh and never on a genuine crash resume.
    """
    if right is RESET:
        return []
    return list(left or []) + list(right)


def union_with_reset(left, right):
    """`operator.or_` twin of `additive_with_reset`, for `retrieved_urls`."""
    if right is RESET:
        return set()
    return set(left or set()) | set(right)


class ReportState(TypedDict, total=False):
    # ADR 0010 §2: `run_id` is the identity of this unit of work. `as_of` is
    # only the date the research window ends on (and the date the report is
    # headed with) -- it keys nothing.
    run_id: str
    as_of: date
    highlight_tickers: list[str]
    macro_topics: list[str]
    covered_events: list[CoveredEvent]

    # R31: the true "every scoring-eligible watchlist ticker" universe
    # (REPORT_SPEC.md §1/D1, §10 item 1) -- data/watchlist.yaml minus
    # `scoring_excluded`, computed once by pipeline/run.py via
    # `pipeline.config.load_scoring_eligible_tickers()` and passed as plain
    # graph input, same pattern as scores/financials/macro_spine below.
    # `pipeline/verifier/node.py` falls back to loading it directly when
    # this key is omitted, so this is optional for callers (e.g. tests)
    # that don't exercise completeness against the real watchlist.
    scoring_eligible_tickers: list[str]

    # Mocked/stubbed input in this package's scope -- see
    # pipeline/fixtures/mock_data.py. Keyed by ticker.
    financials: dict[str, dict]
    scores: dict[str, dict]

    # R28: the macro quantitative spine (list[pipeline.macro_spine.MacroSpineItem]),
    # computed once per run (not per branch) and passed as graph input the
    # same way scores/financials are. Empty/absent means no FRED client was
    # configured for this run -- synthesis_node then adds no spine table.
    macro_spine: list

    # Qualitative column -- no numeric fields anywhere in this branch.
    # Reset-aware reducers (not bare operator.add/or_): see
    # `additive_with_reset` for why the reset belongs to the graph rather
    # than to `pipeline/run.py`'s thread bookkeeping.
    research_findings: Annotated[list[Finding], additive_with_reset]
    retrieved_urls: Annotated[set[str], union_with_reset]
    failures: Annotated[list[FailureNote], additive_with_reset]
    branch_yields: Annotated[list[BranchYield], additive_with_reset]

    draft_report: str | None
    verifier_report: VerifierReport | None

    # URLs the writer cited that no research branch ever retrieved, removed
    # from the draft by `pipeline/synthesis/render.strip_ungrounded_citations`.
    # Kept on the state (not just logged) because the strip runs *before* the
    # verifier: this is the pre-strip evidence the verifier raises its
    # compliance_hard citation violation from, and without it a fabrication
    # is invisible to every layer below the reader.
    fabricated_citations_dropped: list[str]
    # Same role for the citation-id vocabulary: `[S<n>]` markers the writer
    # used that its closed source list never contained, recorded by
    # `pipeline/synthesis/render.expand_citation_ids`.
    fabricated_citation_ids: list[str]

    # Synthesis/verifier LLM usage, tracked separately from `branch_yields`
    # (which is research-branch-only): the writer and judge calls are each a
    # single node invocation, not a fan-out, so they don't fit BranchYield's
    # per-branch shape. `pipeline/observability.py` rolls these into
    # RunSummary.total_tokens/total_cost_usd so that figure is the whole
    # run's spend, not just research.
    synthesis_tokens: int
    synthesis_cost_usd: float
    verifier_tokens: int
    verifier_cost_usd: float
