from __future__ import annotations

from pipeline.observability import (
    _log_verifier_violations,
    check_yield_floor,
    run_with_summary,
    summarize_run,
)
from pipeline.schemas import BranchYield, FailureNote, Finding, VerifierReport


def _state(**overrides) -> dict:
    base = dict(
        run_id="2026-08-10",
        highlight_tickers=["NVDA", "MSFT"],
        macro_topics=["Fed policy"],
        research_findings=[
            Finding(
                scope="company",
                ticker="NVDA",
                event_title="New product",
                event_type="product",
                narrative="...",
            )
        ],
        failures=[
            FailureNote(scope="company", ticker="MSFT", reason="Tavily timeout")
        ],
        verifier_report=VerifierReport(
            verdict="pass_with_flags",
            number_leak_violations=[],
            citation_violations=["https://example.com/bad"],
        ),
    )
    base.update(overrides)
    return base


def test_summarize_run_counts_branches_findings_and_failures():
    summary = summarize_run(_state(), duration_seconds=12.5)

    assert summary.run_id == "2026-08-10"
    assert summary.duration_seconds == 12.5
    assert summary.company_branches == 2
    assert summary.macro_branches == 1
    assert summary.findings_count == 1
    assert summary.failure_count == 1
    assert summary.verdict == "pass_with_flags"
    assert summary.citation_violation_count == 1


def test_summarize_run_handles_missing_verifier_report():
    state = _state(verifier_report=None)
    summary = summarize_run(state, duration_seconds=1.0)

    assert summary.verdict is None
    assert summary.number_leak_count == 0
    assert summary.citation_violation_count == 0


def test_as_log_line_is_a_single_line_string():
    summary = summarize_run(_state(), duration_seconds=3.0)
    line = summary.as_log_line()

    assert "\n" not in line
    assert "run_id=2026-08-10" in line
    assert "verdict=pass_with_flags" in line


class _FakeGraph:
    def __init__(self, result: dict):
        self._result = result
        self.calls: list[tuple[dict, dict | None]] = []

    def invoke(self, state_input: dict, config: dict | None = None) -> dict:
        self.calls.append((state_input, config))
        return self._result


def test_run_with_summary_times_and_returns_result_and_summary():
    graph = _FakeGraph(_state())
    ticks = iter([100.0, 107.5])

    result, summary = run_with_summary(
        graph, {"run_id": "2026-08-10"}, config={"max_concurrency": 6}, clock=lambda: next(ticks)
    )

    assert result == _state()
    assert summary.duration_seconds == 7.5
    assert graph.calls == [({"run_id": "2026-08-10"}, {"max_concurrency": 6})]


def test_summarize_run_rolls_up_branch_yields_into_totals():
    branch_yields = [
        BranchYield(scope="company", ticker="NVDA", findings_count=2, tokens=100, cost_usd=0.01),
        BranchYield(scope="macro", topic="Fed policy", findings_count=1, tokens=50, cost_usd=0.005),
    ]
    summary = summarize_run(_state(branch_yields=branch_yields), duration_seconds=1.0)

    assert summary.branch_yields == branch_yields
    assert summary.total_tokens == 150
    assert summary.total_cost_usd == 0.015


def test_summarize_run_derives_verdict_reason_from_notes():
    state = _state(
        verifier_report=VerifierReport(verdict="pass_with_flags", notes="thin section: NVDA")
    )
    summary = summarize_run(state, duration_seconds=1.0)
    assert summary.verdict_reason == "thin section: NVDA"


def test_summarize_run_verdict_reason_falls_back_to_violation_name_when_notes_empty():
    summary = summarize_run(_state(), duration_seconds=1.0)  # citation_violations, no notes
    assert summary.verdict_reason == "fabricated citation"


def test_summarize_run_verdict_reason_empty_on_pass():
    state = _state(verifier_report=VerifierReport(verdict="pass"))
    summary = summarize_run(state, duration_seconds=1.0)
    assert summary.verdict_reason == ""


def test_summarize_run_verdict_reason_falls_back_when_notes_empty():
    state = _state(
        verifier_report=VerifierReport(verdict="block", missing_disclaimer=True, notes="")
    )
    summary = summarize_run(state, duration_seconds=1.0)
    assert "missing disclaimer" in summary.verdict_reason


def test_summarize_run_verdict_reason_none_when_no_verifier():
    summary = summarize_run(_state(verifier_report=None), duration_seconds=1.0)
    assert summary.verdict_reason == "verifier did not run"


def test_check_yield_floor_warns_below_fraction_of_median():
    summary = summarize_run(_state(research_findings=[]), duration_seconds=1.0)
    warning = check_yield_floor(summary, [10, 12, 11], fraction=0.5)
    assert warning is not None
    assert "below" in warning


def test_check_yield_floor_silent_when_above_floor():
    summary = summarize_run(_state(), duration_seconds=1.0)  # 1 finding
    warning = check_yield_floor(summary, [1, 1, 2], fraction=0.5)
    assert warning is None


def test_check_yield_floor_silent_with_no_history():
    summary = summarize_run(_state(research_findings=[]), duration_seconds=1.0)
    assert check_yield_floor(summary, []) is None


def test_run_with_summary_logs_yield_floor_warning(caplog):
    graph = _FakeGraph(_state(research_findings=[]))
    with caplog.at_level("WARNING"):
        run_with_summary(
            graph, {"run_id": "2026-08-10"}, trailing_findings_counts=[10, 10, 10]
        )
    assert any("below" in message for message in caplog.messages)


def test_verdict_reason_names_the_blocking_violation_ahead_of_notes():
    """A compliance_hard violation is what forces `block`, but `notes`
    (verifier/node.py) only ever carries label/completeness messages. The
    reason must name the citation, not the completeness note it happened to
    be logged alongside -- the 2026-08-17 runs recorded
    "5 deep-dive sections (spec requires 3-4)" for a run blocked on a
    fabricated citation, and that string is persisted to
    runs.verdict_reason."""
    summary = summarize_run(
        _state(
            verifier_report=VerifierReport(
                verdict="block",
                citation_violations=["https://example.com/invented"],
                completeness_violations=["5 deep-dive sections (spec requires 3-4)"],
                notes="5 deep-dive sections (spec requires 3-4)",
            )
        ),
        duration_seconds=1.0,
    )
    assert summary.verdict_reason.startswith("fabricated citation")
    assert "5 deep-dive sections" in summary.verdict_reason


def test_verdict_reason_names_every_compliance_check_that_tripped():
    summary = summarize_run(
        _state(
            verifier_report=VerifierReport(
                verdict="block",
                number_leak_violations=["42"],
                citation_violations=["https://example.com/invented"],
                missing_disclaimer=True,
                notes="thin section: NVDA",
            )
        ),
        duration_seconds=1.0,
    )
    assert summary.verdict_reason == (
        "number leak; fabricated citation; missing disclaimer; thin section: NVDA"
    )


def test_summary_carries_and_logs_fabricated_citations_even_on_pass(caplog):
    """The strip in synthesis means a fabrication no longer reaches the
    verifier -- so it must surface here, including on a clean `pass`, or
    making the pipeline resilient would also make the problem invisible."""
    summary = summarize_run(
        _state(
            fabricated_citations_dropped=["https://www.reuters.com/invented"],
            verifier_report=VerifierReport(verdict="pass"),
        ),
        duration_seconds=1.0,
    )
    assert summary.fabricated_citations_dropped == ["https://www.reuters.com/invented"]
    assert "fabricated_citations_dropped=1" in summary.as_log_line()

    with caplog.at_level("WARNING"):
        _log_verifier_violations(summary)
    assert "https://www.reuters.com/invented" in caplog.text
