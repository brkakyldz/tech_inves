from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from techinves.api import repositories as repo
from techinves.api.deps import DbSession
from techinves.api.schemas import ReportDetailOut, ReportListResponse, ReportSummaryOut

router = APIRouter(prefix="/v1/reports", tags=["reports"])


@router.get("", response_model=ReportListResponse)
async def list_reports(
    session: AsyncSession = Depends(DbSession),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100, alias="pageSize"),
) -> ReportListResponse:
    items, total = await repo.list_reports(session, page=page, page_size=page_size)
    return ReportListResponse(items=items, page=page, page_size=page_size, total=total)


@router.get("/latest", response_model=ReportSummaryOut)
async def get_latest_report(session: AsyncSession = Depends(DbSession)) -> ReportSummaryOut:
    report = await repo.get_latest_report(session)
    if report is None:
        raise HTTPException(404, "no reports yet")
    return report


@router.get("/{slug}", response_model=ReportDetailOut)
async def get_report(slug: str, session: AsyncSession = Depends(DbSession)) -> ReportDetailOut:
    report = await repo.get_report_by_slug(session, slug)
    if report is None:
        raise HTTPException(404, f"unknown report: {slug}")
    return report
