"""The ordering Faz 3.2 calls load-bearing, asserted at the application
level: reconciliation happens during startup, before the app accepts any
request, and a failing reconciliation stops the app from starting at all.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from techinves.api import main as main_module
from techinves.api._time import now_naive_utc
from techinves.db.models import RunRow
from techinves.runs.reconcile import RESTART_ERROR, ReconciliationFailed
from techinves.runs.service import RunService


async def test_reconciliation_completes_before_the_first_request(session_maker, monkeypatch):
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
        await session.commit()

    service = RunService(session_maker=session_maker, work_registry={})
    monkeypatch.setattr(main_module, "get_run_service", lambda: service)

    observed: list[bool] = []

    app = main_module.create_app()

    @app.get("/_probe")
    async def probe():
        observed.append(service.reconciled)
        return {"ok": True}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # `async with app.router.lifespan_context` is what uvicorn runs
        # before binding the socket; ASGITransport does not run it on its
        # own, so it is driven explicitly here.
        async with app.router.lifespan_context(app):
            await client.get("/_probe")

    # The very first request already sees a reconciled service...
    assert observed == [True]
    # ...and the abandoned row was cleared during startup, not lazily on
    # first use.
    async with session_maker() as session:
        row = (
            await session.execute(select(RunRow).where(RunRow.run_id == "crashed"))
        ).scalar_one()
    assert row.status == "failed"
    assert row.error == RESTART_ERROR


async def test_a_failing_reconciliation_aborts_startup(monkeypatch):
    """An application that cannot tell whether the lock is held by live work
    or by a corpse must fail loudly rather than serve."""

    class Boom:
        def __call__(self):
            raise OSError("disk gone")

    service = RunService(session_maker=Boom(), work_registry={})
    monkeypatch.setattr(main_module, "get_run_service", lambda: service)

    app = main_module.create_app()
    with pytest.raises(ReconciliationFailed):
        async with app.router.lifespan_context(app):
            pass  # pragma: no cover - startup never completes
