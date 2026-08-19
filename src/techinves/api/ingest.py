"""Ingestion job: turns a scoring run's output into DB rows.

Faz 1 (`reports/research/BACKEND_IMPLEMENTATION_PLAN.md` Section 5). In production this
reads `scores.json` from S3 (`s3://.../scores/{run_id}/scores.json`, written
by the weekly pipeline); for now it takes a local file path so it can run and
be tested without any pipeline/S3 dependency. Swapping the source to S3 later
is a change to `_load_score_blocks`'s input only -- the validate/upsert
contract below does not change.

Idempotent by (ticker, run_id): re-running the same file under the same run
id twice leaves the DB unchanged. All *data* writes for one run happen in a
single transaction -- a partial run is never visible
(BACKEND_IMPLEMENTATION_PLAN.md Section 5.3).

This is the `scores` trigger of ADR 0010 §1, and its bookkeeping row is a
`runs` row with `trigger_type="scores"` -- the separate `ingestion_runs`
table was folded into `runs` in Faz 2, since a score refresh is one of the
three trigger types, not a different kind of thing.

That row is deliberately kept OUTSIDE the data transaction: it is committed
before any data write starts, so that rolling the data back on failure cannot
also erase the record that the run happened. The terminal status
("succeeded"/"failed") is then written by a separate transaction. `/health`
depends on this -- a run that fails and leaves no row at all is
indistinguishable from a run that never started.

**The CLI never takes the in-flight run lock.** That lock is a unique partial
index over `runs(trigger_type) WHERE status IN ('queued','running')`
(`db/models.py`), held by a *row* and released only when that row lands
terminal. `techinves.runs.reconcile` is what clears a row whose process
died -- and it runs at API startup, which this CLI is not. So a CLI ingest
that marked its row `running` would (a) collide with a genuinely in-flight
`scores` run and die on a raw `IntegrityError`, and (b) on a Ctrl-C strand
the lock, leaving the API refusing every `scores` trigger until it is
restarted. Instead the CLI's row is created **already terminal**, as `failed`
carrying `INGEST_INCOMPLETE_ERROR`, and is rewritten to its real outcome when
the ingest lands. It is outside the lock's predicate the whole way through,
so it never holds the lock and never needs reconciling; a process that dies
mid-ingest leaves a row that already says, accurately, that the ingest did
not complete. A row that *is* non-terminal belongs to the API's executor and
is never written to at all -- `IngestRunConflict` is raised instead.

The schema is owned by Alembic (`alembic/versions/`), not by this module.
`create_schema()` exists only as an explicit bootstrap for tests and for
throwaway dev databases; it is never called on the ingest path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from techinves.api._time import now_naive_utc, to_naive_utc
from techinves.api.schemas import COHORT_LABELS
from techinves.db.models import (
    Base,
    CategoryScoreRow,
    CohortRow,
    CompanyRow,
    RiskMetricsRow,
    RunRow,
    ScoreHistoryRow,
)
from techinves.db.session import get_sessionmaker, make_engine
from techinves.models import ScoreBlock
from techinves.runs.reconcile import NON_TERMINAL_STATUSES

_SCORE_BLOCK_LIST = TypeAdapter(list[ScoreBlock])

#: What a CLI-managed run row says between "the ingest started" and "the
#: ingest landed". The row is `failed` from the moment it is created, which
#: keeps it outside the in-flight lock's predicate (see module docstring) and
#: makes a killed ingest self-describing rather than stranding a lock. A
#: fixed sentence with no interpolation, so a caller can test for it exactly.
INGEST_INCOMPLETE_ERROR = (
    "The ingest did not complete: the process ended before a terminal status "
    "was written. Nothing partial was published -- the data transaction is "
    "all-or-nothing. Re-run techinves-ingest with the same --run-id; the "
    "upsert is idempotent."
)


class IngestionValidationError(RuntimeError):
    """Raised when the input file fails schema validation. The DB is never
    touched when this is raised -- see module docstring."""


class IngestRunConflict(RuntimeError):
    """The `runs` row for this run id is non-terminal, i.e. some other process
    -- the API's background executor -- is executing it right now and holds
    the in-flight lock through it.

    Raised instead of writing anything. Reopening that row would either
    violate the lock's unique partial index (a raw `IntegrityError` out of a
    `commit()`, which is not a diagnosable CLI error) or, worse, succeed and
    let two writers publish under one run id.
    """


def _load_score_blocks(path: Path) -> list[ScoreBlock]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _SCORE_BLOCK_LIST.validate_python(raw)
    except Exception as exc:  # noqa: BLE001 - re-raised as a domain error on purpose
        raise IngestionValidationError(f"{path} does not match ScoreBlock[] schema: {exc}") from exc


async def _get_or_create_cohort(session: AsyncSession, code: str) -> CohortRow:
    row = (await session.execute(select(CohortRow).where(CohortRow.code == code))).scalar_one_or_none()
    if row is None:
        row = CohortRow(code=code, label=COHORT_LABELS.get(code, code), weight_profile={}, methodology_version="1.0")
        session.add(row)
        await session.flush()
    return row


async def _get_or_create_company(
    session: AsyncSession, ticker: str, cohort: CohortRow, company_names: dict[str, str]
) -> CompanyRow:
    row = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == ticker))).scalar_one_or_none()
    name = company_names.get(ticker, ticker)
    if row is None:
        row = CompanyRow(ticker=ticker, name=name, cohort_id=cohort.id, is_active=True)
        session.add(row)
        await session.flush()
    else:
        row.cohort_id = cohort.id
        if ticker in company_names:
            row.name = name
    return row


async def create_schema(engine: AsyncEngine) -> None:
    """Create every table from the ORM metadata.

    Explicit bootstrap only -- for tests and for a scratch dev database with no
    Alembic history. Production schema comes from `alembic upgrade head`; running
    `create_all` on the ingest path would race the migrations (it creates tables
    at the ORM's current shape, so Alembic then finds a schema it never stamped).
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _finish_run(
    session_maker,
    run_pk: int,
    *,
    status: str | None,
    company_count: int | None = None,
    error: str | None = None,
) -> None:
    """Write a terminal status onto the already-committed run row, in its own
    transaction so it survives a rollback of the data-writing transaction.

    `status=None` writes the measurement columns only and leaves the run's
    lifecycle alone -- the `manage_run_row=False` case, where the background
    executor owns status, `finished_at` and the in-flight lock."""
    async with session_maker() as session:
        run = await session.get(RunRow, run_pk)
        if run is None:  # pragma: no cover - the row is committed before this runs
            return
        if status is not None:
            run.status = status
            run.finished_at = now_naive_utc()
            # The row has carried `INGEST_INCOMPLETE_ERROR` since it was
            # created; landing terminal replaces it with the real outcome, so
            # a succeeded run does not keep advertising a failure that did not
            # happen. The `error is not None` branch below then writes the
            # genuine message on the failure path.
            run.error = None
        if company_count is not None:
            run.company_count = company_count
        if error is not None:
            run.error = error[:2000]
        await session.commit()


def _reopen_run_row(run: RunRow, now) -> None:
    """Point an existing `runs` row at this CLI ingest, without ever making it
    non-terminal.

    A row that is already non-terminal is somebody else's -- the API's
    executor is running it and holds the in-flight lock through it -- so it is
    refused rather than rewritten. Everything else is reopened in place, but
    to `failed`/`INGEST_INCOMPLETE_ERROR` rather than to `running`: the row
    stays outside the lock's predicate for the whole ingest, so this path can
    neither violate the partial index nor strand the lock if the process is
    killed.
    """
    if run.status in NON_TERMINAL_STATUSES:
        raise IngestRunConflict(
            f"run {run.run_id!r} is {run.status!r}: it is in flight under the "
            "application's background executor, which holds the in-flight run "
            "lock through this row. Ingesting into it from the CLI would "
            "collide with that run. Wait for it to finish (GET /v1/runs) or "
            "ingest under a different --run-id."
        )
    run.trigger_type = "scores"
    run.status = "failed"
    run.error = INGEST_INCOMPLETE_ERROR
    run.started_at = now
    run.finished_at = None


async def ingest(
    engine: AsyncEngine,
    scores_path: Path,
    *,
    run_id: str,
    company_names: dict[str, str] | None = None,
    manage_run_row: bool = True,
) -> int:
    """Load a `ScoreBlock[]` dump into the database under `run_id`.

    `manage_run_row` says who owns the bookkeeping row. `True` (the CLI, and
    the historical behaviour) means this function creates/reopens the `runs`
    row and writes its terminal status. `False` means a caller already owns
    the row's lifecycle -- specifically `techinves.runs`' background executor,
    which created the row, holds the in-flight lock through it, and lands the
    terminal status itself. Writing "succeeded" from in here under that
    caller would release the lock while the executor still considered the run
    in flight, and would let a second trigger start against a run that has
    not finished.

    Under `True` the row is never made non-terminal, so this function never
    acquires the in-flight lock (module docstring). If the row already exists
    and *is* non-terminal, `IngestRunConflict` is raised and nothing is
    written.
    """
    company_names = company_names or {}

    blocks = _load_score_blocks(scores_path)  # raises before any DB write on schema mismatch

    session_maker = get_sessionmaker(engine)

    # Transaction 1: the run row, committed before any risky work, so the
    # failure path below has a durable row to mark "failed".
    run_pk: int | None = None
    async with session_maker() as run_session:
        now = now_naive_utc()
        run = (
            await run_session.execute(select(RunRow).where(RunRow.run_id == run_id))
        ).scalar_one_or_none()
        if run is None:
            if not manage_run_row:
                raise IngestionValidationError(
                    f"no runs row for run_id={run_id!r}; with manage_run_row=False "
                    "the caller must have created it"
                )
            run = RunRow(
                run_id=run_id,
                trigger_type="scores",
                created_at=now,
                started_at=now,
                status="failed",
                error=INGEST_INCOMPLETE_ERROR,
                company_count=0,
            )
            run_session.add(run)
            try:
                await run_session.commit()
            except IntegrityError as exc:
                # The in-flight lock cannot be what fired: this row is created
                # terminal, outside the lock's predicate. So it is
                # `uq_runs_run_id` -- another writer created the same run id
                # between the SELECT above and this INSERT. Adopt its row
                # (subject to the same in-flight check as any other existing
                # row) rather than letting a raw DBAPI error out of the CLI.
                await run_session.rollback()
                run = (
                    await run_session.execute(select(RunRow).where(RunRow.run_id == run_id))
                ).scalar_one_or_none()
                if run is None:
                    raise IngestRunConflict(
                        f"could not create the runs row for run_id={run_id!r}, and no "
                        f"row exists to reuse: {type(exc).__name__}: {exc}"
                    ) from exc
                _reopen_run_row(run, now)
                await run_session.commit()
        elif manage_run_row:
            # A retry under the same run id reopens its row rather than
            # minting a second one -- `run_id` is unique, and the data
            # upsert below is keyed on it, so the bookkeeping has to match.
            _reopen_run_row(run, now)
            await run_session.commit()
        run_pk = run.id

    # Transaction 2: the data writes. Rolled back as a unit on failure; the run
    # row is not part of it.
    async with session_maker() as session:
        upserted_count = 0
        try:
            for block in blocks:
                # A block with no usable metric carries no score to store
                # (composite_score is None, and score_history.composite_score is
                # NOT NULL). Skipping the score_history write is deliberate:
                # publishing a placeholder would recreate the "no data == very
                # weak" confusion that ADR 0001 clause 6 exists to prevent.
                #
                # But a ticker that scored in a prior run and is
                # insufficient_data in this one must not keep serving that stale
                # row as "current" -- that is the exact silent-wrong-number
                # failure ADR 0001 was written against, just one layer down.
                # Null out current_score_id (the prior score_history row stays
                # in the table for history) so the API surfaces "no data"
                # instead.
                if block.insufficient_data or block.composite_score is None or block.sector_percentile is None:
                    company = (
                        await session.execute(
                            select(CompanyRow).where(CompanyRow.ticker == block.ticker)
                        )
                    ).scalar_one_or_none()
                    if company is not None and company.current_score_id is not None:
                        company.current_score_id = None
                        session.add(company)
                    continue
                cohort = await _get_or_create_cohort(session, block.cohort.value)
                company = await _get_or_create_company(session, block.ticker, cohort, company_names)

                existing = (
                    await session.execute(
                        select(ScoreHistoryRow).where(
                            ScoreHistoryRow.company_id == company.id,
                            ScoreHistoryRow.run_id == run_id,
                        )
                    )
                ).scalar_one_or_none()

                if existing is not None:
                    sh = existing
                else:
                    sh = ScoreHistoryRow(company_id=company.id, run_id=run_id)
                    session.add(sh)

                sh.composite_score = block.composite_score
                sh.composite_band = block.composite_band
                sh.sector_percentile = block.sector_percentile
                sh.sector_percentile_band = block.sector_percentile_band
                sh.coverage_pct = block.coverage_pct
                sh.low_reliability = block.low_reliability
                sh.regime = block.regime.value
                sh.cohort_size = block.cohort_size
                sh.extended_cohort = block.extended_cohort
                sh.distress_ceiling_applied = block.distress_ceiling_applied
                sh.warnings = list(block.warnings)
                sh.generated_at = to_naive_utc(block.generated_at)
                await session.flush()

                await session.execute(
                    CategoryScoreRow.__table__.delete().where(CategoryScoreRow.score_history_id == sh.id)
                )
                for cat in block.categories:
                    session.add(
                        CategoryScoreRow(
                            score_history_id=sh.id,
                            category_name=cat.name,
                            score=cat.score,
                            weight=cat.weight,
                            coverage=cat.coverage,
                            metrics=[m.model_dump(mode="json") for m in cat.metrics],
                        )
                    )

                await session.execute(
                    RiskMetricsRow.__table__.delete().where(RiskMetricsRow.score_history_id == sh.id)
                )
                risk = block.risk
                session.add(
                    RiskMetricsRow(
                        score_history_id=sh.id,
                        score=risk.score,
                        band=risk.band,
                        altman_z=risk.altman_z,
                        altman_zone=risk.altman_zone.value,
                        piotroski_f=risk.piotroski_f,
                        net_debt_ebitda=risk.net_debt_ebitda,
                        interest_coverage=risk.interest_coverage,
                        cash_runway_months=risk.cash_runway_months,
                        burn_multiple=risk.burn_multiple,
                        dilution_yoy_pct=risk.dilution_yoy_pct,
                        components_used=list(risk.components_used),
                    )
                )

                company.current_score_id = sh.id
                session.add(company)
                upserted_count += 1

            await session.commit()
        except Exception as exc:
            await session.rollback()
            await _finish_run(
                session_maker,
                run_pk,
                status="failed" if manage_run_row else None,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    await _finish_run(
        session_maker,
        run_pk,
        status="succeeded" if manage_run_row else None,
        company_count=upserted_count,
    )

    return upserted_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scores_json", type=Path, help="path to a ScoreBlock[] JSON dump")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--company-names", type=Path, default=None, help="optional {ticker: name} JSON file")
    args = parser.parse_args()

    company_names = {}
    if args.company_names is not None:
        company_names = json.loads(args.company_names.read_text(encoding="utf-8"))

    engine = make_engine(args.database_url)
    try:
        count = asyncio.run(
            ingest(
                engine,
                args.scores_json,
                run_id=args.run_id,
                company_names=company_names,
            )
        )
    except (IngestRunConflict, IngestionValidationError) as exc:
        # Both are diagnosable states a user can act on, so they leave as a
        # message and a non-zero exit code rather than as a traceback.
        raise SystemExit(f"techinves-ingest: {exc}") from exc
    print(f"ingested {count} companies (run_id={args.run_id})")


if __name__ == "__main__":
    main()
