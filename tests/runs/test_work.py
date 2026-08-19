"""The three units of work (Faz 3.4): that they are wired to the right entry
points, with the right arguments, and that none of them takes ownership of
the run row away from the executor.

The work itself needs API keys behind a credential guard, so what is asserted
here is the wiring -- which is also the only part Faz 3 added.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from techinves.api._time import now_naive_utc
from techinves.db.models import RunRow
from techinves.runs import work
from techinves.runs.service import RunContext


def _ctx(**overrides) -> RunContext:
    kwargs = {
        "run_id": "run-1",
        "trigger_type": "report",
        "ticker": None,
        "log": lambda line: None,
    }
    kwargs.update(overrides)
    return RunContext(**kwargs)


def test_the_report_trigger_runs_the_whole_watchlist(monkeypatch):
    import pipeline.run as run_module

    calls = {}

    def fake_run_pipeline(**kwargs):
        calls.update(kwargs)
        return type("S", (), {"verdict": "pass", "findings_count": 7})()

    monkeypatch.setattr(run_module, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr("pipeline.config.load_watchlist_tickers", lambda: ["AAA", "BBB"])

    work.run_report(_ctx())

    assert calls["tickers"] == ["AAA", "BBB"]
    assert calls["run_id"] == "run-1"
    assert calls["trigger_type"] == "report"
    assert calls["highlight_tickers"] is None


def test_the_company_trigger_narrows_the_same_pipeline(monkeypatch):
    """ADR 0010 §1: the single-ticker action narrows an existing entry point
    rather than adding a second pipeline. Same `run_pipeline`, one ticker,
    and that ticker pinned as the highlight so the run researches what it was
    asked about instead of re-deriving a selection from a list of one."""
    import pipeline.run as run_module

    calls = {}

    def fake_run_pipeline(**kwargs):
        calls.update(kwargs)
        return type("S", (), {"verdict": "pass", "findings_count": 1})()

    monkeypatch.setattr(run_module, "run_pipeline", fake_run_pipeline)

    work.run_company(_ctx(trigger_type="company", ticker="NVDA"))

    assert calls["tickers"] == ["NVDA"]
    assert calls["highlight_tickers"] == ["NVDA"]
    assert calls["trigger_type"] == "company"
    assert calls["ticker"] == "NVDA"


def test_the_company_trigger_requires_a_ticker():
    with pytest.raises(ValueError):
        work.run_company(_ctx(trigger_type="company", ticker=None))


def test_company_names_are_loaded_from_the_canonical_table():
    """`ScoreBlock` carries no company name and `data/watchlist.yaml` holds
    tickers only, so without this every company would be named after its own
    ticker."""
    names = work.load_company_names()
    assert len(names) == 43, "the 43-ticker watchlist should all parse"
    assert "Ticker" not in names  # the table's own header row
    assert names["MSFT"] == "Microsoft"
    assert all(name for name in names.values())


async def test_the_scores_trigger_leaves_the_run_row_to_the_executor(
    session_maker, monkeypatch, tmp_path
):
    """`ingest(manage_run_row=False)`: the executor created the row, holds
    the in-flight lock through it, and lands the terminal status itself.
    `ingest()` writing "succeeded" from underneath would release the lock
    while the executor still considered the run in flight."""
    from techinves.api import ingest as ingest_module

    now = now_naive_utc()
    async with session_maker() as session:
        session.add(
            RunRow(
                run_id="scores-1",
                trigger_type="scores",
                status="running",
                created_at=now,
                started_at=now,
            )
        )
        await session.commit()

    scores_path = tmp_path / "empty-scores.json"
    scores_path.write_text(json.dumps([]), encoding="utf-8")

    monkeypatch.setattr(ingest_module, "get_sessionmaker", lambda engine=None: session_maker)
    count = await ingest_module.ingest(
        None, scores_path, run_id="scores-1", manage_run_row=False
    )

    assert count == 0
    async with session_maker() as session:
        row = (
            await session.execute(select(RunRow).where(RunRow.run_id == "scores-1"))
        ).scalar_one()
    assert row.status == "running"  # untouched -- still in flight, still locked
    assert row.finished_at is None
    assert row.company_count == 0


async def test_manage_run_row_false_refuses_when_no_row_exists(
    session_maker, monkeypatch, tmp_path
):
    """Creating the row here would mint a second, executor-less run and take
    the lock nobody would release."""
    from techinves.api import ingest as ingest_module

    scores_path = tmp_path / "empty-scores.json"
    scores_path.write_text(json.dumps([]), encoding="utf-8")
    monkeypatch.setattr(ingest_module, "get_sessionmaker", lambda engine=None: session_maker)

    with pytest.raises(ingest_module.IngestionValidationError):
        await ingest_module.ingest(
            None, scores_path, run_id="nobody", manage_run_row=False
        )
