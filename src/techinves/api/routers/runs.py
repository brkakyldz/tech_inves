"""`POST /v1/runs`, `GET /v1/runs`, `GET /v1/runs/{id}` -- Faz 4 of
`reports/plans/2026-08-18_on-demand-transformation.md`, under ADR 0010 §1-4.

The first **write** endpoints in `src/techinves/api` (README.md's "Back-end
API" section is updated in the same commit that adds this module). Faz 3
(`src/techinves.runs.service.RunService`) already owns the lock, the
in-flight refusal and the run lifecycle; this module is the HTTP surface
over it -- untrusted input is validated here (ticker, trigger type), not
re-validated inside the service.

## Refusal shape

Every refusal is an `HTTPException` whose `detail` is a dict with a machine
-readable `code`, never prose alone:

| Situation | Status | `code` |
|---|---|---|
| unknown `trigger_type` | 422 | `UnknownTriggerType` |
| `company` trigger with no ticker | 422 | `TickerRequired` |
| a ticker given for a non-`company` trigger | 422 | `UnexpectedTicker` |
| ticker not on the watchlist | 422 | `UnknownTicker` |
| a required API key is absent | 503 | `MissingApiKey` |
| startup reconciliation has not completed | 503 | `RunNotReconciled` |
| the in-flight lock is held | 409 | `RunRefused` |
| unknown run id (`GET /v1/runs/{id}`) | 404 | -- (plain detail string, matching `reports.py`) |
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from pipeline.config import load_watchlist_tickers
from techinves.api.deps import DbSession
from techinves.api.schemas import (
    RunDetailOut,
    RunListResponse,
    RunSummaryOut,
    RunTriggerIn,
    RunTriggerOut,
)
from techinves.db.models import RunRow
from techinves.runs.keys import (
    REQUIRED_KEYS_BY_TRIGGER,
    missing_required_key as _missing_required_key,
)
from techinves.runs.service import (
    RunNotReconciled,
    RunRefused,
    RunService,
    TRIGGER_TYPES,
    UnknownTriggerType,
    get_run_service,
)

router = APIRouter(prefix="/v1/runs", tags=["runs"])

# `REQUIRED_KEYS_BY_TRIGGER` and `_missing_required_key` moved to
# `techinves.runs.keys` in Faz 6, so the CLI and `GET /v1/meta` can share the
# same check without importing this FastAPI router module. Re-exported here
# under their original names -- this module built the check in Faz 4 and
# remains its most visible caller.


def get_run_service_dep() -> RunService:
    """A thin wrapper around the process-wide singleton, so tests can
    override it via `app.dependency_overrides` the same way `DbSession` is
    overridden -- the run service is injectable (`RunService.__init__`
    already supports fakes for exactly this reason), but the module-level
    singleton `get_run_service()` returns is not, without a seam like this
    one."""
    return get_run_service()


def _refused(status_code: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message, **extra})


def _summary_fields(row: RunRow) -> dict:
    return {
        "run_id": row.run_id,
        "trigger_type": row.trigger_type,
        "ticker": row.ticker,
        "status": row.status,
        "created_at": row.created_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "error": row.error,
        "verdict": row.verdict,
        "duration_seconds": row.duration_seconds,
        "findings_count": row.findings_count,
        "failure_count": row.failure_count,
        "total_tokens": row.total_tokens,
        "total_cost_usd": row.total_cost_usd,
    }


@router.post("", response_model=RunTriggerOut, status_code=202)
async def create_run(
    body: RunTriggerIn,
    service: RunService = Depends(get_run_service_dep),
) -> RunTriggerOut:
    """Trigger a run and return its id immediately (202 Accepted -- the work
    itself has not happened yet; `GET /v1/runs/{id}` is where status lands).

    Validation order, cheapest and most-certain first: shape of the request
    (trigger type, ticker) -> key presence -> the service's own reconciliation
    gate and in-flight lock. A caller that fails an earlier check never pays
    for triggering work that a later check would have refused anyway.
    """
    trigger_type = body.trigger_type
    if trigger_type not in TRIGGER_TYPES:
        raise _refused(
            422,
            "UnknownTriggerType",
            f"unknown trigger type {trigger_type!r}; expected one of {TRIGGER_TYPES}",
        )

    ticker = body.ticker
    if trigger_type == "company":
        if not ticker:
            raise _refused(422, "TickerRequired", "the 'company' trigger requires a ticker")
        ticker = ticker.strip().upper()
        watchlist = load_watchlist_tickers()
        if ticker not in watchlist:
            raise _refused(
                422,
                "UnknownTicker",
                f"{ticker!r} is not on the watchlist",
                ticker=ticker,
            )
    elif ticker:
        raise _refused(
            422,
            "UnexpectedTicker",
            f"a ticker was given for the '{trigger_type}' trigger, which does not take one",
        )

    missing_key = _missing_required_key(trigger_type)
    if missing_key is not None:
        raise _refused(
            503,
            "MissingApiKey",
            f"the '{trigger_type}' trigger needs {missing_key}, which is not set",
            missing_key=missing_key,
        )

    try:
        run_id = await service.trigger(trigger_type, ticker=ticker)
    except RunNotReconciled as exc:
        raise _refused(503, "RunNotReconciled", str(exc)) from exc
    except RunRefused as exc:
        raise _refused(
            409,
            "RunRefused",
            str(exc),
            trigger_type=exc.trigger_type,
            active_run_id=exc.active_run_id,
        ) from exc
    except UnknownTriggerType as exc:  # pragma: no cover - already validated above
        raise _refused(422, "UnknownTriggerType", str(exc)) from exc

    return RunTriggerOut(run_id=run_id, trigger_type=trigger_type, ticker=ticker, status="queued")


@router.get("", response_model=RunListResponse)
async def list_runs(
    session: AsyncSession = Depends(DbSession),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> RunListResponse:
    """History, newest first. Ordered by `created_at DESC, id DESC` -- the
    same tie-break `routers/reports.py` uses, and for the same reason:
    `created_at` is the only timestamp every row is guaranteed to have (a
    `queued` row has nothing else), and two runs can share a `created_at`
    when they land inside the same second."""
    total = (await session.execute(select(func.count()).select_from(RunRow))).scalar_one()

    stmt = (
        select(RunRow)
        .order_by(RunRow.created_at.desc(), RunRow.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return RunListResponse(
        items=[RunSummaryOut(**_summary_fields(r)) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run(
    run_id: str,
    session: AsyncSession = Depends(DbSession),
    log_offset: int = Query(default=0, ge=0),
) -> RunDetailOut:
    """Status plus a log tail.

    `log_offset` is a character offset into `runs.log`; the response returns
    everything from there onward, plus the offset to poll from next
    (`log_offset` in the response body = the full log's current length).
    Offsets are monotonic because `runs.log` is append-only and never
    rewritten -- including once the cap in
    `techinves.runs.service.RUN_LOG_MAX_CHARS` is reached, at which point the
    log simply stops growing rather than having its head truncated out from
    under an outstanding cursor. An offset past the current length (a stale
    cursor read against a row that was somehow shorter, which cannot happen
    here but is handled defensively) yields an empty tail, not an error.
    """
    row = (await session.execute(select(RunRow).where(RunRow.run_id == run_id))).scalars().first()
    if row is None:
        raise HTTPException(404, f"unknown run: {run_id}")

    full_log = row.log or ""
    tail = full_log[log_offset:] if log_offset < len(full_log) else ""
    return RunDetailOut(**_summary_fields(row), log=tail, log_offset=len(full_log))
