"""The no-environment boot test -- Faz 6's actual deliverable (plan §7.4,
`reports/plans/2026-08-18_on-demand-transformation.md`): the application
boots and serves a populated, navigable site with no API-key environment
variables set at all.

"No environment variables" here means every variable this application's
config layer reads to decide whether it can run live -- the five that
matter (`FMP_API_KEY`, `OPENAI_API_KEY`, `TAVILY_API_KEY`,
`FRED_API_KEY`, `EXA_API_KEY`), not the process's entire environment
(`PATH` etc. clearing would break the interpreter, not this application).

This drives the real `techinves.api.main.lifespan` -- including startup
reconciliation and the seed-on-empty step -- through
`app.router.lifespan_context(app)`, the same way `tests/runs/test_app_startup.py`
does, rather than the shortcut most of `tests/api/**` uses (building a
`RunService` directly and skipping the lifespan). `DATABASE_URL` is pointed
at an isolated on-disk sqlite file so `api/main.py`'s own
`make_engine()` call (used by the seed-on-empty step) and this test's
`RunService`/`DbSession` override all agree on the same database.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from techinves.api import main as main_module
from techinves.api.deps import DbSession
from techinves.api.routers import runs as runs_router
from techinves.db.models import Base, CompanyRow
from techinves.db.session import get_sessionmaker, make_engine
from techinves.runs.service import RunService

ALL_KEY_VARS = (
    "FMP_API_KEY",
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "FRED_API_KEY",
    "EXA_API_KEY",
)


@pytest_asyncio.fixture
async def keyless_boot(tmp_path, monkeypatch):
    """Boots the real app against an empty, schema-only database with no
    key env vars set, and tears it down after. Yields the connected client."""
    for name in ALL_KEY_VARS:
        monkeypatch.delenv(name, raising=False)

    db_path = tmp_path / "keyless.db"
    db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    # `api/main.py`'s seed-on-empty step calls `make_engine()` with no
    # explicit URL, reading `DATABASE_URL` fresh -- setting it here is what
    # keeps that call pointed at this test's isolated file instead of the
    # real dev.db.
    monkeypatch.setenv("DATABASE_URL", db_url)

    engine = make_engine(db_url)
    async with engine.begin() as conn:
        # Schema only -- no data. A real deployment gets here via
        # `alembic upgrade head`; reconciliation already assumes the schema
        # exists (it queries `runs`), so seed-on-empty does not create it.
        await conn.run_sync(Base.metadata.create_all)

    session_maker = get_sessionmaker(engine)
    service = RunService(session_maker=session_maker)
    monkeypatch.setattr(main_module, "get_run_service", lambda: service)

    app = main_module.create_app()

    async def _override_get_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[DbSession] = _override_get_session
    app.dependency_overrides[runs_router.get_run_service_dep] = lambda: service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with app.router.lifespan_context(app):
            yield client, session_maker

    await engine.dispose()


async def test_app_boots_and_serves_a_populated_site_with_no_api_keys_set(keyless_boot):
    client, session_maker = keyless_boot

    health = await client.get("/health")
    assert health.status_code == 200

    meta = await client.get("/v1/meta")
    assert meta.status_code == 200
    meta_body = meta.json()
    assert meta_body["mode"] == "demo"
    assert meta_body["missingKeys"] == {
        "scores": "FMP_API_KEY",
        "report": "FMP_API_KEY",
        "company": "FMP_API_KEY",
    }

    # Seed-on-empty: an empty database came up populated (ADR 0010 §7) --
    # not zero companies, and the seeded fixture reports are reachable.
    reports = await client.get("/v1/reports")
    assert reports.status_code == 200
    report_items = reports.json()["items"]
    assert len(report_items) == 5

    companies = await client.get("/v1/companies")
    assert companies.status_code == 200
    assert len(companies.json()["items"]) > 0

    async with session_maker() as session:
        count = (await session.execute(select(CompanyRow))).scalars().all()
    assert len(count) > 0


async def test_every_trigger_refuses_naming_the_missing_key(keyless_boot):
    client, _ = keyless_boot

    for trigger_type, ticker in (("scores", None), ("report", None), ("company", "NVDA")):
        body = {"triggerType": trigger_type}
        if ticker:
            body["ticker"] = ticker
        r = await client.post("/v1/runs", json=body)
        assert r.status_code == 503, (trigger_type, r.text)
        detail = r.json()["detail"]
        assert detail["code"] == "MissingApiKey"
        assert detail["missing_key"] == "FMP_API_KEY"


async def test_seed_on_empty_is_a_noop_once_the_database_has_data(keyless_boot):
    """A second startup against the same (now-populated) database does not
    re-seed or duplicate rows -- `seed_if_empty`'s emptiness check, exercised
    through the real lifespan rather than unit-tested in isolation."""
    client, session_maker = keyless_boot
    reports_first = (await client.get("/v1/reports")).json()["items"]

    # Re-run the lifespan prologue directly against the same session_maker,
    # simulating a second process boot against a database that already has
    # this run's seeded data.
    from techinves.api import seed_mock

    async with session_maker() as session:
        pass  # ensures the fixture's engine/session_maker still work
    engine = make_engine()
    try:
        seeded_again = await seed_mock.seed_if_empty(engine)
    finally:
        await engine.dispose()
    assert seeded_again == 0

    reports_second = (await client.get("/v1/reports")).json()["items"]
    assert len(reports_second) == len(reports_first)


async def test_demo_mode_serves_both_delta_states(keyless_boot):
    """Faz 7a against the Faz 6 demo path.

    The seeded fixtures are the realistic multi-run case, and they contain
    both states on purpose: 33 tickers carry six quarters of backfilled
    history and so have a comparable predecessor, while seven were added
    with an empty history array and exist only in the current run. The demo
    therefore has to render a real delta *and* a first-run state without
    either one degrading into the other.
    """
    client, _ = keyless_boot

    items = (await client.get("/v1/companies", params={"pageSize": 200})).json()["items"]
    assert items

    available = [i for i in items if i["delta"]["delta"] is not None]
    first_run = [i for i in items if i["delta"]["unavailableReason"] == "first_run"]

    assert available, "the seeded multi-run history should produce real deltas"
    assert first_run, "the history-less seeded tickers should report first_run"

    for item in items:
        d = item["delta"]
        # The contract: exactly one of the two is set, on every single row.
        assert (d["delta"] is None) != (d["unavailableReason"] is None), item["ticker"]
    for item in first_run:
        assert item["delta"]["previousRunId"] is None
        assert item["delta"]["previousComposite"] is None
