from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from techinves import __version__
from techinves.api import repositories as repo
from techinves.api.deps import DbSession
from techinves.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(DbSession)) -> HealthResponse:
    return await repo.get_health(session, version=__version__)
