from __future__ import annotations

import pytest


async def test_list_companies_default_sort_is_composite_desc(client):
    r = await client.get("/v1/companies")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 40
    assert len(body["items"]) == 40  # default pageSize (50) > total
    scores = [item["compositeScore"] for item in body["items"]]
    assert scores == sorted(scores, reverse=True)


async def test_list_companies_pagination(client):
    r = await client.get("/v1/companies", params={"pageSize": 5, "page": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 2
    assert body["pageSize"] == 5
    assert len(body["items"]) == 5
    assert body["total"] == 40


async def test_list_companies_filter_by_cohort(client):
    r = await client.get("/v1/companies", params={"cohort": "A"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] > 0
    assert all(item["cohort"] == "A" for item in body["items"])


async def test_list_companies_filter_by_band(client):
    r = await client.get("/v1/companies", params={"band": "Strong"})
    assert r.status_code == 200
    body = r.json()
    assert all(item["band"] == "Strong" for item in body["items"])


async def test_list_companies_search(client):
    r = await client.get("/v1/companies", params={"search": "microsoft"})
    assert r.status_code == 200
    body = r.json()
    assert any(item["ticker"] == "MSFT" for item in body["items"])


async def test_list_companies_invalid_sort_is_422(client):
    r = await client.get("/v1/companies", params={"sort": "nonsense"})
    assert r.status_code == 422


async def test_list_companies_sort_ascending(client):
    r = await client.get("/v1/companies", params={"order": "asc"})
    scores = [item["compositeScore"] for item in r.json()["items"]]
    assert scores == sorted(scores)


async def test_get_company_detail(client):
    r = await client.get("/v1/companies/MSFT")
    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "MSFT"
    assert body["companyName"]
    assert "risk" in body
    assert body["history"] is None


async def test_get_company_detail_categories_expose_per_metric_layer(client):
    """R26: each category's `metrics` (raw value, cohort percentile,
    weight used) must be present in the API response, not dropped at this
    boundary the way it previously was."""
    r = await client.get("/v1/companies/MSFT")
    body = r.json()
    assert body["categories"], "expected at least one category"
    for category in body["categories"]:
        assert "metrics" in category
        assert isinstance(category["metrics"], list)


async def test_get_company_detail_is_case_insensitive(client):
    r = await client.get("/v1/companies/msft")
    assert r.status_code == 200
    assert r.json()["ticker"] == "MSFT"


async def test_get_company_detail_with_history_embedded(client):
    r = await client.get("/v1/companies/MSFT", params={"include": "history"})
    body = r.json()
    assert isinstance(body["history"], list)
    assert len(body["history"]) > 0


async def test_get_company_detail_unknown_ticker_is_404(client):
    r = await client.get("/v1/companies/NOPE")
    assert r.status_code == 404


async def test_get_company_history(client):
    """One point per run, oldest first (ADR 0010 §2 -- was one per week)."""
    r = await client.get("/v1/companies/MSFT/history")
    assert r.status_code == 200
    points = r.json()
    assert len(points) > 0
    assert all(p["runId"] for p in points)
    periods = [p["period"] for p in points]
    assert periods == sorted(periods)  # ascending, oldest first


async def test_get_company_history_unknown_ticker_is_404(client):
    r = await client.get("/v1/companies/NOPE/history")
    assert r.status_code == 404


async def test_get_company_history_of_existing_company_with_no_current_score_is_not_404(
    client, seeded_engine
):
    """The existence check used to reuse get_company_detail, which returns
    None both for an unknown ticker AND for a company that exists but has no
    current score -- conflating "not found" with "no current score"."""
    from sqlalchemy import select

    from techinves.db.models import CompanyRow
    from techinves.db.session import get_sessionmaker

    session_maker = get_sessionmaker(seeded_engine)
    async with session_maker() as session:
        company = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "MSFT"))).scalar_one()
        company.current_score_id = None
        session.add(company)
        await session.commit()

    r = await client.get("/v1/companies/MSFT/history")
    assert r.status_code == 200


async def test_list_companies_total_matches_category_sort_join(client, seeded_engine):
    """`total` must reflect the same join as the paginated query: sorting by a
    category inner-joins CategoryScoreRow, which drops any company with no row
    for that category. The count used to be computed before that join, so it
    overcounted relative to what the page actually returned."""
    from sqlalchemy import delete, select

    from techinves.db.models import CategoryScoreRow, CompanyRow, ScoreHistoryRow
    from techinves.db.session import get_sessionmaker

    session_maker = get_sessionmaker(seeded_engine)
    async with session_maker() as session:
        company = (await session.execute(select(CompanyRow).where(CompanyRow.ticker == "MSFT"))).scalar_one()
        await session.execute(
            delete(CategoryScoreRow).where(
                CategoryScoreRow.score_history_id == company.current_score_id,
                CategoryScoreRow.category_name == "quality",
            )
        )
        await session.commit()

    r = await client.get("/v1/companies", params={"sort": "quality", "pageSize": 200})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == len(body["items"])
    assert all(item["ticker"] != "MSFT" for item in body["items"])


async def test_category_sort_puts_unscored_companies_last_in_both_directions(
    client, seeded_engine
):
    """`category_scores.score` is nullable -- it means "no metric in this
    category was computable", not "scored zero" -- and a bare ORDER BY puts
    those NULLs in opposite places on the two supported backends: SQLite
    sorts NULL smallest (last on DESC, **first** on ASC), PostgreSQL defaults
    to NULLS FIRST on DESC. Same query, same data, inverted first page.

    The ordering is pinned explicitly instead. Only the ASC half of this is
    observable on SQLite, and that is exactly the half that used to be wrong
    here -- a page of unscored companies ahead of every real score.
    """
    from sqlalchemy import select, update

    from techinves.db.models import CategoryScoreRow, CompanyRow
    from techinves.db.session import get_sessionmaker

    session_maker = get_sessionmaker(seeded_engine)
    async with session_maker() as session:
        company = (
            await session.execute(select(CompanyRow).where(CompanyRow.ticker == "MSFT"))
        ).scalar_one()
        await session.execute(
            update(CategoryScoreRow)
            .where(
                CategoryScoreRow.score_history_id == company.current_score_id,
                CategoryScoreRow.category_name == "quality",
            )
            .values(score=None)
        )
        await session.commit()

    for order in ("asc", "desc"):
        r = await client.get(
            "/v1/companies", params={"sort": "quality", "order": order, "pageSize": 200}
        )
        assert r.status_code == 200
        tickers = [item["ticker"] for item in r.json()["items"]]
        assert tickers[-1] == "MSFT", f"unscored company is not last on order={order}"


async def test_list_companies_search_escapes_like_wildcards(client):
    r = await client.get("/v1/companies", params={"search": "%"})
    assert r.status_code == 200
    # a literal "%" must not match every company via an unescaped LIKE wildcard
    assert r.json()["total"] == 0


async def test_highlights_default_limit_five(client):
    r = await client.get("/v1/scores/highlights")
    assert r.status_code == 200
    assert len(r.json()) == 5


async def test_highlights_custom_limit(client):
    r = await client.get("/v1/scores/highlights", params={"limit": 2})
    assert len(r.json()) == 2
