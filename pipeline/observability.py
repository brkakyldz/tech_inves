"""Run-level observability: a post-run `RunSummary` plus a timed
`graph.invoke()` wrapper. No external tracing service (e.g. LangSmith) is
wired here -- this is a minimal, dependency-free stand-in so a run against
the full watchlist (dozens of branches) isn't a black box.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from pipeline.config import YIELD_FLOOR_FRACTION
from pipeline.schemas import BranchYield, FailureNote, Finding, ReportState, VerifierReport

logger = logging.getLogger(__name__)


class InvocableGraph(Protocol):
    def invoke(self, state_input: dict | None, config: dict | None = ...) -> dict: ...


@dataclass
class RunSummary:
    run_id: str
    duration_seconds: float
    company_branches: int
    macro_branches: int
    findings_count: int
    failure_count: int
    failures: list[FailureNote] = field(default_factory=list)
    verdict: str | None = None
    number_leak_count: int = 0
    citation_violation_count: int = 0
    # The offending tokens themselves, not just their counts: a `block`
    # verdict is only actionable if the operator can see *which* number or
    # URL tripped it. `as_log_line()` deliberately stays a one-line summary,
    # so these are logged separately by `run_with_summary()`.
    number_leak_violations: list[str] = field(default_factory=list)
    citation_violations: list[str] = field(default_factory=list)
    completeness_violations: list[str] = field(default_factory=list)
    missing_disclaimer: bool = False
    verifier_notes: str = ""
    # Citations the writer invented that `pipeline/synthesis/render.
    # strip_ungrounded_citations` removed before the verifier ever saw them.
    # Tracked at run level precisely *because* the strip means they no
    # longer show up as `citation_violations`: without this, making the
    # pipeline resilient to fabrication would also make fabrication
    # invisible, and a writer/prompt regression could worsen silently.
    fabricated_citations_dropped: list[str] = field(default_factory=list)
    # F5: citation ids (`[S7]`) the writer used that were never in the closed
    # vocabulary its prompt listed. Distinct from the URL list above -- an
    # invented id never became a URL, so there is nothing to print as one --
    # but the same run-level signal: a non-zero count here is a writer/prompt
    # regression, and (unlike before) it is also a `block`.
    fabricated_citation_ids: list[str] = field(default_factory=list)
    # R1: per-branch yield/cost so a collapse is attributable to specific
    # branches, plus run-level totals rolled up from them.
    branch_yields: list[BranchYield] = field(default_factory=list)
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    # Distinct from `verdict` (pass/pass_with_flags/block): the human-readable
    # *why*, so a non-pass verdict is diagnosable from the persisted run row
    # alone instead of requiring a log re-read.
    verdict_reason: str = ""

    def as_log_line(self) -> str:
        return (
            f"run_id={self.run_id} duration={self.duration_seconds:.1f}s "
            f"branches(company={self.company_branches}, macro={self.macro_branches}) "
            f"findings={self.findings_count} failures={self.failure_count} "
            f"verdict={self.verdict} verdict_reason={self.verdict_reason!r} "
            f"number_leaks={self.number_leak_count} "
            f"citation_violations={self.citation_violation_count} "
            f"fabricated_citations_dropped={len(self.fabricated_citations_dropped)} "
            f"fabricated_citation_ids={len(self.fabricated_citation_ids)} "
            f"tokens={self.total_tokens} cost_usd={self.total_cost_usd:.4f}"
        )


def _verdict_reason(verifier: VerifierReport | None) -> str:
    if verifier is None:
        return "verifier did not run"
    if verifier.verdict == "pass":
        return ""
    # Name the compliance_hard checks that tripped *first*, ahead of
    # `verifier.notes`. `notes` (pipeline/verifier/node.py) is built only
    # from label/completeness messages -- it never mentions a number leak,
    # a fabricated citation, or a missing disclaimer. Returning it as soon
    # as it was non-empty therefore reported a structural_hard completeness
    # message as the reason for a block that a compliance_hard violation
    # actually caused, and that wrong reason is persisted to
    # `runs.verdict_reason` (pipeline/storage/report_store.py) --
    # see the 2026-08-17 runs, blocked on a fabricated citation but
    # recorded as "5 deep-dive sections (spec requires 3-4)".
    reasons = []
    if verifier.number_leak_violations:
        reasons.append("number leak")
    if verifier.citation_violations or verifier.citation_id_violations:
        reasons.append("fabricated citation")
    if verifier.missing_disclaimer:
        reasons.append("missing disclaimer")
    if verifier.notes:
        reasons.append(verifier.notes)
    elif verifier.completeness_violations:
        reasons.append("completeness violation")
    return "; ".join(reasons) or "unspecified"


def summarize_run(state: ReportState, *, duration_seconds: float) -> RunSummary:
    findings: list[Finding] = state.get("research_findings", [])
    failures: list[FailureNote] = state.get("failures", [])
    verifier: VerifierReport | None = state.get("verifier_report")
    branch_yields: list[BranchYield] = state.get("branch_yields", [])

    return RunSummary(
        run_id=state.get("run_id", ""),
        duration_seconds=duration_seconds,
        company_branches=len(state.get("highlight_tickers", [])),
        macro_branches=len(state.get("macro_topics", [])),
        findings_count=len(findings),
        failure_count=len(failures),
        failures=failures,
        verdict=verifier.verdict if verifier else None,
        number_leak_count=len(verifier.number_leak_violations) if verifier else 0,
        citation_violation_count=(
            len(verifier.citation_violations) + len(verifier.citation_id_violations)
            if verifier
            else 0
        ),
        number_leak_violations=list(verifier.number_leak_violations) if verifier else [],
        citation_violations=list(verifier.citation_violations) if verifier else [],
        completeness_violations=list(verifier.completeness_violations) if verifier else [],
        missing_disclaimer=verifier.missing_disclaimer if verifier else False,
        verifier_notes=verifier.notes if verifier else "",
        fabricated_citations_dropped=list(state.get("fabricated_citations_dropped", [])),
        fabricated_citation_ids=list(state.get("fabricated_citation_ids", [])),
        branch_yields=list(branch_yields),
        # Run-level total: research branches plus the synthesis and
        # verifier LLM calls (pipeline/graph.py's _with_llm_usage) -- not
        # just research, which understated the largest generation in a run.
        total_tokens=(
            sum(b.tokens for b in branch_yields)
            + state.get("synthesis_tokens", 0)
            + state.get("verifier_tokens", 0)
        ),
        total_cost_usd=(
            sum(b.cost_usd for b in branch_yields)
            + state.get("synthesis_cost_usd", 0.0)
            + state.get("verifier_cost_usd", 0.0)
        ),
        verdict_reason=_verdict_reason(verifier),
    )


def check_yield_floor(
    summary: RunSummary,
    trailing_findings_counts: list[int],
    *,
    fraction: float = YIELD_FLOOR_FRACTION,
) -> str | None:
    """R3: warn (never block) when `summary.findings_count` falls below
    `fraction` of the trailing median. Returns the warning message, or None
    if there isn't enough history or the floor wasn't breached -- the caller
    decides whether/how to surface it (this module never raises on it)."""
    if not trailing_findings_counts:
        return None
    sorted_counts = sorted(trailing_findings_counts)
    mid = len(sorted_counts) // 2
    median = (
        sorted_counts[mid]
        if len(sorted_counts) % 2
        else (sorted_counts[mid - 1] + sorted_counts[mid]) / 2
    )
    if median <= 0:
        return None
    floor = median * fraction
    if summary.findings_count >= floor:
        return None
    return (
        f"run_id={summary.run_id} findings={summary.findings_count} is below "
        f"{fraction:.0%} of the trailing median ({median:g}, n={len(trailing_findings_counts)}); "
        "thin-research run"
    )


def _log_verifier_violations(summary: RunSummary) -> None:
    """Log every individual verifier violation behind a non-`pass` verdict.

    Without this, a blocked run's only trace is `citation_violations=1` in
    the one-line summary -- a count with no way to tell which URL or number
    caused it short of re-running the pipeline with custom instrumentation.
    """
    # Logged before the verdict gate below: a dropped citation is a writer
    # fabrication whether or not the surviving report passed, and a `pass`
    # run is exactly the case where nothing else would report it.
    for url in summary.fabricated_citations_dropped:
        logger.warning(
            "synthesis: writer fabricated a citation, dropped before verification: %s", url
        )
    for citation_id in summary.fabricated_citation_ids:
        logger.warning(
            "synthesis: writer cited an id that was never in its source "
            "vocabulary: [%s]",
            citation_id,
        )
    if summary.verdict in (None, "pass"):
        return
    for token in summary.number_leak_violations:
        logger.warning("verifier: number leak (not in scores/financials): %r", token)
    for url in summary.citation_violations:
        logger.warning("verifier: fabricated citation (URL never retrieved): %s", url)
    for violation in summary.completeness_violations:
        logger.warning("verifier: completeness violation: %s", violation)
    if summary.missing_disclaimer:
        logger.warning("verifier: report is missing the required disclaimer phrase")
    if summary.verifier_notes:
        logger.warning("verifier notes: %s", summary.verifier_notes)


def run_with_summary(
    graph: InvocableGraph,
    state_input: dict | None,
    *,
    config: dict | None = None,
    clock: Any = time.monotonic,
    trailing_findings_counts: list[int] | None = None,
) -> tuple[dict, RunSummary]:
    """Times graph.invoke(state_input, config=config), logs a one-line
    summary plus one warning per branch failure, and returns
    (result, RunSummary) so a caller can also inspect it programmatically.

    `trailing_findings_counts` (R3) is injected rather than looked up here --
    this module stays DB-free; the caller (pipeline/run.py) fetches recent
    persisted run rows and passes their findings_count in."""

    start = clock()
    result = graph.invoke(state_input, config=config)
    duration = clock() - start

    summary = summarize_run(result, duration_seconds=duration)
    logger.info(summary.as_log_line())
    if trailing_findings_counts:
        floor_warning = check_yield_floor(summary, trailing_findings_counts)
        if floor_warning:
            logger.warning(floor_warning)
    _log_verifier_violations(summary)
    for failure in summary.failures:
        logger.warning(
            "branch failure: scope=%s ticker=%s topic=%s reason=%s",
            failure.scope,
            failure.ticker,
            failure.topic,
            failure.reason,
        )
    return result, summary
