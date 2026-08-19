"""DB-backed covered-events store: per-run rows, a trailing window, and the
de-duplication guarantee ADR 0010 §9 exists to protect.

The re-key from `week_of` to the run replaced a `delete(everything)` +
re-insert save with a per-`event_key` upsert plus a prune, because a load that
returns only the last N runs cannot be paired with a save that wipes the rest.
These tests pin both halves of that, and the guarantee they exist to serve:
two consecutive runs must stop resurfacing the same events.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from pipeline.schemas import Finding
from pipeline.storage import covered_events_db_store as db_store
from pipeline.storage.covered_events_store import update_covered_events
from techinves.db.models import Base, CoveredEventRow
from techinves.db.session import get_sessionmaker


@pytest_asyncio.fixture
async def session_maker(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = get_sessionmaker(engine)
    monkeypatch.setattr(db_store, "get_sessionmaker", lambda: maker)
    yield maker
    await engine.dispose()


def _finding(**overrides) -> Finding:
    base = dict(
        scope="company",
        ticker="NVDA",
        event_title="New export licensing rules",
        event_type="regulatory",
        narrative="Some narrative.",
        source_urls=["https://reuters.com/a"],
    )
    base.update(overrides)
    return Finding(**base)


async def test_save_then_load_roundtrips_run_keys(session_maker):
    events = update_covered_events([], [_finding()], run_id="run-1")

    await db_store._save_async(events, window=4)
    loaded = await db_store._load_async(4)

    assert len(loaded) == 1
    assert loaded[0].first_covered_run == "run-1"
    assert loaded[0].last_updated_run == "run-1"
    assert loaded[0].run_seq == 1


async def test_two_consecutive_runs_do_not_resurface_the_same_event(session_maker):
    """The guarantee ADR 0010 §9 is written to protect, exercised through the
    real load/merge/save cycle rather than the pure merge helper alone."""
    first = update_covered_events(await db_store._load_async(4), [_finding()], run_id="run-1")
    await db_store._save_async(first, window=4)

    # Run 2 retrieves the same event again.
    second = update_covered_events(await db_store._load_async(4), [_finding()], run_id="run-2")
    await db_store._save_async(second, window=4)

    async with session_maker() as session:
        rows = (await session.execute(select(CoveredEventRow))).scalars().all()

    assert len(rows) == 1  # one event, not two -- de-dup survived the re-key
    assert rows[0].first_covered_run == "run-1"
    assert rows[0].last_updated_run == "run-2"


async def test_save_updates_a_matched_row_in_place_rather_than_duplicating(session_maker):
    """A matched event's headline is deliberately updated to the newest
    wording. `event_key` is derived from the *first* title, so the row moves
    with it instead of a second row appearing beside it."""
    first = update_covered_events(
        [], [_finding(event_type="product", event_title="NVDA launches new AI chip")], run_id="run-1"
    )
    await db_store._save_async(first, window=4)

    second = update_covered_events(
        await db_store._load_async(4),
        [_finding(event_type="product", event_title="NVDA AI chip launch expands")],
        run_id="run-2",
    )
    await db_store._save_async(second, window=4)

    async with session_maker() as session:
        rows = (await session.execute(select(CoveredEventRow))).scalars().all()

    assert len(rows) == 1
    assert rows[0].event_title == "NVDA AI chip launch expands"


async def test_load_returns_only_the_trailing_window(session_maker):
    events = []
    for seq in range(1, 7):
        events = update_covered_events(
            # Load, so each run sees only what the window exposes -- exactly
            # what `run_pipeline` does.
            await db_store._load_async(4),
            [_finding(event_type=f"type-{seq}", event_title=f"Event {seq}")],
            run_id=f"run-{seq}",
        )
        await db_store._save_async(events, window=4)

    loaded = await db_store._load_async(4)

    assert {e.event_title for e in loaded} == {f"Event {seq}" for seq in range(3, 7)}


async def test_rows_beyond_the_window_are_pruned_from_the_table(session_maker):
    """Not merely hidden at read time: a store that only ever grows is what
    the old 26-week retention cutoff existed to prevent, and the window has
    to keep doing that job."""
    for seq in range(1, 8):
        events = update_covered_events(
            await db_store._load_async(4),
            [_finding(event_type=f"type-{seq}", event_title=f"Event {seq}")],
            run_id=f"run-{seq}",
        )
        await db_store._save_async(events, window=4)

    async with session_maker() as session:
        rows = (await session.execute(select(CoveredEventRow))).scalars().all()

    assert len(rows) == 4


async def test_save_does_not_delete_rows_the_caller_never_loaded(session_maker):
    """The old save was `delete(all)` + re-insert. Under a windowed load that
    would let one run's in-memory view silently truncate the table."""
    await db_store._save_async(
        update_covered_events([], [_finding(event_title="Older event")], run_id="run-1"),
        window=4,
    )
    # A caller that loaded nothing at all still must not wipe the table.
    await db_store._save_async(
        update_covered_events(
            [], [_finding(event_type="product", event_title="Newer event")], run_id="run-2"
        ),
        window=4,
    )

    async with session_maker() as session:
        rows = (await session.execute(select(CoveredEventRow))).scalars().all()

    assert {r.event_title for r in rows} == {"Older event", "Newer event"}


async def test_load_on_an_empty_table_returns_empty(session_maker):
    assert await db_store._load_async(4) == []
