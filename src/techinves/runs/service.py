"""The run service: queue a run, execute it out of band, land it terminal.

Faz 3.1/3.3 of `reports/plans/2026-08-18_on-demand-transformation.md`, under
ADR 0010 §3 and §4.

## Mechanism

An in-process `asyncio` task whose state lives in the database (plan §9 Q2,
decided). No Celery, no RQ, no Redis, no second service to run -- ADR 0010 §7
measures setup friction in people who never get the project running, and a
broker is a service to install before the first button press. The explicit
price of that choice is `techinves.runs.reconcile`.

## Lifecycle of one run

    trigger()            INSERT status='queued'      <- lock acquired here
      |                  (IntegrityError => refusal)
      | returns run_id immediately; the caller's request can now complete
      v
    _execute()           status='running', started_at
      |                  work runs in a worker thread; log lines append
      v
    terminal             status='succeeded' | 'failed', finished_at, error
                                                     <- lock released here

Every arrow after the first happens outside the request/response cycle, so a
four-minute pipeline does not need a four-minute HTTP request (ADR 0010 §3),
and closing the page cancels nothing.

**The terminal landing is written before anything best-effort.** `_land`
writes `_finish` first and only then appends the failure line to the log:
both do database I/O, and if the log append went first and raised, `_finish`
would never run and the row would stay non-terminal. A crash inside the work
must never leave the row non-terminal, because a non-terminal row holds the
lock (see `reconcile`).

## Why the work runs in a thread

The three units of work are synchronous, blocking, and drive their own
`asyncio.run(...)` internally (`pipeline.storage.report_store`). Calling
`asyncio.run()` from inside a running event loop raises, and a multi-minute
blocking call on the loop would freeze every request anyway. So work runs via
`asyncio.to_thread`, and the log callback it is handed marshals back onto the
service's loop with `run_coroutine_threadsafe`.

## Cancellation, and why it cannot simply land the run terminal

`asyncio.to_thread` starts a thread and hands back a future. Cancelling the
future -- which is what a loop shutdown, a `--reload` restart or a
`wait_for` timeout does -- raises `CancelledError` in *this* coroutine and
**does nothing whatsoever to the thread**. Python has no way to stop a
thread from outside it; the pipeline keeps running, keeps issuing provider
requests, and keeps writing.

So cancellation must never take the shortcut of writing a terminal status:
terminal drops the row out of `uq_runs_active_trigger` and releases the
lock, and the next press of the same button would start a second full
research chain against the same provider keys while the first one is still
mid-flight. Against EDGAR that is the IP-ban case README.md calls "not a
per-ticker recoverable failure".

What happens instead:

1. A `threading.Event` is raised. The work sees it through
   `RunContext.is_cancelled()` and is expected to stop at its next
   checkpoint -- cooperative, because that is the only kind of cancellation
   a thread has.
2. This coroutine keeps waiting for the thread, for up to
   `CANCEL_GRACE_SECONDS`, with the row still `running` and therefore still
   holding the lock.
3. If the thread exits within the grace period, the run lands terminal on
   whatever the thread actually ended with (it may have raised, and it may
   even have finished the work) and the lock is released -- correctly, since
   nothing is executing any more.
4. If it does not, the row is **left exactly as it stands**: `running`,
   lock held, nothing written. `reconcile_stale_runs` at the next startup is
   then the thing that clears it, which is precisely the case that module
   exists for. Holding the lock for a run whose thread is unaccounted for is
   the safe direction to fail in; releasing it is not.

## The lock, and its race

`runs` carries a unique partial index over `trigger_type` restricted to
`('queued','running')`. `trigger()` does **not** check-then-insert -- that
races: two coroutines both read "no active run", both insert, and the second
one's failure would surface as an unhandled 500 rather than a refusal. It
inserts unconditionally and catches the `IntegrityError` the index raises,
converting exactly that violation into `RunRefused`, which names the run
holding the lock. The database arbitrates; this code only translates.

`IntegrityError` also covers `uq_runs_run_id`, so the handler re-reads the
active row and only refuses when one is actually there -- otherwise the
original error is re-raised rather than being mislabelled as a collision.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError

from techinves.api._time import now_naive_utc
from techinves.db.models import RunRow
from techinves.db.session import get_sessionmaker
from techinves.runs.reconcile import NON_TERMINAL_STATUSES

logger = logging.getLogger(__name__)

#: ADR 0010 §1's three actions, and the only legal values of
#: `runs.trigger_type`. The lock is per trigger type, so this list is also
#: the maximum number of runs that can be in flight at once.
TRIGGER_TYPES = ("scores", "report", "company")

#: Faz 4 (`reports/backlog/run-log-grows-without-bound.md`): a cap on
#: `runs.log`, so a pathologically chatty run (a retry storm inside a
#: research branch, say) does not grow the column without bound. 200,000
#: characters is generous headroom over a normal run -- Faz 3's report calls
#: one full pipeline run's log "small" -- while still bounding the worst
#: case to a size that is cheap to send over HTTP on every poll.
RUN_LOG_MAX_CHARS = 200_000

#: A fixed, distinctive marker embedded in the one truncation notice a
#: capped run's log ever gets. `append_log` checks for this substring
#: (rather than a separate "truncated" column) to decide whether the cap has
#: already been reached -- see that method's docstring for why a marker
#: check is enough and a schema change is not needed.
RUN_LOG_TRUNCATION_MARKER = "[RUN_LOG_TRUNCATED]"

RUN_LOG_TRUNCATION_NOTICE = (
    f"{RUN_LOG_TRUNCATION_MARKER} log capped at {RUN_LOG_MAX_CHARS:,} characters; "
    "earlier output above is preserved and no further lines will be appended "
    "for this run.\n"
)

#: How long a cancelled run's coroutine keeps waiting for its worker thread
#: to confirm it has exited before giving up and leaving the row non-terminal
#: for startup reconciliation. Generous rather than tight: every second spent
#: here is a second the lock is *correctly* held, whereas giving up early
#: only trades that for a row `reconcile` has to clear later.
CANCEL_GRACE_SECONDS = 60.0

#: Appended to the log when cancellation is requested, so a run that ends up
#: reconciled at the next startup still says why it stopped.
CANCEL_REQUESTED_NOTICE = (
    "cancellation requested; waiting for the work to stop. The in-flight "
    "lock stays held until it does -- a worker thread cannot be stopped from "
    "outside, and releasing the lock while it still runs would allow a "
    "second run against the same provider keys."
)

NOT_RECONCILED_MESSAGE = (
    "Startup reconciliation has not run in this process, so the in-flight "
    "lock's state is unknown. reconcile_stale_runs() must complete before "
    "any run is triggered -- see techinves.runs.reconcile."
)


class RunRefused(RuntimeError):
    """A run of this trigger type is already in flight (ADR 0010 §4).

    Refused, never queued: provider quotas are the binding constraint, and an
    unbounded queue behind a button is the most direct route to burning a
    free-tier key.
    """

    def __init__(self, trigger_type: str, active_run_id: str) -> None:
        self.trigger_type = trigger_type
        self.active_run_id = active_run_id
        super().__init__(
            f"a '{trigger_type}' run is already in flight (run_id={active_run_id}); "
            "wait for it to finish rather than starting a second one"
        )


class RunNotReconciled(RuntimeError):
    """`trigger()` was called before startup reconciliation. See
    `NOT_RECONCILED_MESSAGE`."""


class UnknownTriggerType(ValueError):
    pass


def _never_cancelled() -> bool:
    return False


@dataclass
class RunContext:
    """What a unit of work is handed. `log()` is safe to call from the worker
    thread the work runs in, and is the only channel the work has to the UI
    while it is in flight."""

    run_id: str
    trigger_type: str
    ticker: str | None
    log: Callable[[str], None]
    #: Cooperative cancellation. Polling this between units of work (a
    #: ticker, a research branch) and returning or raising when it goes true
    #: is the *only* way a run can actually be stopped: the work runs in a
    #: thread, and no caller can stop a thread from outside it. Work that
    #: never polls it is not wrong -- it just cannot be cancelled, and the
    #: executor will wait `CANCEL_GRACE_SECONDS` for it either way. Defaults
    #: to "never cancelled" so a hand-built context (tests, a driver) needs
    #: no extra argument.
    is_cancelled: Callable[[], bool] = _never_cancelled


#: A unit of work: synchronous, blocking, executed in a worker thread.
#: Returning normally is success; raising is failure. Nothing else is
#: consulted -- in particular a verifier `block` verdict is a *completed*
#: run, not a failed one (see `pipeline.storage.report_store`).
WorkFn = Callable[[RunContext], Any]


def _default_work_registry() -> dict[str, WorkFn]:
    # Imported lazily: `work` pulls in the pipeline and the scoring engine,
    # and neither the reconciliation path nor a test that injects fakes
    # should pay for that import.
    from techinves.runs import work

    return {
        "scores": work.run_scores,
        "report": work.run_report,
        "company": work.run_company,
    }


class RunService:
    """Owns the lifecycle of every run in this process.

    `session_maker` and `work_registry` are injectable so tests can drive the
    whole lifecycle against an in-memory database with the real work mocked
    -- which is the only way this layer is testable at all, since the live
    path needs API keys.
    """

    def __init__(
        self,
        *,
        session_maker=None,
        work_registry: dict[str, WorkFn] | None = None,
        run_id_factory: Callable[[], str] | None = None,
        cancel_grace_seconds: float = CANCEL_GRACE_SECONDS,
    ) -> None:
        self._session_maker = session_maker
        self._work_registry = work_registry
        self._run_id_factory = run_id_factory
        self._cancel_grace_seconds = cancel_grace_seconds
        self._tasks: dict[str, asyncio.Task] = {}
        # The cooperative cancellation flag of each in-flight run, keyed by
        # run id. Held here (rather than only inside `_execute`) so the flag
        # is reachable for as long as the run is.
        self._cancel_flags: dict[str, threading.Event] = {}
        # Strong references to in-flight log appends scheduled from the loop
        # thread; without them the loop only holds tasks weakly and a line
        # could be collected before it is written.
        self._log_tasks: set[asyncio.Task] = set()
        self._reconciled = False

    # -- wiring -----------------------------------------------------------

    @property
    def session_maker(self):
        return self._session_maker or get_sessionmaker()

    @property
    def work_registry(self) -> dict[str, WorkFn]:
        if self._work_registry is None:
            self._work_registry = _default_work_registry()
        return self._work_registry

    def _new_run_id(self) -> str:
        if self._run_id_factory is not None:
            return self._run_id_factory()
        from pipeline.run import make_run_id

        return make_run_id()

    # -- startup ----------------------------------------------------------

    async def startup(self) -> list[str]:
        """Reconcile, then open the service for business.

        The two steps are one call on purpose: `_reconciled` is set **only**
        by a completed reconciliation, so there is no ordering for a caller
        to get wrong and no way to reach `trigger()` with an unknown lock
        state. A failing reconciliation propagates (`ReconciliationFailed`),
        leaves `_reconciled` false, and aborts startup.
        """
        from techinves.runs.reconcile import reconcile_stale_runs

        stale = await reconcile_stale_runs(self.session_maker)
        self._reconciled = True
        return stale

    @property
    def reconciled(self) -> bool:
        return self._reconciled

    # -- triggering -------------------------------------------------------

    async def trigger(
        self, trigger_type: str, *, ticker: str | None = None, run_id: str | None = None
    ) -> str:
        """Queue a run and return its id **immediately**, without waiting for
        any of the work. Raises `RunRefused` when the lock is held.

        Ticker validation against the watchlist is deliberately not here: it
        is Faz 4.3, on the endpoint, where the caller is untrusted input.
        """
        if not self._reconciled:
            raise RunNotReconciled(NOT_RECONCILED_MESSAGE)
        if trigger_type not in TRIGGER_TYPES:
            raise UnknownTriggerType(
                f"unknown trigger type {trigger_type!r}; expected one of {TRIGGER_TYPES}"
            )

        run_id = run_id or self._new_run_id()
        now = now_naive_utc()

        async with self.session_maker() as session:
            session.add(
                RunRow(
                    run_id=run_id,
                    trigger_type=trigger_type,
                    ticker=ticker,
                    status="queued",
                    created_at=now,
                    log="",
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                holder = await self._active_run_id(trigger_type)
                if holder is None:
                    # Not the lock: some other constraint (a duplicated
                    # run_id, say). Mislabelling it as a collision would send
                    # the caller looking for a run that does not exist.
                    raise
                raise RunRefused(trigger_type, holder) from exc

        loop = asyncio.get_running_loop()
        task = loop.create_task(self._execute(run_id), name=f"run:{run_id}")
        # Strong reference: a bare `create_task` result is only weakly held by
        # the loop, so without this the run could be garbage-collected
        # mid-flight and never land terminal.
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._tasks.pop(rid, None))
        return run_id

    async def _active_run_id(self, trigger_type: str) -> str | None:
        async with self.session_maker() as session:
            return (
                await session.execute(
                    select(RunRow.run_id).where(
                        RunRow.trigger_type == trigger_type,
                        RunRow.status.in_(NON_TERMINAL_STATUSES),
                    )
                )
            ).scalars().first()

    # -- execution --------------------------------------------------------

    async def _execute(self, run_id: str) -> None:
        row = await self._load(run_id)
        if row is None:  # pragma: no cover - the row is committed before this runs
            logger.error("run %s vanished before execution", run_id)
            return

        trigger_type, ticker = row
        await self._mark_running(run_id)

        loop = asyncio.get_running_loop()
        cancel = threading.Event()
        self._cancel_flags[run_id] = cancel
        ctx = RunContext(
            run_id=run_id,
            trigger_type=trigger_type,
            ticker=ticker,
            log=_thread_safe_logger(loop, self, run_id),
            is_cancelled=cancel.is_set,
        )

        # A handle on the thread rather than a bare `await asyncio.to_thread(...)`.
        # The handle is the whole point: it is what lets this coroutine keep
        # waiting for the thread *after* it has itself been cancelled, and
        # `asyncio.shield` is what stops a cancellation of this task from
        # propagating into it.
        worker = asyncio.ensure_future(
            asyncio.to_thread(self.work_registry[trigger_type], ctx)
        )
        try:
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                cancel.set()
                if not await self._await_worker_exit(run_id, worker):
                    # The thread is still going. Leave the row exactly as it
                    # stands -- `running`, and so still holding the in-flight
                    # lock -- and let startup reconciliation clear it. Landing
                    # it terminal here is the one thing that must not happen:
                    # it would hand the lock to a second, identical run while
                    # this one is still issuing provider requests from a
                    # thread nothing can stop. See the module docstring.
                    logger.warning(
                        "run %s was cancelled but its worker thread is still running after "
                        "%.0fs; leaving the row non-terminal so it keeps holding the "
                        "in-flight lock until startup reconciliation clears it",
                        run_id,
                        self._cancel_grace_seconds,
                    )
                    raise
                # The thread confirmed it is gone, so nothing is executing and
                # the lock is safe to release. Record whatever it ended with,
                # then stay cancelled.
                await self._land(run_id, worker)
                raise
            except BaseException as exc:
                # The work raised. Deliberately BaseException, not Exception:
                # anything that comes out of the thread must still land
                # terminal, because a non-terminal row holds the in-flight
                # lock and only a process restart clears it afterwards.
                await self._land(run_id, worker)
                if not isinstance(exc, Exception):
                    raise
            else:
                await self._land(run_id, worker)
        finally:
            self._cancel_flags.pop(run_id, None)

    async def _await_worker_exit(self, run_id: str, worker: asyncio.Future) -> bool:
        """Wait up to `CANCEL_GRACE_SECONDS` for a cancelled run's worker
        thread to actually exit. Returns whether it did.

        `worker.done()` is the answer, not whatever the wait raises: the wait
        can end in a timeout, in the work's own exception, or in a second
        cancellation of this task, and none of those says anything about
        whether the thread is still alive. Only the future being resolved
        does.
        """
        try:
            await self.append_log(run_id, CANCEL_REQUESTED_NOTICE)
        except Exception:  # noqa: BLE001 - observability, never the work
            logger.exception("could not record the cancellation of run %s", run_id)
        try:
            await asyncio.wait_for(
                asyncio.shield(worker), timeout=self._cancel_grace_seconds
            )
        except BaseException:  # noqa: BLE001 - see the docstring
            pass
        return worker.done() and not worker.cancelled()

    async def _land(self, run_id: str, worker: asyncio.Future) -> None:
        """Write the terminal status from what the worker thread actually
        ended with.

        `_finish` goes first and the log append second, and the order is
        load-bearing: both do database I/O, and an append that raised before
        `_finish` had run would leave the row non-terminal -- holding the
        in-flight lock until the next process restart. The log line is
        observability; the terminal status is the lock.
        """
        exc = None if worker.cancelled() else worker.exception()
        if exc is None:
            await self._finish(run_id, status="succeeded", error=None)
            return

        error = f"{type(exc).__name__}: {exc}"
        await self._finish(run_id, status="failed", error=error)
        try:
            await self.append_log(run_id, f"run failed: {error}")
        except Exception:  # noqa: BLE001 - the row is already terminal
            logger.exception("could not record the failure of run %s in its log", run_id)
        if isinstance(exc, Exception):
            logger.error("run %s failed: %s", run_id, error, exc_info=exc)

    async def _load(self, run_id: str) -> tuple[str, str | None] | None:
        async with self.session_maker() as session:
            row = (
                await session.execute(
                    select(RunRow.trigger_type, RunRow.ticker).where(RunRow.run_id == run_id)
                )
            ).first()
            return (row[0], row[1]) if row is not None else None

    async def _mark_running(self, run_id: str) -> None:
        async with self.session_maker() as session:
            await session.execute(
                update(RunRow)
                .where(RunRow.run_id == run_id)
                .values(status="running", started_at=now_naive_utc())
            )
            await session.commit()

    async def _finish(self, run_id: str, *, status: str, error: str | None) -> None:
        """Land the terminal status. The executor owns this row's lifecycle,
        so this is authoritative over anything the work itself wrote onto the
        row on its way past (see `pipeline.storage.report_store`)."""
        values: dict[str, Any] = {"status": status, "finished_at": now_naive_utc()}
        if error is not None:
            values["error"] = error[:2000]
        async with self.session_maker() as session:
            await session.execute(update(RunRow).where(RunRow.run_id == run_id).values(**values))
            await session.commit()

    # -- the log stream ---------------------------------------------------

    async def append_log(self, run_id: str, line: str) -> None:
        """Append one line to `runs.log`, respecting `RUN_LOG_MAX_CHARS`.

        Three properties, all required by Faz 4 (plan §5, "the log-tail
        protocol and the cap are the same decision") and all delivered by the
        same single statement:

        * **No line is lost under concurrent writes.** The append is
          `UPDATE runs SET log = log || :chunk`, evaluated inside the
          database. It never reads the column into Python, so there is no
          read-modify-write window in which a second writer's line can be
          overwritten by a stale copy. Two concurrent appends serialize into
          two appends; last-writer-wins is impossible because no writer ever
          carries a snapshot of the whole column.
        * **The whole column is never rewritten**, capped or not. Only the
          new chunk (or, once, the truncation notice) crosses the wire,
          regardless of how long the log already is.
        * **The cap never drops or rewrites existing content.** Once
          `length(log) + length(chunk)` would exceed `RUN_LOG_MAX_CHARS`, the
          chunk is dropped and replaced by exactly one truncation notice
          (never zero, never more than one); every append after that is a
          no-op. Truncating the *head* to make room -- the other reading of
          "cap the log" -- would silently rewrite content a client may
          already hold an offset into, which is exactly what the monotonic
          offset protocol (`GET /v1/runs/{id}?log_offset=N`,
          `techinves.api.routers.runs`) exists to rule out. "Exactly once" is
          tracked without a second column: the notice carries a fixed,
          distinctive marker (`RUN_LOG_TRUNCATION_MARKER`), and this method
          checks for that substring rather than reading the whole column, so
          the check costs nothing extra a `LIKE` scan of the row wouldn't
          already cost.

        `||`, `length()` and `LIKE` are standard SQL and mean the same thing
        on both supported backends (SQLite, PostgreSQL).
        """
        chunk = f"{_stamp()} {line.rstrip()}\n"
        stmt = text(
            """
            UPDATE runs
            SET log = CASE
                WHEN log LIKE '%' || :marker || '%' THEN log
                WHEN length(log) + length(:chunk) > :cap THEN log || :notice
                ELSE log || :chunk
            END
            WHERE run_id = :run_id
            """
        )
        async with self.session_maker() as session:
            await session.execute(
                stmt,
                {
                    "marker": RUN_LOG_TRUNCATION_MARKER,
                    "chunk": chunk,
                    "cap": RUN_LOG_MAX_CHARS,
                    "notice": RUN_LOG_TRUNCATION_NOTICE,
                    "run_id": run_id,
                },
            )
            await session.commit()

    # -- test/driver helpers ----------------------------------------------

    async def wait_for(self, run_id: str, *, timeout: float = 30.0) -> None:
        """Await a triggered run's completion.

        A driver for tests and for a CLI that wants to block; nothing in the
        request path calls it, since not blocking is the entire point of this
        module.
        """
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        await self._drain_log_tasks(timeout=timeout)

    async def wait_for_all(self, *, timeout: float = 30.0) -> None:
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True), timeout=timeout
            )
        await self._drain_log_tasks(timeout=timeout)

    async def _drain_log_tasks(self, *, timeout: float) -> None:
        pending = list(self._log_tasks)
        if pending:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True), timeout=timeout
            )


def _stamp() -> str:
    return datetime.isoformat(now_naive_utc(), timespec="seconds")


def _thread_safe_logger(loop: asyncio.AbstractEventLoop, service: RunService, run_id: str):
    """A sync `log(line)` the work can call from its worker thread.

    Marshals onto the service's loop rather than opening a second connection
    from the thread, so appends for one run stay ordered and the worker
    thread never touches the engine directly. A failed append must not kill
    the run -- progress output is observability, not the work.
    """
    main_thread_id = threading.get_ident()

    def log(line: str) -> None:
        coro = service.append_log(run_id, line)
        if threading.get_ident() == main_thread_id and loop.is_running():
            # Called from the loop's own thread (a test's fake work, say):
            # scheduling and blocking on the result would deadlock.
            task = loop.create_task(coro)
            service._log_tasks.add(task)
            task.add_done_callback(service._log_tasks.discard)
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=30)
        except Exception:  # noqa: BLE001
            logger.exception("could not append a log line for run %s", run_id)

    return log


_service: RunService | None = None


def get_run_service() -> RunService:
    """The process-wide service instance. One per process, matching the
    single-process assumption `reconcile` documents."""
    global _service
    if _service is None:
        _service = RunService()
    return _service
