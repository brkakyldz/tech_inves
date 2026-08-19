"""Run-to-run delta (plan §8 Faz 7a).

The delta is a **UI/API affordance only**. ADR 0009's decision that the
report narrative carries no movement language is untouched by everything
here: nothing in `pipeline/synthesis/**` reads these fields and the verifier
never sees them.

What these tests defend is the one failure mode the phase was scoped
against: *showing a confident number for a pair the data cannot support*.
Every test below is an assertion that the API either produces a delta it can
justify, or produces none and says why -- never a zero, and never a
difference across a boundary the two numbers were not computed on the same
side of.
"""

from __future__ import annotations

from sqlalchemy import select

from techinves.api.repositories import (
    DELTA_COHORT_CHANGED,
    DELTA_FIRST_RUN,
    DELTA_INCOMPLETE_RUN,
    DELTA_REGIME_CHANGED,
    DELTA_UNKNOWN_PROVENANCE,
)
from techinves.api.seed_mock import SEED_CURRENT_RUN_ID
from techinves.db.models import CompanyRow, RunRow, ScoreHistoryRow
from techinves.db.session import get_sessionmaker

# A seeded ticker with backfilled quarterly history: it has a row in the
# current run and at least one row before it.
TICKER_WITH_HISTORY = "MSFT"
# A seeded ticker with an empty history array in the fixtures -- it exists
# only in the current run, which is the first-ever-run shape.
TICKER_WITHOUT_HISTORY = "AAPL"


async def _rows(engine, ticker: str) -> list[ScoreHistoryRow]:
    """This ticker's score rows, oldest first."""
    async with get_sessionmaker(engine)() as session:
        return list(
            (
                await session.execute(
                    select(ScoreHistoryRow)
                    .join(CompanyRow, ScoreHistoryRow.company_id == CompanyRow.id)
                    .where(CompanyRow.ticker == ticker)
                    .order_by(ScoreHistoryRow.id)
                )
            )
            .scalars()
            .all()
        )


async def _mutate(engine, fn):
    async with get_sessionmaker(engine)() as session:
        await fn(session)
        await session.commit()


# --------------------------------------------------------------------------
# The happy path: a comparable pair produces a real number
# --------------------------------------------------------------------------


async def test_delta_is_the_difference_against_the_previous_scoring_run(
    client, seeded_engine
):
    rows = await _rows(seeded_engine, TICKER_WITH_HISTORY)
    assert len(rows) >= 2, "fixture precondition: needs a comparable pair"
    current, previous = rows[-1], rows[-2]

    body = (await client.get(f"/v1/companies/{TICKER_WITH_HISTORY}")).json()
    delta = body["delta"]

    assert delta["unavailableReason"] is None
    assert delta["delta"] == current.composite_score - previous.composite_score
    assert delta["currentRunId"] == current.run_id
    assert delta["previousRunId"] == previous.run_id
    assert delta["previousComposite"] == previous.composite_score


async def test_the_seeded_demo_database_yields_real_deltas(client):
    """Demo mode (Faz 6) is the realistic multi-run case, so it must actually
    exercise the comparable path rather than degrading to reasons."""
    items = (await client.get("/v1/companies", params={"pageSize": 200})).json()["items"]
    available = [i for i in items if i["delta"]["delta"] is not None]
    assert len(available) >= 30, "seeded fixtures should give most tickers a delta"


async def test_highlights_carry_the_delta_the_landing_page_renders(client):
    """`ScoreHighlights` -> `ScoreCard` -> `DeltaIndicator` reads this."""
    items = (await client.get("/v1/scores/highlights")).json()
    assert items
    for item in items:
        assert "delta" in item
        assert set(item["delta"]) == {
            "delta",
            "previousComposite",
            "previousRunId",
            "currentRunId",
            "unavailableReason",
        }


# --------------------------------------------------------------------------
# Gate test 1 -- the first-ever run is a normal state, not an error or a zero
# --------------------------------------------------------------------------


async def test_first_ever_run_reports_first_run_rather_than_a_zero_delta(
    client, seeded_engine
):
    rows = await _rows(seeded_engine, TICKER_WITHOUT_HISTORY)
    assert len(rows) == 1, "fixture precondition: nothing precedes this score"

    response = await client.get(f"/v1/companies/{TICKER_WITHOUT_HISTORY}")
    # Not an error: the company page renders normally, minus the delta.
    assert response.status_code == 200
    delta = response.json()["delta"]

    assert delta["unavailableReason"] == DELTA_FIRST_RUN
    # The whole point: 0.0 would assert "measured, and unchanged".
    assert delta["delta"] is None
    assert delta["previousRunId"] is None
    assert delta["previousComposite"] is None
    assert delta["currentRunId"] == SEED_CURRENT_RUN_ID


# --------------------------------------------------------------------------
# Gate test 2 -- an incomparable previous run yields no number, with a reason
# --------------------------------------------------------------------------


async def test_delta_is_unavailable_rather_than_fabricated_when_the_previous_run_is_not_comparable(
    client, seeded_engine
):
    """The previous run scored this company against a differently-sized
    cohort. A composite score is a cohort-relative percentile (ADR 0005), so
    the difference would measure the cohort, not the company -- ADR 0009's
    data-boundary objection, generalised past the FMP->EDGAR seam it was
    originally written about.
    """
    rows = await _rows(seeded_engine, TICKER_WITH_HISTORY)
    current, previous = rows[-1], rows[-2]
    # Sanity: with the fixture untouched, this pair *is* comparable, so the
    # assertions below cannot pass vacuously.
    before = (await client.get(f"/v1/companies/{TICKER_WITH_HISTORY}")).json()["delta"]
    assert before["delta"] is not None

    async def _shrink_cohort(session):
        row = await session.get(ScoreHistoryRow, previous.id)
        row.cohort_size = current.cohort_size - 3

    await _mutate(seeded_engine, _shrink_cohort)

    delta = (await client.get(f"/v1/companies/{TICKER_WITH_HISTORY}")).json()["delta"]
    assert delta["unavailableReason"] == DELTA_COHORT_CHANGED
    assert delta["delta"] is None
    # The operand is withheld too: handing a client the previous score is the
    # same wrong number one subtraction away.
    assert delta["previousComposite"] is None
    # The run is still named, so the UI can say *which* comparison it declined.
    assert delta["previousRunId"] == previous.run_id


async def test_an_incomparable_pair_does_not_suppress_every_other_ticker(
    client, seeded_engine
):
    """Comparability is decided per company, so one bad pair must not blank
    the whole screener."""
    rows = await _rows(seeded_engine, TICKER_WITH_HISTORY)
    previous = rows[-2]

    async def _flip_regime(session):
        row = await session.get(ScoreHistoryRow, previous.id)
        row.regime = "unprofitable_growth"

    await _mutate(seeded_engine, _flip_regime)

    items = (await client.get("/v1/companies", params={"pageSize": 200})).json()["items"]
    by_ticker = {i["ticker"]: i["delta"] for i in items}
    assert by_ticker[TICKER_WITH_HISTORY]["unavailableReason"] == DELTA_REGIME_CHANGED
    assert by_ticker[TICKER_WITH_HISTORY]["delta"] is None
    assert any(
        d["delta"] is not None
        for t, d in by_ticker.items()
        if t != TICKER_WITH_HISTORY
    )


async def test_a_previous_run_that_never_finished_is_not_differenced_against(
    client, seeded_engine
):
    rows = await _rows(seeded_engine, TICKER_WITH_HISTORY)
    previous = rows[-2]

    async def _fail_the_run(session):
        run = (
            await session.execute(select(RunRow).where(RunRow.run_id == previous.run_id))
        ).scalar_one()
        run.status = "failed"
        run.error = "RuntimeError: provider timed out"

    await _mutate(seeded_engine, _fail_the_run)

    delta = (await client.get(f"/v1/companies/{TICKER_WITH_HISTORY}")).json()["delta"]
    assert delta["unavailableReason"] == DELTA_INCOMPLETE_RUN
    assert delta["delta"] is None


async def test_a_previous_row_with_no_run_record_has_unknown_provenance(
    client, seeded_engine
):
    """The shape a pre-reset or imported row would arrive in: score data whose
    run -- and therefore whose inputs and data source -- this database cannot
    account for. ADR 0009's seam, if one ever reappears, lands here."""
    rows = await _rows(seeded_engine, TICKER_WITH_HISTORY)
    previous = rows[-2]

    async def _orphan_the_row(session):
        run = (
            await session.execute(select(RunRow).where(RunRow.run_id == previous.run_id))
        ).scalar_one()
        await session.delete(run)

    await _mutate(seeded_engine, _orphan_the_row)

    delta = (await client.get(f"/v1/companies/{TICKER_WITH_HISTORY}")).json()["delta"]
    assert delta["unavailableReason"] == DELTA_UNKNOWN_PROVENANCE
    assert delta["delta"] is None
    assert delta["previousComposite"] is None


async def test_a_current_run_that_failed_partway_is_not_differenced_from_either(
    client, seeded_engine
):
    """Both sides are checked. Rows written by a run that died partway are
    individually real but collectively partial."""

    async def _fail_current(session):
        run = (
            await session.execute(
                select(RunRow).where(RunRow.run_id == SEED_CURRENT_RUN_ID)
            )
        ).scalar_one()
        run.status = "failed"

    await _mutate(seeded_engine, _fail_current)

    delta = (await client.get(f"/v1/companies/{TICKER_WITH_HISTORY}")).json()["delta"]
    assert delta["unavailableReason"] == DELTA_INCOMPLETE_RUN
    assert delta["delta"] is None


# --------------------------------------------------------------------------
# "Previous" under interleaved trigger types
# --------------------------------------------------------------------------


async def test_a_report_run_between_two_scoring_runs_is_not_the_previous_run(
    client, seeded_engine
):
    """`report` and `company` runs write no `score_history` rows, so they are
    not in the series at all. A report run landing between two score refreshes
    must not become anybody's comparison baseline."""
    rows = await _rows(seeded_engine, TICKER_WITH_HISTORY)
    current, previous = rows[-1], rows[-2]

    async def _insert_report_run(session):
        session.add(
            RunRow(
                run_id="interleaved-report-run",
                trigger_type="report",
                status="succeeded",
                created_at=current.generated_at,
            )
        )

    await _mutate(seeded_engine, _insert_report_run)

    delta = (await client.get(f"/v1/companies/{TICKER_WITH_HISTORY}")).json()["delta"]
    assert delta["previousRunId"] == previous.run_id
    assert delta["delta"] == current.composite_score - previous.composite_score


async def test_a_run_that_skipped_this_ticker_is_not_its_previous_run(
    client, seeded_engine
):
    """Ingest writes no score row for an `insufficient_data` block, so a run
    can legitimately score 41 of 43 tickers. The skipped ticker compares
    against the last run that actually scored it, not against the run that
    passed it over."""
    rows = await _rows(seeded_engine, TICKER_WITH_HISTORY)
    current, previous = rows[-1], rows[-2]

    async def _add_a_run_covering_nobody(session):
        # Inserted between `previous` and `current` in run order, but with no
        # score row for this ticker.
        session.add(
            RunRow(
                run_id="partial-scores-run",
                trigger_type="scores",
                status="succeeded",
                created_at=current.generated_at,
                company_count=1,
            )
        )

    await _mutate(seeded_engine, _add_a_run_covering_nobody)

    delta = (await client.get(f"/v1/companies/{TICKER_WITH_HISTORY}")).json()["delta"]
    assert delta["previousRunId"] == previous.run_id
    assert delta["delta"] == current.composite_score - previous.composite_score
