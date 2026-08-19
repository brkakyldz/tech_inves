from __future__ import annotations


async def test_meta_lists_cohorts_and_bands(client):
    r = await client.get("/v1/meta")
    assert r.status_code == 200
    body = r.json()
    assert {c["code"] for c in body["cohorts"]} == {"A", "B", "C"}
    assert body["bands"] == ["Strong", "Good", "Moderate", "Weak", "Very Weak"]
    assert body["latestRunId"] is not None


async def test_meta_reports_live_mode_when_every_required_key_is_present(client, monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("TAVILY_API_KEY", "x")
    r = await client.get("/v1/meta")
    body = r.json()
    assert body["mode"] == "live"
    assert body["missingKeys"] == {}


async def test_meta_reports_demo_mode_and_names_every_gap(client, monkeypatch):
    # FRED_API_KEY/EXA_API_KEY are optional (ADR 0010 §8) and must never
    # appear in missingKeys even when absent.
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    r = await client.get("/v1/meta")
    body = r.json()
    assert body["mode"] == "demo"
    assert body["missingKeys"] == {
        "scores": "FMP_API_KEY",
        "report": "FMP_API_KEY",
        "company": "FMP_API_KEY",
    }


async def test_health_ok_after_seed(client):
    r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    # `ingestion_runs` folded into `runs` in Faz 2; the terminal status
    # vocabulary is the `runs` one.
    assert body["lastIngestionStatus"] == "succeeded"


async def test_health_degraded_with_no_ingestion(empty_engine):
    from httpx import ASGITransport, AsyncClient

    from techinves.api.deps import DbSession
    from techinves.api.main import create_app
    from techinves.db.models import Base
    from techinves.db.session import get_sessionmaker

    async with empty_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    session_maker = get_sessionmaker(empty_engine)

    async def _override_get_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[DbSession] = _override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "degraded"
