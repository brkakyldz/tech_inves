"""Async engine/session setup.

`DATABASE_URL` selects the backend:
- unset / local dev / tests -> `sqlite+aiosqlite:///./dev.db` (or `:memory:` in tests)
- production -> `postgresql+asyncpg://...` (Neon), per
  `reports/research/BACKEND_IMPLEMENTATION_PLAN.md` Section 8.

sqlite is intentionally supported as a first-class target (not just a test
shim): it lets Faz 0 run and be reviewed with zero external services before
a Neon project exists.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

# Anchored to the repo, not the working directory. `sqlite:///./dev.db` is
# CWD-relative, so the API served from one directory and the pipeline run
# from another silently used two different database files -- the pipeline
# would write a report that the API could never see, which looks exactly
# like "the database is out of sync". A stray second dev.db under
# .claude/worktrees/ is what made this concrete.
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SQLITE_PATH = _REPO_ROOT / "dev.db"
DEFAULT_SQLITE_URL = f"sqlite+aiosqlite:///{DEFAULT_SQLITE_PATH.as_posix()}"


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_SQLITE_URL)


def make_engine(url: str | None = None):
    url = url or get_database_url()
    if url.startswith("sqlite"):
        # NullPool, not the default queue pool. Faz 3's background executor
        # runs each unit of work in a worker thread, and that work drives its
        # own `asyncio.run(...)` -- so the *same* engine is used from the
        # API's event loop and from a second, short-lived loop in the worker
        # thread. A pooled aiosqlite connection carries futures bound to the
        # loop that created it, and reusing one across loops fails with
        # "attached to a different loop". NullPool opens and closes a
        # connection per checkout, so a connection is never shared between
        # loops. For a local sqlite file that costs almost nothing.
        return create_async_engine(
            url,
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
            future=True,
        )
    return create_async_engine(url, future=True)


_engine = make_engine()
_SessionLocal = async_sessionmaker(bind=_engine, expire_on_commit=False, class_=AsyncSession)


def get_sessionmaker(engine=None) -> async_sessionmaker[AsyncSession]:
    if engine is None:
        return _SessionLocal
    return async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with _SessionLocal() as session:
        yield session
