"""Background execution of the three triggers (ADR 0010 §1, §3, §4).

Faz 3 of `reports/plans/2026-08-18_on-demand-transformation.md`. Three
modules, in the order they matter:

* `reconcile` -- startup reconciliation. Must run before anything else here.
* `service`   -- `RunService`: the queued row, the in-flight lock, the
                 out-of-band execution, the log stream, the terminal status.
* `work`      -- the three units of work the service wraps.

**This layer deliberately contains no HTTP.** `POST /v1/runs` and
`GET /v1/runs*` are Faz 4; this is the service they will call.
"""

from __future__ import annotations

from techinves.runs.reconcile import (
    MultiWorkerRefused,
    ReconciliationFailed,
    RESTART_ERROR,
    reconcile_stale_runs,
    refuse_if_multi_worker,
)
from techinves.runs.service import (
    NOT_RECONCILED_MESSAGE,
    RunContext,
    RunNotReconciled,
    RunRefused,
    RunService,
    TRIGGER_TYPES,
    get_run_service,
)

__all__ = [
    "MultiWorkerRefused",
    "NOT_RECONCILED_MESSAGE",
    "RESTART_ERROR",
    "ReconciliationFailed",
    "RunContext",
    "RunNotReconciled",
    "RunRefused",
    "RunService",
    "TRIGGER_TYPES",
    "get_run_service",
    "reconcile_stale_runs",
    "refuse_if_multi_worker",
]
