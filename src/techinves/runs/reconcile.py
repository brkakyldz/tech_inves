"""Startup reconciliation of runs left non-terminal by a dead process.

Faz 3.2 of `reports/plans/2026-08-18_on-demand-transformation.md`, under
ADR 0010 §4.

## The failure mode this exists to prevent

The in-flight lock is a unique partial index on
`runs(trigger_type) WHERE status IN ('queued','running')`
(`techinves.db.models.RunRow.__table_args__`). It is held by a *row*, not by
a process. A process that dies mid-run -- Ctrl-C, an OOM kill, a `--reload`
restart landing mid-pipeline -- leaves its row saying `running` forever, and
that row keeps holding the lock. Without reconciliation, **one crash locks
the application permanently**: every subsequent press of that button is
refused, pointing at a run that has not existed since the crash. That is the
central failure mode of a DB-held lock, not an edge case, and it is the
reason this module is unconditional.

## Why it needs no liveness check

**This is a single-process application.** At startup, by construction, no
executor exists: `RunService` holds its tasks in memory
(`RunService._tasks`), that memory died with the previous process, and
nothing else in this repository executes a run. Therefore *every*
non-terminal row found at startup is provably stale. There is no
"is the executor still alive?" question to ask, because there is nothing
alive to ask about -- a heuristic here would be inventing doubt where the
process model supplies certainty. Every `queued` or `running` row is marked
`failed` with `RESTART_ERROR`.

`RESTART_ERROR` is a fixed sentence, distinct from anything the work itself
can produce (a genuine failure records `"<ExceptionType>: <message>"`), so
"the process restarted under this run" is always distinguishable from "this
run's work raised".

## The single-process assumption is load-bearing

If this application were ever run under **multiple workers** -- `uvicorn
--workers 2`, `gunicorn -w 4`, `WEB_CONCURRENCY=2` -- each worker would boot,
find the *other workers'* genuinely-live runs non-terminal, and reconcile
them to `failed`, releasing locks out from under running work. The
reconciliation would go from a repair to a corruption.

`refuse_if_multi_worker()` detects the cheap, observable cases and refuses to
start. It reads process configuration only (argv and environment), so it is
best-effort: a supervisor that spawns N single-worker processes from a
process manager is invisible to it. That residual case is documented rather
than defended, because defending it means a real distributed lock, which
ADR 0010 §7's setup-friction criterion rules out for a single-user
self-hosted tool.
"""

from __future__ import annotations

import logging
import os
import sys

from sqlalchemy import select, update

from techinves.api._time import now_naive_utc
from techinves.db.models import RunRow
from techinves.db.session import get_sessionmaker

logger = logging.getLogger(__name__)

#: The statuses a run can be in while it is (supposedly) in flight. These are
#: exactly the statuses the partial index's predicate covers -- the two lists
#: are the same fact and must not drift apart.
NON_TERMINAL_STATUSES = ("queued", "running")

#: Recorded in `runs.error` for every row this module reconciles. A fixed
#: sentence with no interpolation, so a caller can test for it exactly, and
#: worded so a human reading the run history knows the run did not fail on
#: its own merits.
RESTART_ERROR = (
    "The application process restarted while this run was in flight, so the "
    "run was abandoned. It did not fail on its own; nothing was executing it "
    "when the process came back up. Trigger it again to retry."
)


class ReconciliationFailed(RuntimeError):
    """Startup reconciliation could not complete.

    Raised so the application fails loudly at startup rather than beginning
    to serve with an unknown lock state -- an app that cannot tell whether
    the lock is held by live work or by a corpse must not hand out the next
    lock.
    """


class MultiWorkerRefused(RuntimeError):
    """The process looks like one of several workers. See the module
    docstring: reconciliation is only correct under a single process."""


def _worker_count_from_argv(argv: list[str]) -> int | None:
    """Parse `--workers N` / `--workers=N` / `-w N` out of a command line.

    uvicorn's and gunicorn's multiprocess supervisors pass their argv down to
    every child, so this is readable from inside a worker too.
    """
    for i, arg in enumerate(argv):
        name, _, inline = arg.partition("=")
        if name not in ("--workers", "-w"):
            continue
        raw = inline if inline else (argv[i + 1] if i + 1 < len(argv) else "")
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def detect_worker_count(*, argv: list[str] | None = None, env: dict | None = None) -> int | None:
    """Best-effort worker count, or `None` when nothing says.

    Two cheap, observable sources; see the module docstring for what they
    cannot see.
    """
    argv = sys.argv if argv is None else argv
    env = os.environ if env is None else env

    from_argv = _worker_count_from_argv(list(argv))
    if from_argv is not None:
        return from_argv

    raw = env.get("WEB_CONCURRENCY")
    if raw:
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def refuse_if_multi_worker(*, argv: list[str] | None = None, env: dict | None = None) -> None:
    """Raise `MultiWorkerRefused` when the process is configured as one of
    several workers. Called from reconciliation, before anything is written."""
    count = detect_worker_count(argv=argv, env=env)
    if count is not None and count > 1:
        raise MultiWorkerRefused(
            f"TechInves is configured with {count} workers, and it only runs "
            "correctly as a single process: startup reconciliation marks every "
            "non-terminal run failed, so a second worker would abandon the "
            "first worker's live runs, and the in-flight lock assumes one "
            "executor. Start it with a single worker "
            "(drop --workers/-w and WEB_CONCURRENCY)."
        )


async def reconcile_stale_runs(session_maker=None) -> list[str]:
    """Mark every non-terminal run `failed`, and return their run ids.

    Called exactly once per process, during startup, **before the app accepts
    any request and before the in-flight lock is ever consulted**
    (`techinves.api.main.lifespan`, and enforced by
    `RunService.trigger()`'s reconciliation gate). Order matters in one
    direction only: if the lock were consulted first, a stale row would refuse
    the trigger and reconciliation would arrive too late to matter.

    Any failure is re-raised as `ReconciliationFailed`, which aborts startup.
    """
    session_maker = session_maker or get_sessionmaker()
    refuse_if_multi_worker()

    try:
        async with session_maker() as session:
            stale = (
                await session.execute(
                    select(RunRow.run_id).where(RunRow.status.in_(NON_TERMINAL_STATUSES))
                )
            ).scalars().all()

            if stale:
                await session.execute(
                    update(RunRow)
                    .where(RunRow.status.in_(NON_TERMINAL_STATUSES))
                    .values(
                        status="failed",
                        finished_at=now_naive_utc(),
                        error=RESTART_ERROR,
                    )
                )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 - deliberately widened, see docstring
        raise ReconciliationFailed(
            "Could not reconcile in-flight runs at startup, so the in-flight "
            f"lock's state is unknown and the application must not serve: {exc}"
        ) from exc

    if stale:
        logger.warning(
            "reconciled %d run(s) left non-terminal by a previous process: %s",
            len(stale),
            ", ".join(stale),
        )
    return list(stale)
