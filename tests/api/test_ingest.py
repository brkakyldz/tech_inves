"""Ingestion job tests, built on the same golden 13-company synthetic cohort
as tests/golden/test_golden_watchlist.py -- a real engine.score_watchlist()
output run through ScoreBlock validation and the upsert transaction.
"""

from __future__ import annotations

import json
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from techinves.api import ingest as ingest_module
from techinves.api.ingest import (
    INGEST_INCOMPLETE_ERROR,
    IngestionValidationError,
    IngestRunConflict,
    create_schema,
    ingest,
)
from techinves.db.models import CategoryScoreRow, CompanyRow, RiskMetricsRow, RunRow, ScoreHistoryRow
from techinves.db.session import get_sessionmaker
from techinves.models import Cohort
from techinves.scoring import engine as engine_module

from tests.conftest import StubFactsProvider
from tests.golden.test_golden_watchlist import _build_cohort_c_facts, _cohort_c_watchlist


@pytest.fixture
def golden_score_blocks(monkeypatch):
    watchlist = _cohort_c_watchlist()
    facts = _build_cohort_c_facts()
    monkeypatch.setattr(engine_module, "load_watchlist", lambda: watchlist)
    return engine_module.score_watchlist(provider=StubFactsProvider(facts))


@pytest.fixture
def scores_json_file(golden_score_blocks, tmp_path):
    path = tmp_path / "scores.json"
    payload = [block.model_dump(mode="json") for block in golden_score_blocks.values()]
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
async def ingest_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    # Schema is owned by Alembic in production; tests bootstrap it explicitly
    # (ingest() no longer runs create_all on every call).
    await create_schema(engine)
    yield engine
    await engine.dispose()


async def test_ingest_writes_all_companies(ingest_engine, scores_json_file, golden_score_blocks):
    count = await ingest(ingest_engine, scores_json_file, run_id="run-1")
    assert count == len(golden_score_blocks)

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        companies = (await session.execute(select(CompanyRow))).scalars().all()
        assert {c.ticker for c in companies} == set(golden_score_blocks.keys())

        distressed = (
            await session.execute(select(CompanyRow).where(CompanyRow.ticker == "DISTRESSED"))
        ).scalar_one()
        assert distressed.current_score_id is not None
        score = await session.get(ScoreHistoryRow, distressed.current_score_id)
        assert score.distress_ceiling_applied is True
        assert score.composite_score <= 70.0

        run = (await session.execute(select(RunRow).where(RunRow.run_id == "run-1"))).scalar_one()
        # The ingest path is ADR 0010 §1's `scores` trigger; `ingestion_runs`
        # folded into `runs` in Faz 2, and "success" became "succeeded".
        assert run.trigger_type == "scores"
        assert run.status == "succeeded"
        assert run.company_count == len(golden_score_blocks)


async def test_ingest_is_idempotent(ingest_engine, scores_json_file, golden_score_blocks):
    """Retrying the *same* run id upserts rather than duplicating.

    Was keyed on `week_of`, so two different run ids in one week collapsed
    into one row; the identity is now the run (ADR 0010 §2), so idempotency
    is per run id -- which is also what a retry actually is."""
    await ingest(ingest_engine, scores_json_file, run_id="run-1")
    await ingest(ingest_engine, scores_json_file, run_id="run-1")

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        history_rows = (await session.execute(select(ScoreHistoryRow))).scalars().all()
        assert len(history_rows) == len(golden_score_blocks)  # not doubled

        one_history = (
            await session.execute(select(ScoreHistoryRow).where(ScoreHistoryRow.company_id == history_rows[0].company_id))
        ).scalars().all()
        assert len(one_history) == 1  # (company_id, run_id) upserted, not duplicated

        cats = (
            await session.execute(select(CategoryScoreRow).where(CategoryScoreRow.score_history_id == one_history[0].id))
        ).scalars().all()
        assert len(cats) == 4  # not doubled by the second run's delete+reinsert


async def test_ingest_different_run_adds_history_point(ingest_engine, scores_json_file, golden_score_blocks):
    """A second run -- even minutes after the first, which week keying could
    not represent at all -- adds a history point rather than overwriting."""
    await ingest(ingest_engine, scores_json_file, run_id="run-1")
    await ingest(ingest_engine, scores_json_file, run_id="run-2")

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        company = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "IT0"))).scalar_one()
        history = (
            await session.execute(select(ScoreHistoryRow).where(ScoreHistoryRow.company_id == company.id))
        ).scalars().all()
        assert len(history) == 2
        assert company.current_score_id in {h.id for h in history if h.run_id == "run-2"}


async def test_ingest_rejects_malformed_input_without_touching_db(ingest_engine, tmp_path):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(json.dumps([{"ticker": "X", "not_a_valid": "score_block"}]), encoding="utf-8")

    with pytest.raises(IngestionValidationError):
        await ingest(ingest_engine, bad_path, run_id="bad-run")

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        companies = (await session.execute(select(CompanyRow))).scalars().all()
        assert companies == []
        runs = (await session.execute(select(RunRow))).scalars().all()
        assert runs == []  # run row is only created after validation succeeds


async def test_failed_ingest_leaves_run_row_marked_failed(ingest_engine, scores_json_file, monkeypatch):
    """The run row is committed before the data writes, so rolling those back
    must not take the run row with it -- otherwise a crashed ingest is
    indistinguishable from one that never started (/health depends on this)."""

    async def boom(*args, **kwargs):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(ingest_module, "_get_or_create_company", boom)

    with pytest.raises(RuntimeError, match="disk on fire"):
        await ingest(ingest_engine, scores_json_file, run_id="doomed-run")

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        run = (
            await session.execute(select(RunRow).where(RunRow.run_id == "doomed-run"))
        ).scalar_one()
        assert run.status == "failed"
        assert run.finished_at is not None
        assert "disk on fire" in (run.error or "")

        # the data-writing transaction really was rolled back
        assert (await session.execute(select(CompanyRow))).scalars().all() == []
        assert (await session.execute(select(ScoreHistoryRow))).scalars().all() == []


async def test_ingest_nulls_current_score_when_ticker_becomes_insufficient_data(
    ingest_engine, scores_json_file, golden_score_blocks, tmp_path
):
    """A ticker that scored in one run and is insufficient_data in the next
    must stop serving the earlier row as "current" -- the ingest step must not
    reintroduce the stale-wrong-number failure ADR 0001 was written against."""
    await ingest(ingest_engine, scores_json_file, run_id="run-1")

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        company = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "IT0"))).scalar_one()
        week1_score_id = company.current_score_id
        assert week1_score_id is not None

    week2_blocks = list(golden_score_blocks.values())
    for i, block in enumerate(week2_blocks):
        if block.ticker == "IT0":
            week2_blocks[i] = block.model_copy(
                update={
                    "composite_score": None,
                    "composite_band": "No Data",
                    "sector_percentile": None,
                    "insufficient_data": True,
                }
            )
            break

    week2_path = tmp_path / "scores_week2.json"
    week2_path.write_text(
        json.dumps([b.model_dump(mode="json") for b in week2_blocks]), encoding="utf-8"
    )
    await ingest(ingest_engine, week2_path, run_id="run-2")

    async with session_maker() as session:
        company = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "IT0"))).scalar_one()
        assert company.current_score_id is None

        # run 1's row is untouched, not deleted -- only the "current" pointer moved
        week1_row = await session.get(ScoreHistoryRow, week1_score_id)
        assert week1_row is not None
        assert week1_row.run_id == "run-1"


async def test_ingest_skips_null_composite_even_when_not_flagged_insufficient(
    ingest_engine, golden_score_blocks, tmp_path
):
    """A block can have composite_score=None while insufficient_data=False
    (independently of the piotroski-fabricated-zero bug). Writing that NULL
    into score_history's NOT NULL column used to abort the whole batch's
    transaction -- it must be skipped instead, and the rest of the batch must
    still commit."""
    blocks = list(golden_score_blocks.values())
    for i, block in enumerate(blocks):
        if block.ticker == "IT0":
            blocks[i] = block.model_copy(
                update={"composite_score": None, "insufficient_data": False}
            )
            break

    path = tmp_path / "scores_bad_composite.json"
    path.write_text(json.dumps([b.model_dump(mode="json") for b in blocks]), encoding="utf-8")

    count = await ingest(ingest_engine, path, run_id="run-1")
    assert count == len(blocks) - 1

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        it0 = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "IT0"))).scalar_one_or_none()
        assert it0 is None or it0.current_score_id is None

        it1 = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "IT1"))).scalar_one()
        assert it1.current_score_id is not None  # the rest of the batch still committed


async def test_ingest_normalizes_tz_aware_generated_at_to_naive_utc(
    ingest_engine, golden_score_blocks, tmp_path
):
    """SQLite silently accepts a tz-aware datetime into a naive DateTime
    column; Postgres rejects it. Normalize at the ingest boundary so the two
    backends behave the same."""
    from datetime import timezone

    blocks = list(golden_score_blocks.values())
    for i, block in enumerate(blocks):
        if block.ticker == "IT0":
            blocks[i] = block.model_copy(
                update={"generated_at": block.generated_at.replace(tzinfo=timezone.utc)}
            )
            break

    path = tmp_path / "scores_tz_aware.json"
    path.write_text(json.dumps([b.model_dump(mode="json") for b in blocks]), encoding="utf-8")

    await ingest(ingest_engine, path, run_id="run-1")

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        company = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "IT0"))).scalar_one()
        score = await session.get(ScoreHistoryRow, company.current_score_id)
        assert score.generated_at.tzinfo is None


async def test_ingest_persists_zero_coverage_as_null_not_zero(
    ingest_engine, golden_score_blocks, tmp_path
):
    """A category (or the risk sub-score) with zero computable metrics must
    reach the DB as NULL, not 0.0 -- 0.0 is a real "ranked last" measurement,
    NULL is "not measured at all" (see CategoryScore.score / RiskSubScore.score
    docstrings). The domain layer (categories.py/risk.py) already produces
    None for zero-coverage/no-data categories, and composite.py renormalizes
    composite_score over the categories that do have scores -- pinned by
    test_company_with_no_data_gets_a_no_data_state_not_a_zero_score
    (tests/golden/test_golden_watchlist.py) and
    test_risk_subscore_with_no_inputs_is_none_not_zero (tests/unit/test_risk.py).
    This test pins the persistence boundary instead: that ingest() does not
    flatten/coerce that None to 0.0 on the way to the row. It does not claim
    ingest() itself ever had the bug -- ingest() is a plain pass-through
    (score=cat.score)."""
    blocks = list(golden_score_blocks.values())
    for i, block in enumerate(blocks):
        if block.ticker == "IT0":
            zeroed_category = block.categories[0].model_copy(update={"score": None, "coverage": 0.0})
            zeroed_risk = block.risk.model_copy(update={"score": None, "band": "No data"})
            blocks[i] = block.model_copy(
                update={"categories": [zeroed_category, *block.categories[1:]], "risk": zeroed_risk}
            )
            break

    path = tmp_path / "scores_zero_coverage.json"
    path.write_text(json.dumps([b.model_dump(mode="json") for b in blocks]), encoding="utf-8")

    await ingest(ingest_engine, path, run_id="run-1")

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        company = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "IT0"))).scalar_one()

        cat_row = (
            await session.execute(
                select(CategoryScoreRow).where(
                    CategoryScoreRow.score_history_id == company.current_score_id,
                    CategoryScoreRow.coverage == 0.0,
                )
            )
        ).scalar_one()
        assert cat_row.score is None

        risk_row = (
            await session.execute(
                select(RiskMetricsRow).where(RiskMetricsRow.score_history_id == company.current_score_id)
            )
        ).scalar_one()
        assert risk_row.score is None
        assert risk_row.band == "No data"


# --- the bookkeeping row and the in-flight lock -----------------------------
#
# The lock is a unique partial index over runs(trigger_type) WHERE status IN
# ('queued','running'), and only `techinves.runs.reconcile` -- which runs at
# API startup -- can clear a row whose process died. The CLI is not that
# process, so it must stay outside the predicate the whole way through.


async def _run_row_statuses_during_ingest(monkeypatch, run_id: str) -> list[str]:
    """Record `runs.status` as observed from inside the data transaction.

    The run row is committed by transaction 1 before any data write, so a
    query issued from the data session sees whatever status the CLI parked it
    at for the duration of the ingest.
    """
    original = ingest_module._get_or_create_cohort
    seen: list[str] = []

    async def spy(session, code):
        run = (
            await session.execute(select(RunRow).where(RunRow.run_id == run_id))
        ).scalar_one()
        seen.append(run.status)
        return await original(session, code)

    monkeypatch.setattr(ingest_module, "_get_or_create_cohort", spy)
    return seen


async def test_the_cli_run_row_never_enters_the_lock_predicate(
    ingest_engine, scores_json_file, monkeypatch
):
    """A CLI ingest that marked its row `running` would hold the in-flight
    lock, and nothing in the CLI ever reconciles it -- so a Ctrl-C would leave
    the API refusing every `scores` trigger until it was restarted. The row is
    created terminal instead, and only ever moves between terminal states."""
    seen = await _run_row_statuses_during_ingest(monkeypatch, "run-1")

    await ingest(ingest_engine, scores_json_file, run_id="run-1")

    assert seen, "the data transaction never ran, so the test proves nothing"
    assert set(seen) == {"failed"}  # never 'queued'/'running': never the lock

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        run = (await session.execute(select(RunRow).where(RunRow.run_id == "run-1"))).scalar_one()
        assert run.status == "succeeded"
        # ...and the placeholder does not survive the landing: a succeeded run
        # must not keep advertising a failure that did not happen.
        assert run.error is None


async def test_retrying_a_run_id_never_flips_its_terminal_row_back_to_running(
    ingest_engine, scores_json_file, monkeypatch
):
    """A retry reopens the row rather than minting a second one -- but
    reopening it as `running` is what used to take the lock, and what raised a
    raw IntegrityError when a `scores` run was genuinely in flight."""
    await ingest(ingest_engine, scores_json_file, run_id="run-1")

    seen = await _run_row_statuses_during_ingest(monkeypatch, "run-1")
    await ingest(ingest_engine, scores_json_file, run_id="run-1")

    assert seen and set(seen) == {"failed"}

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        run = (await session.execute(select(RunRow).where(RunRow.run_id == "run-1"))).scalar_one()
        assert run.status == "succeeded"
        assert run.error is None
        assert run.finished_at is not None


@pytest.mark.parametrize("status", ["queued", "running"])
async def test_ingesting_into_a_run_the_executor_owns_is_refused(
    ingest_engine, scores_json_file, status
):
    """A non-terminal row belongs to the API's background executor, which is
    holding the in-flight lock through it. The CLI reports the conflict rather
    than writing into someone else's run."""
    from techinves.api._time import now_naive_utc

    now = now_naive_utc()
    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        session.add(
            RunRow(
                run_id="in-flight",
                trigger_type="scores",
                status=status,
                created_at=now,
                started_at=now,
            )
        )
        await session.commit()

    with pytest.raises(IngestRunConflict, match="in flight"):
        await ingest(ingest_engine, scores_json_file, run_id="in-flight")

    async with session_maker() as session:
        run = (
            await session.execute(select(RunRow).where(RunRow.run_id == "in-flight"))
        ).scalar_one()
        assert run.status == status  # untouched
        assert run.finished_at is None
        # and nothing was published under it
        assert (await session.execute(select(ScoreHistoryRow))).scalars().all() == []


async def test_an_ingest_killed_mid_flight_leaves_a_row_that_says_so(
    ingest_engine, scores_json_file, monkeypatch
):
    """Ctrl-C is the case the old `running` row could not survive: it left the
    lock held by a row nobody would ever land. The row is terminal from the
    start now, so a killed ingest leaves an accurate, lock-free record instead
    of a stranded lock. `KeyboardInterrupt` is not an `Exception`, so it skips
    the failure handler entirely -- exactly like a real Ctrl-C."""

    def boom(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr(ingest_module, "_get_or_create_company", boom)

    with pytest.raises(KeyboardInterrupt):
        await ingest(ingest_engine, scores_json_file, run_id="killed-run")

    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        run = (
            await session.execute(select(RunRow).where(RunRow.run_id == "killed-run"))
        ).scalar_one()
        assert run.status == "failed"  # outside the lock predicate
        assert run.error == INGEST_INCOMPLETE_ERROR
        assert (await session.execute(select(ScoreHistoryRow))).scalars().all() == []


async def test_a_run_id_that_appears_between_the_select_and_the_insert_is_adopted(
    tmp_path, scores_json_file, monkeypatch
):
    """The `IntegrityError` path, exercised rather than assumed.

    The row is created terminal, so the in-flight lock's partial index cannot
    be what fires on that INSERT -- only `uq_runs_run_id` can, when another
    writer created the same run id between the SELECT (which found nothing)
    and the commit. That row is adopted instead of a raw DBAPI error escaping
    the CLI as a traceback.

    A file-backed database, so the competing writer can be a plain `sqlite3`
    connection slipped in at the one point where the race is possible.
    """
    import sqlite3

    from sqlalchemy.ext.asyncio import create_async_engine

    from techinves.api._time import now_naive_utc

    db_path = tmp_path / "race.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await create_schema(engine)

    real_now = ingest_module.now_naive_utc
    raced = {"done": False}

    def now_that_races():
        # Called once, immediately after the SELECT that found no row and
        # before the INSERT is committed.
        if not raced["done"]:
            raced["done"] = True
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO runs (run_id, trigger_type, status, created_at, log,"
                    " duration_seconds, company_branches, macro_branches, findings_count,"
                    " failure_count, company_count, verdict_reason, total_tokens,"
                    " total_cost_usd, branch_yields)"
                    " VALUES (?, 'scores', 'succeeded', ?, '', 0, 0, 0, 0, 0, 0, '', 0, 0, '[]')",
                    ("raced", now_naive_utc().isoformat(sep=" ")),
                )
        return real_now()

    monkeypatch.setattr(ingest_module, "now_naive_utc", now_that_races)

    count = await ingest(engine, scores_json_file, run_id="raced")
    assert count > 0
    assert raced["done"], "the race never happened, so the test proves nothing"

    session_maker = get_sessionmaker(engine)
    async with session_maker() as session:
        runs = (await session.execute(select(RunRow).where(RunRow.run_id == "raced"))).scalars().all()
        assert len(runs) == 1  # adopted, not duplicated
        assert runs[0].status == "succeeded"
        assert runs[0].error is None
    await engine.dispose()


async def test_ingest_applies_company_name_overrides(ingest_engine, scores_json_file, tmp_path):
    names = {"IT0": "IT Zero Corp"}
    await ingest(
        ingest_engine, scores_json_file, run_id="run-1", company_names=names
    )
    session_maker = get_sessionmaker(ingest_engine)
    async with session_maker() as session:
        company = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "IT0"))).scalar_one()
        assert company.name == "IT Zero Corp"
        other = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "IT1"))).scalar_one()
        assert other.name == "IT1"  # falls back to ticker when no override given
