from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from techinves.api import repositories as repo
from techinves.api.deps import DbSession
from techinves.api.schemas import CompanyDetail, CompanyListItem, CompanyListResponse, ScoreHistoryPointOut

router = APIRouter(prefix="/v1", tags=["companies"])

VALID_SORTS = {"composite", "ticker", "sectorPercentile", "valuation", "growth", "quality", "financial_health"}
VALID_BANDS = {"Strong", "Good", "Moderate", "Weak", "Very Weak"}


@router.get("/companies", response_model=CompanyListResponse)
async def list_companies(
    session: AsyncSession = Depends(DbSession),
    cohort: list[str] | None = Query(default=None),
    band: str | None = Query(default=None),
    min_composite: float | None = Query(default=None, alias="minComposite"),
    max_composite: float | None = Query(default=None, alias="maxComposite"),
    min_coverage: float | None = Query(default=None, alias="minCoverage"),
    search: str | None = Query(default=None),
    sort: str = Query(default="composite"),
    order: str = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200, alias="pageSize"),
    run_id: str | None = Query(default=None, alias="runId"),
) -> CompanyListResponse:
    if sort not in VALID_SORTS:
        raise HTTPException(422, f"invalid sort: {sort}")
    if band is not None and band not in VALID_BANDS:
        raise HTTPException(422, f"invalid band: {band}")

    items, total, resolved_run = await repo.list_companies(
        session,
        cohorts=cohort,
        band=band,
        min_composite=min_composite,
        max_composite=max_composite,
        min_coverage=min_coverage,
        search=search,
        sort=sort,
        order=order,
        page=page,
        page_size=page_size,
        run_id=run_id,
    )
    return CompanyListResponse(items=items, page=page, page_size=page_size, total=total, run_id=resolved_run)


@router.get("/companies/{ticker}", response_model=CompanyDetail)
async def get_company(
    ticker: str,
    session: AsyncSession = Depends(DbSession),
    include: str | None = Query(default=None),
) -> CompanyDetail:
    detail = await repo.get_company_detail(session, ticker, include_history=include == "history")
    if detail is None:
        raise HTTPException(404, f"unknown ticker: {ticker}")
    return detail


@router.get("/companies/{ticker}/history", response_model=list[ScoreHistoryPointOut])
async def get_company_history(
    ticker: str,
    session: AsyncSession = Depends(DbSession),
    limit: int = Query(default=52, ge=1, le=500),
) -> list[ScoreHistoryPointOut]:
    """History is one point per run (ADR 0010 §2). The `from`/`to` date
    filters went with `week_of`: they narrowed a calendar the history is no
    longer keyed by, and `limit` already bounds the response."""
    exists = await repo.company_exists(session, ticker)
    if not exists:
        raise HTTPException(404, f"unknown ticker: {ticker}")
    return await repo.get_score_history(session, ticker, limit=limit)


@router.get("/scores/highlights", response_model=list[CompanyListItem])
async def get_highlights(
    session: AsyncSession = Depends(DbSession),
    limit: int = Query(default=5, ge=1, le=42),
) -> list[CompanyListItem]:
    return await repo.get_highlights(session, limit=limit)
