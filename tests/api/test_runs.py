"""`POST /v1/runs`, `GET /v1/runs`, `GET /v1/runs/{id}` -- Faz 4.

No API key lives in this environment (a repo guard denies them), so every
trigger here runs against a fake work registry -- the same pattern
`tests/runs/test_run_service.py` uses for `RunService` itself. What this
file adds on top is the HTTP surface: status codes, the machine-readable
refusal `code`s, pagination, the log-tail/offset protocol and its cap, and
that the rate limiter actually covers `/v1/runs*`.
"""

from __future__ import annotations

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from techinves.api import main as main_module
from techinves.api.deps import DbSession
from techinves.api.main import create_app
from techinves.api.routers import runs as runs_router
from techinves.db.session import get_sessionmaker
from techinves.runs.service import RunService, TRIGGER_TYPES


def _ok_work(ctx):
    ctx.log("fake work started")
    ctx.log("fake work finished")
    return "ok"


def _failing_work(ctx):
    raise RuntimeError("synthetic failure")


async def _make_runs_client(seeded_engine, *, work_registry=None, reconciled=True):
    app = create_app()
    session_maker = get_sessionmaker(seeded_engine)

    async def _override_get_session():
        async with session_maker() as session:
            yield session

    service = RunService(
        session_maker=session_maker,
        work_registry=work_registry if work_registry is not None else {t: _ok_work for t in TRIGGER_TYPES},
    )
    if reconciled:
        await service.startup()

    app.dependency_overrides[DbSession] = _override_get_session
    app.dependency_overrides[runs_router.get_run_service_dep] = lambda: service

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return client, service


@pytest_asyncio.fixture
async def runs_client(seeded_engine):
    client, service = await _make_runs_client(seeded_engine)
    async with client:
        yield client, service


# -- happy path: the three triggers, driven end-to-end with the work mocked --


async def test_scores_trigger_runs_end_to_end(runs_client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    client, service = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "scores"})
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    run_id = body["runId"]

    await service.wait_for(run_id)

    detail = (await client.get(f"/v1/runs/{run_id}")).json()
    assert detail["status"] == "succeeded"
    assert "fake work started" in detail["log"]


async def test_report_trigger_runs_end_to_end(runs_client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    client, service = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "report"})
    assert r.status_code == 202
    run_id = r.json()["runId"]
    await service.wait_for(run_id)
    detail = (await client.get(f"/v1/runs/{run_id}")).json()
    assert detail["status"] == "succeeded"


async def test_company_trigger_runs_end_to_end(runs_client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    client, service = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "company", "ticker": "nvda"})
    assert r.status_code == 202
    body = r.json()
    assert body["ticker"] == "NVDA"  # uppercased
    run_id = body["runId"]
    await service.wait_for(run_id)
    detail = (await client.get(f"/v1/runs/{run_id}")).json()
    assert detail["status"] == "succeeded"
    assert detail["ticker"] == "NVDA"


async def test_a_run_that_raises_lands_failed(runs_client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    client, service = runs_client
    service._work_registry = {"scores": _failing_work}
    r = await client.post("/v1/runs", json={"triggerType": "scores"})
    run_id = r.json()["runId"]
    await service.wait_for(run_id)
    detail = (await client.get(f"/v1/runs/{run_id}")).json()
    assert detail["status"] == "failed"
    assert "synthetic failure" in detail["error"]


# -- refusals, each with its status code and reason code --


async def test_unknown_trigger_type_is_422(runs_client):
    client, _ = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "nonsense"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "UnknownTriggerType"


async def test_company_trigger_without_ticker_is_422(runs_client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    client, _ = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "company"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "TickerRequired"


async def test_ticker_off_watchlist_is_422(runs_client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    client, _ = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "company", "ticker": "NOTATICKER"})
    assert r.status_code == 422
    body = r.json()["detail"]
    assert body["code"] == "UnknownTicker"
    assert body["ticker"] == "NOTATICKER"


async def test_ticker_on_a_non_company_trigger_is_422(runs_client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    client, _ = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "scores", "ticker": "NVDA"})
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "UnexpectedTicker"


async def test_missing_key_is_503_and_names_the_key(runs_client, monkeypatch):
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    client, _ = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "scores"})
    assert r.status_code == 503
    body = r.json()["detail"]
    assert body["code"] == "MissingApiKey"
    assert body["missing_key"] == "FMP_API_KEY"


async def test_optional_keys_never_block_a_run(runs_client, monkeypatch):
    """ADR 0010 §8: FRED_API_KEY/EXA_API_KEY are optional and must never
    block a trigger."""
    monkeypatch.setenv("FMP_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    client, service = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "report"})
    assert r.status_code == 202
    await service.wait_for(r.json()["runId"])


async def test_report_missing_key_names_the_first_one_missing(runs_client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    client, _ = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "report"})
    assert r.status_code == 503
    assert r.json()["detail"]["missing_key"] == "OPENAI_API_KEY"


async def test_lock_held_is_409_and_names_the_holder(seeded_engine, monkeypatch):
    """The in-flight lock (ADR 0010 §4): a second trigger of the same type
    while one is in flight is refused, not queued."""
    monkeypatch.setenv("FMP_API_KEY", "x")

    import asyncio
    import threading

    # Plain threading.Event, not asyncio.Event: the work runs in a worker
    # thread with its own event loop (RunService's design, see
    # src/techinves/runs/service.py), so an asyncio primitive created on the
    # test's loop is the wrong tool -- it would either raise or block
    # forever waiting on a loop that never runs it.
    started = threading.Event()
    release = threading.Event()

    def _blocking_work(ctx):
        started.set()
        release.wait(timeout=10)

    client, service = await _make_runs_client(seeded_engine, work_registry={"scores": _blocking_work})
    async with client:
        r1 = await client.post("/v1/runs", json={"triggerType": "scores"})
        assert r1.status_code == 202
        first_run_id = r1.json()["runId"]

        await asyncio.get_event_loop().run_in_executor(None, started.wait, 5)
        assert started.is_set()

        r2 = await client.post("/v1/runs", json={"triggerType": "scores"})
        assert r2.status_code == 409
        detail = r2.json()["detail"]
        assert detail["code"] == "RunRefused"
        assert detail["active_run_id"] == first_run_id
        assert detail["trigger_type"] == "scores"

        release.set()
        await service.wait_for(first_run_id)


async def test_not_reconciled_is_503(seeded_engine, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    client, _service = await _make_runs_client(seeded_engine, reconciled=False)
    async with client:
        r = await client.post("/v1/runs", json={"triggerType": "scores"})
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "RunNotReconciled"


async def test_unknown_run_id_is_404(runs_client):
    client, _ = runs_client
    r = await client.get("/v1/runs/does-not-exist")
    assert r.status_code == 404


# -- listing: order and pagination, matching routers/reports.py's convention --


async def test_list_runs_orders_newest_first_and_paginates(runs_client, monkeypatch):
    # `seeded_engine` (tests/api/conftest.py) already seeds some run rows so
    # reports have a run to point at -- this test only asserts on the delta
    # its own triggers add, not on an absolute total.
    monkeypatch.setenv("FMP_API_KEY", "x")
    client, service = runs_client
    before = (await client.get("/v1/runs", params={"pageSize": 1})).json()["total"]

    run_ids = []
    for _ in range(3):
        r = await client.post("/v1/runs", json={"triggerType": "scores"})
        run_id = r.json()["runId"]
        await service.wait_for(run_id)
        run_ids.append(run_id)

    r = await client.get("/v1/runs", params={"pageSize": 2, "page": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 1
    assert body["pageSize"] == 2
    assert body["total"] == before + 3
    assert len(body["items"]) == 2
    # newest-first: the last-triggered run leads.
    assert body["items"][0]["runId"] == run_ids[-1]


# -- the log-tail protocol and its cap --


async def test_log_offset_returns_only_new_content(runs_client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    client, service = runs_client
    r = await client.post("/v1/runs", json={"triggerType": "scores"})
    run_id = r.json()["runId"]
    await service.wait_for(run_id)

    first = await client.get(f"/v1/runs/{run_id}")
    full_log = first.json()["log"]
    offset = first.json()["logOffset"]
    assert offset == len(full_log)

    # Polling again from the returned offset yields nothing new -- no more
    # work happened, so the log did not grow.
    second = await client.get(f"/v1/runs/{run_id}", params={"log_offset": offset})
    assert second.json()["log"] == ""
    assert second.json()["logOffset"] == offset

    # Polling from 0 always yields the whole log.
    from_zero = await client.get(f"/v1/runs/{run_id}", params={"log_offset": 0})
    assert from_zero.json()["log"] == full_log


async def test_log_cap_truncates_once_and_offsets_stay_monotonic(seeded_engine, monkeypatch):
    """Faz 4's log cap: once the cap is reached, exactly one truncation
    notice is appended and the log stops growing -- the head is never
    rewritten, so an offset taken before the cap still reads correctly
    afterward. The cap is monkeypatched down to a small value so the test
    doesn't need thousands of real appends to exercise it."""
    monkeypatch.setenv("FMP_API_KEY", "x")
    import techinves.runs.service as service_module

    monkeypatch.setattr(service_module, "RUN_LOG_MAX_CHARS", 500)
    RUN_LOG_TRUNCATION_MARKER = service_module.RUN_LOG_TRUNCATION_MARKER

    def _chatty_work(ctx):
        line = "x" * 40
        for _ in range(40):  # 40 * ~50 chars/line (with stamp) comfortably exceeds 500
            ctx.log(line)

    client, service = await _make_runs_client(seeded_engine, work_registry={"scores": _chatty_work})
    async with client:
        r = await client.post("/v1/runs", json={"triggerType": "scores"})
        run_id = r.json()["runId"]
        await service.wait_for(run_id, timeout=30)

        first = await client.get(f"/v1/runs/{run_id}")
        log1 = first.json()["log"]
        assert RUN_LOG_TRUNCATION_MARKER in log1
        assert log1.count(RUN_LOG_TRUNCATION_MARKER) == 1
        offset1 = first.json()["logOffset"]

        # A cursor taken at the cap reads no new content, and the prefix it
        # already holds is never invalidated: fetching from 0 again returns
        # a byte-identical log.
        second = await client.get(f"/v1/runs/{run_id}", params={"log_offset": offset1})
        assert second.json()["log"] == ""

        again_from_zero = await client.get(f"/v1/runs/{run_id}")
        assert again_from_zero.json()["log"] == log1


# -- rate limiting: confirm /v1/runs* is not exempt --


async def test_post_runs_is_rate_limited(seeded_engine, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    app = create_app()
    session_maker = get_sessionmaker(seeded_engine)

    async def _override_get_session():
        async with session_maker() as session:
            yield session

    service = RunService(session_maker=session_maker, work_registry={"scores": _ok_work})
    await service.startup()
    app.dependency_overrides[DbSession] = _override_get_session
    app.dependency_overrides[runs_router.get_run_service_dep] = lambda: service

    for middleware in app.user_middleware:
        if middleware.cls is main_module.InProcessRateLimitMiddleware:
            middleware.kwargs["limit"] = 0

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/runs", json={"triggerType": "scores"})
    assert r.status_code == 429


async def test_get_runs_is_rate_limited(seeded_engine):
    app = create_app()
    session_maker = get_sessionmaker(seeded_engine)

    async def _override_get_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[DbSession] = _override_get_session

    for middleware in app.user_middleware:
        if middleware.cls is main_module.InProcessRateLimitMiddleware:
            middleware.kwargs["limit"] = 0

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/runs")
    assert r.status_code == 429
