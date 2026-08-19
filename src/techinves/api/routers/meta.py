from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from techinves.api import repositories as repo
from techinves.api.deps import DbSession
from techinves.api.schemas import MetaResponse

router = APIRouter(prefix="/v1", tags=["meta"])


@router.get("/meta", response_model=MetaResponse)
async def get_meta(session: AsyncSession = Depends(DbSession)) -> MetaResponse:
    return await repo.get_meta(session)
