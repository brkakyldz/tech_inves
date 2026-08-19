from __future__ import annotations


async def test_list_reports_returns_every_report(client):
    """There is no publication state any more (ADR 0010 §5): every stored
    report is listed, newest-written first."""
    r = await client.get("/v1/reports")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 5
    assert all(item["runId"] for item in body["items"])
    created = [item["createdAt"] for item in body["items"]]
    assert created == sorted(created, reverse=True)


async def test_list_reports_pagination(client):
    r = await client.get("/v1/reports", params={"pageSize": 2, "page": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 2
    assert body["pageSize"] == 2
    assert len(body["items"]) == 2


async def test_get_latest_report(client):
    r = await client.get("/v1/reports/latest")
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "2026-08-04-yari-iletken-kohortu-ayrisiyor"
    assert body["highlightedTickers"] == ["NVDA", "MSFT", "INTC"]


async def test_get_report_by_slug(client):
    r = await client.get("/v1/reports/2026-07-28-bulut-guvenlik-marjlari")
    assert r.status_code == 200
    body = r.json()
    assert body["title"]
    assert isinstance(body["sections"], list)
    assert len(body["sections"]) >= 1
    assert body["sections"][0]["bodyMarkdown"]


async def test_get_report_unknown_slug_is_404(client):
    r = await client.get("/v1/reports/nope")
    assert r.status_code == 404


async def test_get_latest_report_404_when_there_are_no_reports(client, empty_engine):
    from techinves.api.deps import DbSession
    from techinves.api.main import create_app
    from techinves.db.models import Base
    from techinves.db.session import get_sessionmaker
    from httpx import ASGITransport, AsyncClient

    async with empty_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    app = create_app()
    session_maker = get_sessionmaker(empty_engine)

    async def _override_get_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[DbSession] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        r = await ac.get("/v1/reports/latest")
        assert r.status_code == 404


async def test_get_latest_report_breaks_a_created_at_tie_by_newest_row(empty_engine):
    """Two rows can share a `created_at` (a week re-run inside the same
    second, or seeded fixtures stamped from their week). Without the id
    tie-break, "latest" returned whichever row the DB happened to yield
    first."""
    from datetime import date

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from techinves.api._time import now_naive_utc
    from techinves.api.repositories import get_latest_report
    from techinves.db.models import Base, ReportRow, RunRow

    async with empty_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(empty_engine, expire_on_commit=False)
    created_at = now_naive_utc()
    async with session_maker() as session:
        # Two runs landing in the same second -- the normal case for a tool
        # with a button, and the reason the id tie-break exists.
        for run_id in ("run-a", "run-b"):
            session.add(
                RunRow(
                    run_id=run_id,
                    trigger_type="report",
                    status="succeeded",
                    created_at=created_at,
                )
            )
            session.add(
                ReportRow(
                    slug=f"run-{run_id}",
                    run_id=run_id,
                    title=f"TechInves Weekly {run_id}",
                    summary="s",
                    created_at=created_at,
                )
            )
        await session.commit()

    async with session_maker() as session:
        latest = await get_latest_report(session)

    assert latest is not None
    assert latest.slug == "run-run-b"  # the newer row, deterministically


async def test_seeded_reports_are_visible(empty_engine):
    """The seed used to write drafts, which the API then filtered out. With
    publication state gone, a seeded report is a visible report -- this is
    what Faz 6's keyless demo mode depends on."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from techinves.api.repositories import get_latest_report, list_reports
    from techinves.api.seed_mock import seed

    await seed(empty_engine, reset=True)

    session_maker = async_sessionmaker(empty_engine, expire_on_commit=False)
    async with session_maker() as session:
        items, total = await list_reports(session)
        latest = await get_latest_report(session)

    assert total == 5
    assert len(items) == 5
    assert latest is not None
    assert latest.slug == "2026-08-04-yari-iletken-kohortu-ayrisiyor"


async def test_report_detail_exposes_the_verdict_and_its_violations(empty_engine):
    """ADR 0010 §6 / Faz 5.3. The banner is rendered from the report API, so
    a verdict and violations that never leave the database cannot be named to
    a reader. This is the wire contract the front-end banner reads."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from techinves.api._time import now_naive_utc
    from techinves.api.repositories import get_report_by_slug, list_reports
    from techinves.db.models import Base, ReportRow, RunRow

    async with empty_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    violations = [
        {
            "severity": "compliance_hard",
            "category": "number_leak",
            "message": "number not found in scores/financials: '41.7%'",
            "section": "NVDA",
        },
        {
            "severity": "structural_hard",
            "category": "completeness",
            "message": "watchlist ticker never mentioned: WDAY",
            "section": None,
        },
    ]

    session_maker = async_sessionmaker(empty_engine, expire_on_commit=False)
    async with session_maker() as session:
        session.add(
            RunRow(
                run_id="run-blocked",
                trigger_type="report",
                status="succeeded",
                created_at=now_naive_utc(),
            )
        )
        session.add(
            ReportRow(
                slug="run-run-blocked",
                run_id="run-blocked",
                title="TechInves Weekly",
                summary="s",
                created_at=now_naive_utc(),
                verifier_verdict="block",
                is_partial=True,
                verifier_violations=violations,
            )
        )
        await session.commit()

    async with session_maker() as session:
        detail = await get_report_by_slug(session, "run-run-blocked")
        items, _ = await list_reports(session)

    assert detail is not None
    assert detail.verifier_verdict == "block"
    assert detail.is_partial is True
    assert [v.model_dump() for v in detail.verifier_violations] == violations

    # The summary carries the verdict too, so the archive can mark a blocked
    # report before the reader opens it.
    assert items[0].verifier_verdict == "block"
    assert items[0].is_partial is True


async def test_report_without_a_verdict_reports_null_not_pass(empty_engine):
    """A row that predates the verdict columns must come back as `null`, not
    as an empty-but-clean-looking result: the front-end renders `null` as
    *unverified*, and any coercion here would turn an unchecked report into a
    clean one."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from techinves.api._time import now_naive_utc
    from techinves.api.repositories import get_report_by_slug
    from techinves.db.models import Base, ReportRow, RunRow

    async with empty_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(empty_engine, expire_on_commit=False)
    async with session_maker() as session:
        session.add(
            RunRow(
                run_id="run-old",
                trigger_type="report",
                status="succeeded",
                created_at=now_naive_utc(),
            )
        )
        session.add(
            ReportRow(
                slug="run-run-old",
                run_id="run-old",
                title="TechInves Weekly",
                summary="s",
                created_at=now_naive_utc(),
            )
        )
        await session.commit()

    async with session_maker() as session:
        detail = await get_report_by_slug(session, "run-run-old")

    assert detail is not None
    assert detail.verifier_verdict is None
    assert detail.verifier_violations is None


async def test_malformed_stored_violations_degrade_instead_of_500ing(empty_engine):
    """The column is JSON written by whatever pipeline revision produced the
    row. A malformed entry must cost detail, never the whole response: a 500
    here would hide the report *and* its verdict, which is the one outcome
    ADR 0010 §6 rules out."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from techinves.api._time import now_naive_utc
    from techinves.api.repositories import get_report_by_slug
    from techinves.db.models import Base, ReportRow, RunRow

    async with empty_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_maker = async_sessionmaker(empty_engine, expire_on_commit=False)
    async with session_maker() as session:
        session.add(
            RunRow(
                run_id="run-junk",
                trigger_type="report",
                status="succeeded",
                created_at=now_naive_utc(),
            )
        )
        session.add(
            ReportRow(
                slug="run-run-junk",
                run_id="run-junk",
                title="TechInves Weekly",
                summary="s",
                created_at=now_naive_utc(),
                verifier_verdict="block",
                verifier_violations=[
                    "a bare string, not an object",
                    {"severity": "compliance_hard"},  # no category, no message
                ],
            )
        )
        await session.commit()

    async with session_maker() as session:
        detail = await get_report_by_slug(session, "run-run-junk")

    assert detail is not None
    assert detail.verifier_verdict == "block"
    # The unusable entry is dropped; the salvageable one survives with
    # placeholders, because "something is wrong here" is the part the reader
    # must not lose.
    assert len(detail.verifier_violations) == 1
    assert detail.verifier_violations[0].severity == "compliance_hard"
    assert detail.verifier_violations[0].message
