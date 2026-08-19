from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver

from pipeline.checkpointer import build_checkpointer, checkpoint_config
from pipeline.schemas import RESET, additive_with_reset


class _AccumulatingState(TypedDict):
    """Module-level (not nested in a test) so its annotations resolve: this
    file's `from __future__ import annotations` defers them to a later
    eval that only sees module globals."""

    findings: Annotated[list, operator.add]


class _ResetAwareState(TypedDict):
    """The real pipeline's channel shape (pipeline/schemas.py). Module-level
    for the same annotation-resolution reason as `_AccumulatingState`."""

    findings: Annotated[list, additive_with_reset]


def test_build_checkpointer_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    saver = build_checkpointer()
    assert isinstance(saver, SqliteSaver)


def test_build_checkpointer_sqlite_url_uses_sqlite(monkeypatch):
    saver = build_checkpointer("sqlite+aiosqlite:///./dev.db")
    assert isinstance(saver, SqliteSaver)


def test_checkpoint_config_keys_by_thread_id():
    assert checkpoint_config("2026-08-10") == {"thread_id": "2026-08-10"}


def test_checkpointed_graph_resumes_after_a_mid_run_failure():
    """R23: a graph compiled with a checkpointer, invoked twice with the
    same thread_id, must not re-run a node that already completed on the
    first (failed) invoke."""
    from langgraph.graph import END, START, StateGraph
    from typing import TypedDict

    calls: list[str] = []

    class State(TypedDict):
        step: str

    def step_a(state: State) -> dict:
        calls.append("a")
        return {"step": "a"}

    def step_b(state: State) -> dict:
        calls.append("b")
        if state.get("step") == "a" and len(calls) == 2:
            raise RuntimeError("simulated mid-run crash")
        return {"step": "b"}

    graph = StateGraph(State)
    graph.add_node("a", step_a)
    graph.add_node("b", step_b)
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)

    import sqlite3

    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    compiled = graph.compile(checkpointer=checkpointer)
    config = {"configurable": checkpoint_config("test-run")}

    try:
        compiled.invoke({"step": "start"}, config=config)
        assert False, "expected the simulated crash"
    except RuntimeError:
        pass

    assert calls == ["a", "b"]

    result = compiled.invoke(None, config=config)  # resume, not restart

    # step "a" ran once (not re-run on resume); step "b" ran again from the
    # checkpointed post-"a" state and succeeded this time.
    assert calls == ["a", "b", "b"]
    assert result["step"] == "b"


def _accumulating_graph():
    """A graph whose one channel uses an additive reducer, like the real
    pipeline's `research_findings`/`branch_yields` (pipeline/schemas.py)."""
    from langgraph.graph import END, START, StateGraph

    def produce(state: _AccumulatingState) -> dict:
        return {"findings": ["f1", "f2", "f3"]}

    graph = StateGraph(_AccumulatingState)
    graph.add_node("produce", produce)
    graph.add_edge(START, "produce")
    graph.add_edge("produce", END)
    return graph


def test_reinvoking_a_completed_thread_accumulates_findings():
    """Characterizes the failure `_resolve_thread` exists to prevent: a
    thread that already ran to completion keeps its reducer-accumulated
    channels, so a second full invoke on the *same* thread_id appends
    rather than starting clean (observed live as findings 33 -> 81 -> 162
    across three attempts at week 2026-08-17)."""
    import sqlite3

    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    compiled = _accumulating_graph().compile(checkpointer=checkpointer)
    config = {"configurable": checkpoint_config("same-thread")}

    first = compiled.invoke({"findings": []}, config=config)
    second = compiled.invoke({"findings": []}, config=config)

    assert len(first["findings"]) == 3
    assert len(second["findings"]) == 6  # the bug: accumulated, not clean


def test_resolve_thread_starts_a_clean_lineage_after_a_terminal_verdict():
    """A completed run (`block` is terminal, not a crash) must not be
    retried on its own thread -- `_resolve_thread` moves it to `#retry1`,
    so the retry's findings are its own."""
    import sqlite3

    from pipeline.run import _resolve_thread

    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    compiled = _accumulating_graph().compile(checkpointer=checkpointer)

    base_config = {"configurable": checkpoint_config("2026-08-17")}
    config, state_input = _resolve_thread(compiled, "2026-08-17", base_config, {"findings": []})
    assert config["configurable"]["thread_id"] == "2026-08-17"
    assert state_input == {"findings": []}
    first = compiled.invoke(state_input, config=config)
    assert len(first["findings"]) == 3

    config, state_input = _resolve_thread(compiled, "2026-08-17", base_config, {"findings": []})
    assert config["configurable"]["thread_id"] == "2026-08-17#retry1"
    assert state_input == {"findings": []}
    second = compiled.invoke(state_input, config=config)
    assert len(second["findings"]) == 3  # clean, not 6

    config, _ = _resolve_thread(compiled, "2026-08-17", base_config, {"findings": []})
    assert config["configurable"]["thread_id"] == "2026-08-17#retry2"


def test_resolve_thread_still_resumes_a_genuinely_crashed_run():
    """R23's original behaviour is unchanged: pending steps mean a crash,
    which resumes in place with `None` rather than starting a new lineage."""
    import sqlite3
    from typing import TypedDict

    from langgraph.graph import END, START, StateGraph

    from pipeline.run import _resolve_thread

    class State(TypedDict):
        step: str

    calls: list[str] = []

    def step_a(state: State) -> dict:
        calls.append("a")
        return {"step": "a"}

    def step_b(state: State) -> dict:
        calls.append("b")
        if len(calls) == 2:
            raise RuntimeError("simulated mid-run crash")
        return {"step": "b"}

    graph = StateGraph(State)
    graph.add_node("a", step_a)
    graph.add_node("b", step_b)
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)

    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    compiled = graph.compile(checkpointer=checkpointer)
    base_config = {"configurable": checkpoint_config("crashed")}

    config, state_input = _resolve_thread(compiled, "crashed", base_config, {"step": "start"})
    assert state_input == {"step": "start"}
    try:
        compiled.invoke(state_input, config=config)
        assert False, "expected the simulated crash"
    except RuntimeError:
        pass

    config, state_input = _resolve_thread(compiled, "crashed", base_config, {"step": "start"})
    assert config["configurable"]["thread_id"] == "crashed"  # same thread
    assert state_input is None  # resume, not restart
    assert compiled.invoke(state_input, config=config)["step"] == "b"
    assert calls == ["a", "b", "b"]  # "a" never re-ran


def test_resolve_thread_passes_through_a_graph_without_checkpointing():
    """An injected fake graph (every pipeline test) has no `get_state` --
    it must get the config and full state it would have got before."""
    from pipeline.run import _resolve_thread

    class FakeGraph:
        pass

    base_config = {"configurable": checkpoint_config("x")}
    config, state_input = _resolve_thread(FakeGraph(), "x", base_config, {"a": 1})
    assert config is base_config
    assert state_input == {"a": 1}


def test_resolve_thread_degrades_to_no_thread_when_the_checkpointer_errors():
    """F2: a transient checkpointer read error used to fall back onto the
    *original* thread_id -- which, if it held a completed checkpoint, is
    exactly the accumulation path. Fail safe: run with no checkpoint thread
    rather than a possibly-dirty one."""
    from pipeline.run import _resolve_thread

    class FlakyGraph:
        def get_state(self, config):
            raise RuntimeError("checkpointer unavailable")

    base_config = {"max_concurrency": 4, "configurable": checkpoint_config("2026-08-17")}
    config, state_input = _resolve_thread(
        FlakyGraph(), "2026-08-17", base_config, {"findings": []}
    )
    assert "configurable" not in config
    assert config["max_concurrency"] == 4
    assert state_input == {"findings": []}


def _reset_aware_graph():
    """The real pipeline's shape: an `init` node that clears the accumulators
    (pipeline/graph.py's `init_node`), then a producer that fans into them."""
    from langgraph.graph import END, START, StateGraph

    def init(state: _ResetAwareState) -> dict:
        return {"findings": RESET}

    def produce(state: _ResetAwareState) -> dict:
        return {"findings": ["f1", "f2", "f3"]}

    graph = StateGraph(_ResetAwareState)
    graph.add_node("init", init)
    graph.add_node("produce", produce)
    graph.add_edge(START, "init")
    graph.add_edge("init", "produce")
    graph.add_edge("produce", END)
    return graph


def test_reset_aware_reducers_keep_a_reused_thread_from_accumulating():
    """F1: idempotency is a property of the *graph*, not of one caller.
    `_resolve_thread` is what kept the live pipeline correct, so any other
    caller invoking the compiled graph directly on an existing thread (a
    resume endpoint, a repair loop, a notebook) still reproduced the observed
    33 -> 81 -> 162 accumulation. With the reset sentinel it cannot."""
    import sqlite3

    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    compiled = _reset_aware_graph().compile(checkpointer=checkpointer)
    config = {"configurable": checkpoint_config("same-thread")}

    counts = [len(compiled.invoke({"findings": []}, config=config)["findings"]) for _ in range(3)]
    assert counts == [3, 3, 3]


def test_the_real_graph_is_idempotent_against_a_checkpointer():
    """F14: both graph smoke tests build `build_graph(...)` without a
    checkpointer, so `init_node`'s emission into the fan-in reducers had no
    test that would catch it doubling. This is that test, on the real graph."""
    import sqlite3

    from pipeline.fixtures.mock_data import MOCK_FINANCIALS, MOCK_SCORES
    from pipeline.graph import build_graph
    from pipeline.research.agent import FindingsBatch
    from pipeline.schemas import Finding
    from pipeline.verifier.node import LLMConsistencyReview
    from tests.pipeline.conftest import FakeSearcher
    from tests.pipeline.test_graph_smoke import ScriptedLLM

    llm = ScriptedLLM(
        findings_batch=FindingsBatch(
            findings=[
                Finding(
                    scope="company",
                    ticker="NVDA",
                    event_title="Chip launch",
                    event_type="product",
                    narrative="NVDA launched a new chip this week.",
                    source_urls=["https://reuters.com/nvda"],
                )
            ]
        ),
        report_text="# Weekly Report\n\nnot investment advice\n",
        review=LLMConsistencyReview(section_scores=[], notes=""),
    )
    searcher = FakeSearcher(
        results=[{"title": "NVDA", "url": "https://reuters.com/nvda", "content": "..."}]
    )
    checkpointer = SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False))
    graph = build_graph(searcher=searcher, llm=llm, checkpointer=checkpointer)

    state = {
        "run_id": "test-run",
        "as_of": "2026-08-10",
        "highlight_tickers": ["NVDA"],
        "macro_topics": [],
        "covered_events": [],
        "scores": {"NVDA": MOCK_SCORES["NVDA"]},
        "financials": {"NVDA": MOCK_FINANCIALS["NVDA"]},
        "scoring_eligible_tickers": ["NVDA"],
    }
    config = {"configurable": checkpoint_config("reused-thread")}

    first = graph.invoke(dict(state), config=config)
    second = graph.invoke(dict(state), config=config)

    assert len(first["research_findings"]) == 1
    assert len(second["research_findings"]) == 1  # not 2, and not 3
    assert len(second["branch_yields"]) == 1
    assert len(second["retrieved_urls"]) == 1
