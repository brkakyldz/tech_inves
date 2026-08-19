from __future__ import annotations

import pytest

from pipeline.graph import default_run_config
from pipeline.run import _parse_args, main, make_run_id, run_pipeline
from pipeline.schemas import Finding, VerifierReport
from pipeline.storage.covered_events_store import load_covered_events


class _FakeGraph:
    def __init__(self, result: dict):
        self._result = result
        self.calls: list[tuple[dict, dict | None]] = []

    def invoke(self, state_input: dict, config: dict | None = None) -> dict:
        self.calls.append((state_input, config))
        return self._result


def test_run_pipeline_uses_injected_graph_and_saves_covered_events(tmp_path):
    covered_events_path = tmp_path / "covered_events.json"
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="New product launch",
        event_type="product",
        narrative="NVDA announced a new chip.",
        source_urls=["https://reuters.com/nvda"],
    )
    graph = _FakeGraph(
        {
            "run_id": "2026-08-10",
            "research_findings": [finding],
            "failures": [],
            "verifier_report": None,
        }
    )

    summary = run_pipeline(
        tickers=["NVDA"],
        run_id="run-1",
        covered_events_path=covered_events_path,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    assert summary.findings_count == 1
    assert len(graph.calls) == 1
    state_input, config = graph.calls[0]
    assert state_input["highlight_tickers"] == ["NVDA"]
    # The checkpoint thread is the run id, not the week.
    assert config == {**default_run_config(), "configurable": {"thread_id": "run-1"}}

    saved = load_covered_events(path=covered_events_path)
    assert len(saved) == 1
    assert saved[0].company == "NVDA"
    assert saved[0].first_covered_run == "run-1"


def test_run_pipeline_carries_forward_covered_events_across_runs(tmp_path):
    covered_events_path = tmp_path / "covered_events.json"
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="New product launch",
        event_type="product",
        narrative="NVDA announced a new chip.",
    )
    graph = _FakeGraph(
        {"research_findings": [finding], "failures": [], "verifier_report": None}
    )

    run_pipeline(
        tickers=["NVDA"],
        run_id="run-1",
        covered_events_path=covered_events_path,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )
    run_pipeline(
        tickers=["NVDA"],
        run_id="run-2",
        covered_events_path=covered_events_path,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    # ADR 0010 §9: the re-key must not cost de-duplication. Two consecutive
    # runs finding the same event still produce one covered event.
    saved = load_covered_events(path=covered_events_path)
    assert len(saved) == 1
    assert saved[0].first_covered_run == "run-1"
    assert saved[0].last_updated_run == "run-2"


def test_blocked_run_does_not_mark_its_events_as_covered(tmp_path):
    """A verdict=block run produced no usable report, so its events must stay
    uncovered -- otherwise they are silently never researched again."""
    covered_events_path = tmp_path / "covered_events.json"
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="New product launch",
        event_type="product",
        narrative="NVDA announced a new chip.",
    )
    graph = _FakeGraph(
        {
            "run_id": "2026-08-10",
            "research_findings": [finding],
            "failures": [],
            "verifier_report": VerifierReport(
                verdict="block", number_leak_violations=["fabricated revenue figure"]
            ),
        }
    )

    summary = run_pipeline(
        tickers=["NVDA"],
        run_id="run-1",
        covered_events_path=covered_events_path,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    assert summary.verdict == "block"
    assert not covered_events_path.exists()
    assert load_covered_events(path=covered_events_path) == []


def test_blocked_run_leaves_previously_covered_events_untouched(tmp_path):
    """The block path must not drop what earlier passing runs already covered."""
    covered_events_path = tmp_path / "covered_events.json"
    old_finding = Finding(
        scope="company",
        ticker="MSFT",
        event_title="Cloud expansion",
        event_type="product",
        narrative="MSFT expanded a datacenter region.",
    )
    passing_graph = _FakeGraph(
        {"research_findings": [old_finding], "failures": [], "verifier_report": None}
    )
    run_pipeline(
        tickers=["MSFT"],
        run_id="run-1",
        covered_events_path=covered_events_path,
        graph=passing_graph,
        scores={},
        financials={},
        highlight_tickers=["MSFT"],
    )

    new_finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="New product launch",
        event_type="product",
        narrative="NVDA announced a new chip.",
    )
    blocking_graph = _FakeGraph(
        {
            "research_findings": [new_finding],
            "failures": [],
            "verifier_report": VerifierReport(verdict="block"),
        }
    )
    run_pipeline(
        tickers=["NVDA"],
        run_id="run-2",
        covered_events_path=covered_events_path,
        graph=blocking_graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    saved = load_covered_events(path=covered_events_path)
    assert [e.company for e in saved] == ["MSFT"]
    assert saved[0].last_updated_run == "run-1"


def test_help_does_not_require_api_keys(monkeypatch, capsys):
    """`--help` must print usage and exit 0 even with no API keys set --
    argparse's SystemExit(0) has to win over key validation."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--tickers" in out
    assert "Config error" not in out


def test_missing_api_keys_still_fail_a_real_run(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    assert main(["--label", "run-1"]) == 1
    assert "Config error" in capsys.readouterr().err


def test_missing_fmp_key_names_it_and_refuses(monkeypatch, capsys):
    """Faz 6: the CLI refuses with the same missing-key reason
    `POST /v1/runs` would give a `report` trigger for -- FMP included, even
    though `run_pipeline` never calls the FMP API directly (scores load from
    the DB). `techinves.runs.keys` is the shared source of truth."""
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "x")

    assert main(["--label", "run-1"]) == 1
    err = capsys.readouterr().err
    assert "Config error" in err
    assert "FMP_API_KEY" in err


def test_parse_args_defaults_to_no_explicit_tickers():
    args = _parse_args([])
    assert args.tickers is None
    assert args.label is None


def test_parse_args_accepts_explicit_tickers():
    args = _parse_args(["--tickers", "NVDA", "MSFT"])
    assert args.tickers == ["NVDA", "MSFT"]


def test_parse_args_accepts_an_optional_run_label():
    """`--week` is retired (ADR 0010 §2). What is left is an optional label
    that names the run."""
    args = _parse_args(["--label", "before-the-fed-meeting"])
    assert args.label == "before-the-fed-meeting"


def test_make_run_id_uses_an_explicit_label_verbatim():
    """A label *is* the run id, so a deliberate retry under the same label
    upserts that run rather than accumulating near-duplicates."""
    assert make_run_id("nightly") == "nightly"


def test_make_run_id_generates_distinct_ids_for_unlabelled_runs():
    """Two runs on the same afternoon are the normal case for a tool with a
    button; under week keying they collided."""
    assert make_run_id() != make_run_id()


def test_two_runs_in_a_row_are_both_allowed(tmp_path):
    """The behaviour the deleted re-run guard used to forbid.

    `_guard_rerun` refused a second run for an already-reported week, and
    `--force` overrode it. Both are gone with week identity: each run has its
    own id, its own checkpoint thread and its own report, so the second run
    simply runs. (The lock that *does* still belong here -- one in-flight run
    per trigger type, ADR 0010 §4 -- is Faz 3.3 and needs the background
    executor first.)
    """
    covered_events_path = tmp_path / "covered_events.json"
    finding = Finding(
        scope="company",
        ticker="NVDA",
        event_title="New product launch",
        event_type="product",
        narrative="NVDA announced a new chip.",
    )
    graph = _FakeGraph(
        {"research_findings": [finding], "failures": [], "verifier_report": None}
    )

    first = run_pipeline(
        tickers=["NVDA"],
        run_id="morning",
        covered_events_path=covered_events_path,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )
    second = run_pipeline(
        tickers=["NVDA"],
        run_id="afternoon",
        covered_events_path=covered_events_path,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    assert first.findings_count == second.findings_count == 1
    assert [c[1]["configurable"]["thread_id"] for c in graph.calls] == [
        "morning",
        "afternoon",
    ]


def test_run_pipeline_mints_a_run_id_when_none_is_given(tmp_path):
    graph = _FakeGraph({"research_findings": [], "failures": [], "verifier_report": None})

    run_pipeline(
        tickers=["NVDA"],
        covered_events_path=tmp_path / "covered_events.json",
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    state_input, config = graph.calls[0]
    assert state_input["run_id"]
    assert config["configurable"]["thread_id"] == state_input["run_id"]


def test_file_store_mode_writes_no_run_row(tmp_path, monkeypatch):
    """Tests and local file-store runs have no DB to write to."""
    import pipeline.run as run_module

    def _boom(*args, **kwargs):
        raise AssertionError("the run row is DB-mode only")

    monkeypatch.setattr(run_module, "start_run", _boom)

    summary = run_pipeline(
        tickers=["NVDA"],
        run_id="run-1",
        covered_events_path=tmp_path / "covered_events.json",
        graph=_FakeGraph(
            {"run_id": "r", "research_findings": [], "failures": [], "verifier_report": None}
        ),
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    assert summary.findings_count == 0


def test_cli_passes_the_label_through_as_the_run_id(monkeypatch):
    import pipeline.run as run_module

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

    seen: dict = {}

    def _capture(**kwargs):
        seen.update(kwargs)
        raise _Stop()

    class _Stop(RuntimeError):
        pass

    monkeypatch.setattr(run_module, "run_pipeline", _capture)

    with pytest.raises(_Stop):
        main(["--label", "nightly", "--tickers", "NVDA"])
    assert seen["run_id"] == "nightly"

    seen.clear()
    with pytest.raises(_Stop):
        main(["--tickers", "NVDA"])
    assert seen["run_id"]  # generated, not empty


def _stub_db_persistence(monkeypatch) -> dict:
    """Neutralise every DB write in `run_pipeline`'s persistence tail except
    the report save, which is what these tests are about."""
    from pipeline import run as run_module

    saved: dict = {}
    monkeypatch.setattr(run_module, "load_covered_events_db", lambda: [])
    monkeypatch.setattr(run_module, "save_covered_events_db", lambda events: None)
    monkeypatch.setattr(run_module, "save_run_summary", lambda summary, run_id=None: None)
    monkeypatch.setattr(run_module, "start_run", lambda *a, **k: None)
    monkeypatch.setattr(run_module, "trailing_findings_counts", lambda **k: [])
    monkeypatch.setattr(
        run_module, "load_watchlist_tickers", lambda: ["NVDA", "AMD"]
    )

    def _fake_save(**kwargs):
        saved.update(kwargs)
        return f"run-{kwargs['run_id']}"

    monkeypatch.setattr(run_module, "save_draft_report", _fake_save)
    return saved


def test_blocked_run_persists_its_report_with_the_violations_named(monkeypatch):
    """ADR 0010 §6, the reversal Faz 5.3 implements: a blocked draft is no
    longer withheld. It is written to `reports` carrying `verdict="block"`
    *and* the classified violation list, because that pair is what the
    reader-facing banner renders. A blocked report reaching the store without
    them would be indistinguishable from a clean one."""
    saved = _stub_db_persistence(monkeypatch)

    from pipeline.schemas import VerifierViolation

    violation = VerifierViolation(
        severity="compliance_hard",
        category="citation",
        message="fabricated citation (URL never retrieved): https://example.com/x",
        section="NVDA",
    )
    graph = _FakeGraph(
        {
            "run_id": "run-b",
            "research_findings": [],
            "failures": [],
            "draft_report": "# TechInves Weekly\n\nSome prose.\n",
            "verifier_report": VerifierReport(verdict="block", violations=[violation]),
        }
    )

    summary = run_pipeline(
        tickers=["NVDA", "AMD"],
        run_id="run-b",
        covered_events_path=None,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    assert summary.verdict == "block"
    assert saved, "a blocked run wrote no report at all"
    assert saved["verifier_verdict"] == "block"
    assert saved["verifier_violations"] == [violation.model_dump(mode="json")]


def test_degraded_publish_run_persists_its_structural_violations(monkeypatch):
    """`degraded_publish` was already persisted; what is new is that its
    structural-hard violations travel with it. Without them the report
    renders looking finished, which is the silent-shipping failure the
    banner's `serious` level exists to prevent."""
    saved = _stub_db_persistence(monkeypatch)

    from pipeline.schemas import VerifierViolation

    violation = VerifierViolation(
        severity="structural_hard",
        category="completeness",
        message="watchlist ticker never mentioned: AMD",
    )
    graph = _FakeGraph(
        {
            "run_id": "run-c",
            "research_findings": [],
            "failures": [],
            "draft_report": "# TechInves Weekly\n\nSome prose.\n",
            "verifier_report": VerifierReport(
                verdict="degraded_publish", violations=[violation]
            ),
        }
    )

    run_pipeline(
        tickers=["NVDA", "AMD"],
        run_id="run-c",
        covered_events_path=None,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    assert saved["verifier_verdict"] == "degraded_publish"
    assert saved["verifier_violations"] == [violation.model_dump(mode="json")]


def test_a_run_that_reached_no_verdict_writes_no_report(monkeypatch):
    """Where the `None` verdict the banner handles does *not* come from.

    A run whose verifier produced nothing has no verdict, and
    `REPORTED_VERDICTS` does not contain `None`, so no report row is written
    at all -- there is nothing for a reader to mistake for a finished report.
    The null-verdict rows the banner's `unknown` level exists for come from
    elsewhere: seeded fixtures, and rows written before the verdict columns
    existed. Asserted rather than assumed, because "no verdict" quietly
    becoming a stored row with `verifier_verdict=NULL` would be a new source
    of unverified reports, and the pipeline is not the layer that should
    start producing them."""
    saved = _stub_db_persistence(monkeypatch)

    graph = _FakeGraph(
        {
            "run_id": "run-d",
            "research_findings": [],
            "failures": [],
            "draft_report": "# TechInves Weekly\n\nSome prose.\n",
            "verifier_report": None,
        }
    )

    run_pipeline(
        tickers=["NVDA", "AMD"],
        run_id="run-d",
        covered_events_path=None,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    assert saved == {}


class _ExplodingGraph:
    def invoke(self, state_input: dict, config: dict | None = None) -> dict:
        raise RuntimeError("simulated graph failure")


def test_a_crashed_run_lands_its_row_terminal_instead_of_holding_the_lock(monkeypatch):
    """ADR 0010 §4: the in-flight lock is a partial unique index over
    *non-terminal* run rows, so a run that raises without landing one holds
    it forever. `techinves.runs`' background executor guards its own path;
    the CLI did not, so a crashed CLI run locked out the UI's "Generate
    report" button until the API was restarted."""
    from pipeline import run as run_module

    _stub_db_persistence(monkeypatch)
    failed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        run_module, "fail_run", lambda run_id, *, error: failed.append((run_id, error))
    )

    with pytest.raises(RuntimeError, match="simulated graph failure"):
        run_pipeline(
            tickers=["NVDA"],
            run_id="run-crash",
            covered_events_path=None,
            graph=_ExplodingGraph(),
            scores={},
            financials={},
            highlight_tickers=["NVDA"],
        )

    assert len(failed) == 1
    assert failed[0][0] == "run-crash"
    assert "simulated graph failure" in failed[0][1]


def test_a_failure_to_record_the_failure_never_masks_the_original(monkeypatch):
    """The exception path must not swap a real traceback for a DB error."""
    from pipeline import run as run_module

    _stub_db_persistence(monkeypatch)

    def _boom(*args, **kwargs):
        raise OSError("database is gone too")

    monkeypatch.setattr(run_module, "fail_run", _boom)

    with pytest.raises(RuntimeError, match="simulated graph failure"):
        run_pipeline(
            tickers=["NVDA"],
            run_id="run-crash",
            covered_events_path=None,
            graph=_ExplodingGraph(),
            scores={},
            financials={},
            highlight_tickers=["NVDA"],
        )


def test_file_store_mode_never_touches_the_run_row_on_a_crash(tmp_path, monkeypatch):
    """No row was created, so there is no lock to release."""
    from pipeline import run as run_module

    def _boom(*args, **kwargs):
        raise AssertionError("the run row is DB-mode only")

    monkeypatch.setattr(run_module, "fail_run", _boom)
    monkeypatch.setattr(run_module, "start_run", _boom)

    with pytest.raises(RuntimeError, match="simulated graph failure"):
        run_pipeline(
            tickers=["NVDA"],
            run_id="run-crash",
            covered_events_path=tmp_path / "covered_events.json",
            graph=_ExplodingGraph(),
            scores={},
            financials={},
            highlight_tickers=["NVDA"],
        )


def test_the_terminal_status_is_written_after_the_report_not_before(monkeypatch):
    """`save_run_summary` lands `status="succeeded"`, which releases the
    in-flight lock. Called at the top of the persistence tail it declared the
    run finished while covered-events persistence, placeholder resolution and
    the report write were still ahead of it, so a second trigger was accepted
    mid-work (ADR 0010 §4)."""
    from pipeline import run as run_module

    order: list[str] = []
    saved = _stub_db_persistence(monkeypatch)
    monkeypatch.setattr(
        run_module,
        "save_run_summary",
        lambda summary, run_id=None: order.append("summary"),
    )
    real_save_draft = run_module.save_draft_report

    def _tracking_save(**kwargs):
        order.append("report")
        return real_save_draft(**kwargs)

    monkeypatch.setattr(run_module, "save_draft_report", _tracking_save)
    monkeypatch.setattr(
        run_module,
        "save_covered_events_db",
        lambda events: order.append("covered_events"),
    )

    graph = _FakeGraph(
        {
            "run_id": "run-order",
            "research_findings": [],
            "failures": [],
            "draft_report": "# TechInves Weekly\n\nSome prose.\n",
            "verifier_report": VerifierReport(verdict="pass"),
        }
    )

    run_pipeline(
        tickers=["NVDA", "AMD"],
        run_id="run-order",
        covered_events_path=None,
        graph=graph,
        scores={},
        financials={},
        highlight_tickers=["NVDA"],
    )

    assert saved, "the report should still have been written"
    assert order == ["covered_events", "report", "summary"]
