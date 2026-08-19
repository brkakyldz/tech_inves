"""Faz 3's gate, proven with the real work mocked.

The three units of work need `FMP_API_KEY`/`OPENAI_API_KEY`/`TAVILY_API_KEY`,
which live behind a credential guard no agent in this repository can read
(`reports/backlog/live-run-verification-blocked-by-credential-guard.md`).
Every property the plan's "Done when" names is a property of the *executor*,
not of the pipeline, so each is tested here against injected work.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from sqlalchemy import select

from techinves.db.models import RunRow
from techinves.runs.reconcile import RESTART_ERROR
from techinves.runs.service import (
    NOT_RECONCILED_MESSAGE,
    RunNotReconciled,
    RunRefused,
    RunService,
    UnknownTriggerType,
)


async def _row(session_maker, run_id: str) -> RunRow:
    async with session_maker() as session:
        return (
            await session.execute(select(RunRow).where(RunRow.run_id == run_id))
        ).scalar_one()


async def _rows(session_maker) -> list[RunRow]:
    async with session_maker() as session:
        return list(
            (await session.execute(select(RunRow).order_by(RunRow.id))).scalars().all()
        )


async def _await_event(event: threading.Event, *, timeout: float = 10.0) -> None:
    """Wait for a worker thread's event without blocking the event loop."""
    for _ in range(int(timeout / 0.01)):
        if event.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("event was never set")


def _counter():
    ids = iter(f"run-{i}" for i in range(1, 100))
    return lambda: next(ids)


async def _started_service(session_maker, work_registry) -> RunService:
    service = RunService(
        session_maker=session_maker,
        work_registry=work_registry,
        run_id_factory=_counter(),
    )
    await service.startup()
    return service


# --- Done when #1 -----------------------------------------------------------


async def test_a_run_survives_the_triggering_call_returning(session_maker):
    """Plan §4 "Done when" (1): a run survives the triggering request
    completing.

    The work is held on a barrier the test controls. `trigger()` returning
    while the work is demonstrably still mid-flight is exactly the property:
    nothing about the run's progress is tied to the caller's lifetime, which
    is what lets a four-minute pipeline sit behind a request that returns in
    milliseconds (ADR 0010 §3).
    """
    entered = threading.Event()
    release = threading.Event()

    def slow_work(ctx):
        entered.set()
        assert release.wait(timeout=10)

    service = await _started_service(session_maker, {"report": slow_work})

    run_id = await service.trigger("report")

    # The trigger has returned -- the "request" is over -- and the work has
    # not. (Awaited rather than blocked on: a synchronous wait here would
    # occupy the event loop thread and the executor task would never get to
    # start, which says nothing about the executor.)
    await _await_event(entered)
    assert not release.is_set()
    row = await _row(session_maker, run_id)
    assert row.status == "running"
    assert row.started_at is not None
    assert row.finished_at is None

    release.set()
    await service.wait_for(run_id)

    row = await _row(session_maker, run_id)
    assert row.status == "succeeded"
    assert row.finished_at is not None
    assert row.error is None


# --- Done when #2 -----------------------------------------------------------


async def test_two_concurrent_triggers_produce_one_run_and_one_refusal(session_maker):
    """Plan §4 "Done when" (2), and ADR 0010 §4.

    Genuinely concurrent: both `trigger()` coroutines are in flight at once
    under `asyncio.gather`, so they interleave at their `await` points and
    the database -- not a Python-level check -- decides which one wins. A
    sequential pair of calls would pass even against a check-then-insert
    implementation, which is the racy one this design rejects.
    """
    release = threading.Event()

    def slow_work(ctx):
        assert release.wait(timeout=10)

    service = await _started_service(session_maker, {"report": slow_work})

    results = await asyncio.gather(
        service.trigger("report"), service.trigger("report"), return_exceptions=True
    )

    accepted = [r for r in results if isinstance(r, str)]
    refused = [r for r in results if isinstance(r, RunRefused)]
    assert len(accepted) == 1, results
    assert len(refused) == 1, results

    # The refusal names the run that holds the lock, so the caller can look
    # at it rather than being told only "busy".
    assert refused[0].active_run_id == accepted[0]
    assert refused[0].trigger_type == "report"
    assert accepted[0] in str(refused[0])

    # One run exists, not two: the loser left no row behind.
    assert [r.run_id for r in await _rows(session_maker)] == [accepted[0]]

    release.set()
    await service.wait_for_all()


async def test_the_lock_is_per_trigger_type(session_maker):
    """ADR 0010 §4 locks per *action type*: a report run in flight must not
    block the cheap score refresh, since bundling their costs is exactly what
    the three separate triggers exist to avoid."""
    release = threading.Event()

    def slow_work(ctx):
        assert release.wait(timeout=10)

    service = await _started_service(
        session_maker, {"report": slow_work, "scores": slow_work, "company": slow_work}
    )

    report_id = await service.trigger("report")
    scores_id = await service.trigger("scores")
    company_id = await service.trigger("company", ticker="NVDA")

    assert len({report_id, scores_id, company_id}) == 3
    assert (await _row(session_maker, company_id)).ticker == "NVDA"

    release.set()
    await service.wait_for_all()


async def test_the_lock_is_released_when_a_run_lands_terminal(session_maker):
    service = await _started_service(session_maker, {"report": lambda ctx: None})

    first = await service.trigger("report")
    await service.wait_for(first)
    second = await service.trigger("report")
    await service.wait_for(second)

    assert first != second
    assert {r.status for r in await _rows(session_maker)} == {"succeeded"}


# --- Done when #3 -----------------------------------------------------------


async def test_a_killed_process_leaves_no_permanently_running_row(session_maker):
    """Plan §4 "Done when" (3), and Faz 3.2.

    A process that dies mid-run cannot write its own epitaph, so the row is
    left exactly as it stood -- which is what this test writes directly.
    Startup reconciliation is then the only thing that can clear it, and if
    it does not, the partial index keeps that row's lock forever and the
    application is permanently unable to run a report.
    """
    from techinves.api._time import now_naive_utc

    now = now_naive_utc()
    async with session_maker() as session:
        session.add(
            RunRow(
                run_id="crashed",
                trigger_type="report",
                status="running",
                created_at=now,
                started_at=now,
            )
        )
        session.add(
            RunRow(
                run_id="never-started",
                trigger_type="scores",
                status="queued",
                created_at=now,
            )
        )
        await session.commit()

    service = await _started_service(session_maker, {"report": lambda ctx: None})

    for run_id in ("crashed", "never-started"):
        row = await _row(session_maker, run_id)
        assert row.status == "failed"
        assert row.finished_at is not None
        # Distinguishable from a genuine failure, which records
        # "<ExceptionType>: <message>".
        assert row.error == RESTART_ERROR
        assert "restarted" in row.error

    # And the lock it was holding is free: the same trigger type runs again.
    new_id = await service.trigger("report")
    await service.wait_for(new_id)
    assert (await _row(session_maker, new_id)).status == "succeeded"


async def test_trigger_refuses_before_reconciliation_has_run(session_maker):
    """Faz 3.2's ordering requirement, enforced rather than assumed: the lock
    cannot be consulted before reconciliation, because `trigger()` refuses
    outright until reconciliation has completed in this process. If the lock
    could be consulted first, a stale row would refuse the trigger and
    reconciliation would arrive too late to matter."""
    service = RunService(session_maker=session_maker, work_registry={"report": lambda ctx: None})

    assert service.reconciled is False
    with pytest.raises(RunNotReconciled) as excinfo:
        await service.trigger("report")
    assert str(excinfo.value) == NOT_RECONCILED_MESSAGE

    # Nothing was written -- in particular no row that would itself hold the
    # lock.
    assert await _rows(session_maker) == []

    await service.startup()
    assert service.reconciled is True
    assert await service.trigger("report")


# --- the case the plan does not name, but the design implies ----------------


async def test_work_that_raises_lands_failed_and_releases_the_lock(session_maker):
    """A crash *inside* the task must never leave the row non-terminal.

    A non-terminal row holds the in-flight lock, and nothing clears it until
    the next process restart -- so an exception that skipped the terminal
    write would turn one bad run into a dead button. The error is recorded on
    the row, and the next trigger of the same type is accepted.
    """

    explode = True

    def maybe_exploding_work(ctx):
        ctx.log("about to fail")
        if explode:
            raise RuntimeError("provider returned 500")

    service = await _started_service(session_maker, {"report": maybe_exploding_work})

    run_id = await service.trigger("report")
    await service.wait_for(run_id)

    row = await _row(session_maker, run_id)
    assert row.status == "failed"
    assert row.finished_at is not None
    assert row.error == "RuntimeError: provider returned 500"
    assert row.error != RESTART_ERROR  # not a restart; a genuine failure
    assert "about to fail" in row.log
    assert "run failed: RuntimeError: provider returned 500" in row.log

    # The lock is free again.
    explode = False
    retry = await service.trigger("report")
    await service.wait_for(retry)
    assert (await _row(session_maker, retry)).status == "succeeded"


# --- cancellation -----------------------------------------------------------
#
# The one thing `asyncio.to_thread` cannot do is stop the thread. Everything
# below is about not pretending otherwise: the lock is held by the row, so
# the row must not land terminal while the thread is still issuing provider
# requests.


async def test_cancelling_a_run_does_not_release_the_lock_while_the_work_runs(
    session_maker,
):
    """The blocker this section exists for.

    Cancelling the executor task raises `CancelledError` in the coroutine and
    does nothing at all to the worker thread. Landing the row terminal there
    would drop it out of `uq_runs_active_trigger` and release the lock, so
    the next press of the same button starts a *second* full run against the
    same provider keys while the first is still mid-flight -- against EDGAR,
    the IP-ban case, not a recoverable per-ticker failure.

    So while the thread is demonstrably still running, the row stays
    `running` and the next trigger of that type is refused.
    """
    entered = threading.Event()
    release = threading.Event()

    def slow_work(ctx):
        entered.set()
        assert release.wait(timeout=10)

    service = await _started_service(session_maker, {"report": slow_work})
    run_id = await service.trigger("report")
    await _await_event(entered)

    # Cancel, with a grace period short enough that the test does not wait a
    # real minute for a thread that is deliberately still blocked.
    service._cancel_grace_seconds = 0.05
    task = service._tasks[run_id]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    row = await _row(session_maker, run_id)
    assert row.status == "running"  # NOT terminal
    assert row.finished_at is None

    # ...and therefore the lock is still held: a second identical run cannot
    # start alongside the thread that is still executing.
    with pytest.raises(RunRefused) as excinfo:
        await service.trigger("report")
    assert excinfo.value.active_run_id == run_id

    release.set()


async def test_cancellation_asks_the_work_to_stop_and_lands_it_once_it_does(
    session_maker,
):
    """Cooperative cancellation is the only kind a thread has. Work that polls
    `ctx.is_cancelled()` stops at its next checkpoint; the coroutine sees the
    thread actually exit, and only *then* lands the run terminal and releases
    the lock -- correctly, because nothing is executing any more.
    """
    entered = threading.Event()
    saw_cancel = threading.Event()

    def polling_work(ctx):
        entered.set()
        for _ in range(1000):
            if ctx.is_cancelled():
                saw_cancel.set()
                return
            time.sleep(0.01)
        raise AssertionError("cancellation was never signalled to the work")

    service = await _started_service(session_maker, {"report": polling_work})
    run_id = await service.trigger("report")
    await _await_event(entered)

    task = service._tasks[run_id]
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await _await_event(saw_cancel)
    await service._drain_log_tasks(timeout=10.0)

    row = await _row(session_maker, run_id)
    assert row.status == "succeeded"  # the work returned normally when asked to stop
    assert row.finished_at is not None
    assert "cancellation requested" in row.log

    # The lock is free again, because the thread really is gone.
    retry = await service.trigger("report")
    assert retry != run_id


async def test_the_default_run_context_is_never_cancelled(session_maker):
    """`is_cancelled` defaults to "no", so work that never polls it (and a
    hand-built context in a test or a driver) needs no extra argument and
    behaves exactly as before."""
    seen: list[bool] = []

    service = await _started_service(
        session_maker, {"report": lambda ctx: seen.append(ctx.is_cancelled())}
    )
    run_id = await service.trigger("report")
    await service.wait_for(run_id)

    assert seen == [False]
    assert (await _row(session_maker, run_id)).status == "succeeded"


async def test_a_terminal_write_survives_a_failing_log_append(session_maker):
    """The module docstring's ordering claim, enforced.

    `_land` writes `_finish` before it appends the failure line. Both do
    database I/O; if the append went first and raised, `_finish` would never
    run and the row would sit non-terminal, holding the lock until the next
    process restart. The log line is observability, the terminal status is
    the lock -- so the lock wins.
    """
    service = await _started_service(
        session_maker, {"report": _raise_runtime_error}
    )

    async def broken_append(run_id, line):
        raise RuntimeError("log column unavailable")

    service.append_log = broken_append  # type: ignore[method-assign]

    run_id = await service.trigger("report")
    await service.wait_for(run_id)

    row = await _row(session_maker, run_id)
    assert row.status == "failed"
    assert row.finished_at is not None
    assert row.error == "RuntimeError: provider returned 500"


def _raise_runtime_error(ctx):
    raise RuntimeError("provider returned 500")


# --- the log stream ---------------------------------------------------------


async def test_log_lines_are_appended_not_overwritten(session_maker):
    def chatty_work(ctx):
        for i in range(20):
            ctx.log(f"step {i}")

    service = await _started_service(session_maker, {"report": chatty_work})
    run_id = await service.trigger("report")
    await service.wait_for(run_id)

    log = (await _row(session_maker, run_id)).log
    for i in range(20):
        assert f"step {i}" in log
    assert log.index("step 0") < log.index("step 19")


async def test_concurrent_log_appends_lose_no_lines(session_maker):
    """The append is `UPDATE runs SET log = log || :chunk`, evaluated inside
    the database, so no writer ever holds a snapshot of the whole column and
    there is no read-modify-write window for a line to disappear into."""
    service = await _started_service(session_maker, {"report": lambda ctx: None})
    run_id = await service.trigger("report")
    await service.wait_for(run_id)

    await asyncio.gather(*(service.append_log(run_id, f"line-{i}") for i in range(50)))

    log = (await _row(session_maker, run_id)).log
    assert sorted(line.split()[-1] for line in log.splitlines()) == sorted(
        f"line-{i}" for i in range(50)
    )


# --- misc contract ----------------------------------------------------------


async def test_unknown_trigger_type_is_rejected_before_any_write(session_maker):
    service = await _started_service(session_maker, {"report": lambda ctx: None})
    with pytest.raises(UnknownTriggerType):
        await service.trigger("everything")
    assert await _rows(session_maker) == []


async def test_the_run_context_carries_the_trigger_identity(session_maker):
    seen = {}

    def capture(ctx):
        seen["run_id"] = ctx.run_id
        seen["trigger_type"] = ctx.trigger_type
        seen["ticker"] = ctx.ticker

    service = await _started_service(session_maker, {"company": capture})
    run_id = await service.trigger("company", ticker="MSFT")
    await service.wait_for(run_id)

    assert seen == {"run_id": run_id, "trigger_type": "company", "ticker": "MSFT"}


async def test_work_runs_off_the_event_loop_thread(session_maker):
    """The three real units of work are blocking and drive their own
    `asyncio.run(...)`, which raises inside a running loop -- so the executor
    has to hand them a thread, not the loop."""
    loop_thread = threading.get_ident()
    work_thread = {}

    def capture(ctx):
        work_thread["id"] = threading.get_ident()

    service = await _started_service(session_maker, {"report": capture})
    run_id = await service.trigger("report")
    await service.wait_for(run_id)

    assert work_thread["id"] != loop_thread
