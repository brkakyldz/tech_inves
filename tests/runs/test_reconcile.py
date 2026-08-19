"""Startup reconciliation (Faz 3.2), and the single-process assumption it
rests on."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from techinves.api._time import now_naive_utc
from techinves.db.models import RunRow
from techinves.runs import reconcile as reconcile_module
from techinves.runs.reconcile import (
    RESTART_ERROR,
    MultiWorkerRefused,
    ReconciliationFailed,
    detect_worker_count,
    reconcile_stale_runs,
    refuse_if_multi_worker,
)
from techinves.runs.service import RunService


async def _add(session_maker, run_id: str, *, trigger_type: str, status: str) -> None:
    now = now_naive_utc()
    async with session_maker() as session:
        session.add(
            RunRow(
                run_id=run_id,
                trigger_type=trigger_type,
                status=status,
                created_at=now,
                started_at=now if status != "queued" else None,
                finished_at=now if status in ("succeeded", "failed") else None,
            )
        )
        await session.commit()


async def test_terminal_rows_are_left_alone(session_maker):
    """Reconciliation touches only the non-terminal statuses. A `succeeded`
    or `failed` row holds no lock and carries real history -- rewriting its
    error would destroy the record of why it actually failed."""
    await _add(session_maker, "done", trigger_type="report", status="succeeded")
    await _add(session_maker, "broke", trigger_type="scores", status="failed")

    assert await reconcile_stale_runs(session_maker) == []

    async with session_maker() as session:
        rows = {r.run_id: r for r in (await session.execute(select(RunRow))).scalars()}
    assert rows["done"].status == "succeeded"
    assert rows["broke"].status == "failed"
    assert rows["broke"].error is None


async def test_every_non_terminal_row_is_reconciled_without_a_liveness_check(session_maker):
    """The single-process assumption: at startup no executor exists by
    construction, so every `queued`/`running` row is *provably* stale. There
    is deliberately no "is something still executing this?" heuristic --
    there is nothing alive to ask."""
    await _add(session_maker, "r1", trigger_type="report", status="running")
    await _add(session_maker, "r2", trigger_type="scores", status="queued")
    await _add(session_maker, "r3", trigger_type="company", status="running")

    stale = await reconcile_stale_runs(session_maker)

    assert sorted(stale) == ["r1", "r2", "r3"]
    async with session_maker() as session:
        rows = (await session.execute(select(RunRow))).scalars().all()
    assert {r.status for r in rows} == {"failed"}
    assert all(r.finished_at is not None for r in rows)
    assert all(r.error == RESTART_ERROR for r in rows)


async def test_the_restart_reason_is_distinguishable_from_a_genuine_failure():
    """A genuine failure records `"<ExceptionType>: <message>"`. The restart
    reason is a fixed sentence that says plainly what happened, so a reader
    of the run history never has to guess which kind of failure they are
    looking at."""
    assert "restarted" in RESTART_ERROR
    assert "did not fail on its own" in RESTART_ERROR
    assert not RESTART_ERROR.startswith(("RuntimeError", "Exception", "ValueError"))


async def test_reconciliation_failure_is_loud(session_maker):
    """If reconciliation cannot complete, the app must fail rather than start
    with an unknown lock state -- the quiet alternative is refusing every
    trigger forever with no explanation."""

    class Boom:
        def __call__(self):
            raise OSError("database file is locked")

    with pytest.raises(ReconciliationFailed) as excinfo:
        await reconcile_stale_runs(Boom())
    assert "must not serve" in str(excinfo.value)
    assert "database file is locked" in str(excinfo.value)

    # A service whose startup raised has not opened for business.
    service = RunService(session_maker=Boom(), work_registry={})
    with pytest.raises(ReconciliationFailed):
        await service.startup()
    assert service.reconciled is False


# --- the multi-worker case --------------------------------------------------


@pytest.mark.parametrize(
    "argv,expected",
    [
        (["uvicorn", "techinves.api.main:app"], None),
        (["uvicorn", "app", "--workers", "4"], 4),
        (["uvicorn", "app", "--workers=4"], 4),
        (["gunicorn", "-w", "2", "app"], 2),
        (["uvicorn", "app", "--workers", "1"], 1),
        (["uvicorn", "app", "--workers", "many"], None),
    ],
)
def test_worker_count_is_read_from_the_command_line(argv, expected):
    assert detect_worker_count(argv=argv, env={}) == expected


def test_worker_count_falls_back_to_web_concurrency():
    assert detect_worker_count(argv=["uvicorn"], env={"WEB_CONCURRENCY": "3"}) == 3
    assert detect_worker_count(argv=["uvicorn"], env={"WEB_CONCURRENCY": ""}) is None


def test_multiple_workers_are_refused_at_startup():
    """Each worker would reconcile the *other* workers' live runs to failed,
    releasing locks out from under running work -- reconciliation would go
    from a repair to a corruption. Detected cheaply from process
    configuration and refused with a message that says what to do."""
    with pytest.raises(MultiWorkerRefused) as excinfo:
        refuse_if_multi_worker(argv=["uvicorn", "app", "--workers", "4"], env={})
    message = str(excinfo.value)
    assert "single process" in message
    assert "--workers" in message

    with pytest.raises(MultiWorkerRefused):
        refuse_if_multi_worker(argv=["uvicorn", "app"], env={"WEB_CONCURRENCY": "2"})

    # One worker, or nothing said, is the supported configuration.
    refuse_if_multi_worker(argv=["uvicorn", "app", "--workers", "1"], env={})
    refuse_if_multi_worker(argv=["uvicorn", "app"], env={})


async def test_a_multi_worker_process_writes_nothing_before_refusing(
    session_maker, monkeypatch
):
    """The refusal happens before any row is touched: a process that should
    not be running must not have reconciled half the table on its way to
    saying so."""
    await _add(session_maker, "live-elsewhere", trigger_type="report", status="running")
    monkeypatch.setattr(reconcile_module.sys, "argv", ["uvicorn", "app", "--workers", "2"])

    with pytest.raises(MultiWorkerRefused):
        await reconcile_stale_runs(session_maker)

    async with session_maker() as session:
        row = (
            await session.execute(select(RunRow).where(RunRow.run_id == "live-elsewhere"))
        ).scalar_one()
    assert row.status == "running"
