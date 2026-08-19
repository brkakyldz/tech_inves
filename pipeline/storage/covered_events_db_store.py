"""DB-backed `covered_events` store (Faz 5,
reports/research/REPORTS_AND_PIPELINE_INTEGRATION_PLAN.md §3/§5): the production
default for `pipeline/run.py`. The JSON-file store in
`covered_events_store.py` is kept for local/dev use and is still what
`run_pipeline()` uses when a `covered_events_path` is passed explicitly (e.g.
in tests); both carry the identical fields and enforce the identical window,
via the shared helpers in that module.

**Trailing window (ADR 0010 §9, plan §9 Q1).** Loading returns only the
events from the last `COVERED_EVENTS_TRAILING_RUNS` runs, not all history.
Saving therefore cannot be `delete(everything)` + re-insert, as it was while
the key was the ISO week: that would discard every row outside the window the
caller happened to be holding, so a load/save cycle would silently truncate
the table to whatever one run had in memory. Rows are upserted by
`event_key` instead, and only rows that have fallen out of the window are
deleted.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, func, select

from pipeline.config import COVERED_EVENTS_TRAILING_RUNS
from pipeline.schemas import CoveredEvent
from techinves.db.models import CoveredEventRow
from techinves.db.session import get_sessionmaker


def _to_event(row: CoveredEventRow) -> CoveredEvent:
    return CoveredEvent(
        event_key=row.event_key,
        scope=row.scope,
        company=row.ticker,
        topic=row.topic,
        event_type=row.event_type,
        event_title=row.event_title,
        first_covered_run=row.first_covered_run,
        last_updated_run=row.last_updated_run,
        run_seq=row.run_seq,
        source_urls=row.source_urls,
    )


async def _load_async(window: int) -> list[CoveredEvent]:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        newest = (
            await session.execute(select(func.max(CoveredEventRow.run_seq)))
        ).scalar_one_or_none()
        if newest is None:
            return []
        stmt = select(CoveredEventRow).where(CoveredEventRow.run_seq >= newest - window + 1)
        rows = (await session.execute(stmt)).scalars().all()
        return [_to_event(row) for row in rows]


async def _save_async(events: list[CoveredEvent], *, window: int) -> None:
    session_maker = get_sessionmaker()
    async with session_maker() as session:
        existing = {
            row.event_key: row
            for row in (await session.execute(select(CoveredEventRow))).scalars().all()
        }
        for e in events:
            row = existing.get(e.event_key)
            if row is None:
                session.add(
                    CoveredEventRow(
                        event_key=e.event_key,
                        scope=e.scope,
                        ticker=e.company,
                        topic=e.topic,
                        event_type=e.event_type,
                        event_title=e.event_title,
                        first_covered_run=e.first_covered_run,
                        last_updated_run=e.last_updated_run,
                        run_seq=e.run_seq,
                        source_urls=e.source_urls,
                    )
                )
            else:
                # A matched event keeps its identity and its first-covered
                # run; only the evolving fields move.
                row.event_title = e.event_title
                row.last_updated_run = e.last_updated_run
                row.run_seq = e.run_seq
                row.source_urls = e.source_urls
        await session.flush()

        # Prune beyond the window. Computed from the table rather than from
        # `events` so rows this caller never loaded (they were already out of
        # the window) are dropped too, instead of lingering forever.
        newest = (await session.execute(select(func.max(CoveredEventRow.run_seq)))).scalar_one_or_none()
        if newest is not None:
            await session.execute(
                delete(CoveredEventRow).where(CoveredEventRow.run_seq < newest - window + 1)
            )
        await session.commit()


def load_covered_events_db(*, window: int = COVERED_EVENTS_TRAILING_RUNS) -> list[CoveredEvent]:
    return asyncio.run(_load_async(window))


def save_covered_events_db(
    events: list[CoveredEvent], *, window: int = COVERED_EVENTS_TRAILING_RUNS
) -> None:
    asyncio.run(_save_async(events, window=window))
