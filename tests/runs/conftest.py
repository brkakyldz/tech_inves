from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from techinves.db.models import Base
from techinves.db.session import make_engine


@pytest_asyncio.fixture
async def session_maker(tmp_path):
    """An empty database with the real schema -- including the unique partial
    index that *is* the in-flight lock, since it is declared on
    `RunRow.__table_args__` and therefore created by `create_all`. Tests here
    exercise the lock, so a fixture that quietly omitted it would assert
    nothing.

    A sqlite **file** with the app's own engine settings (NullPool), not the
    `:memory:` + `StaticPool` pair the API tests use. That pair hands every
    session the same single DBAPI connection, which silently merges
    concurrent transactions into one: the losing insert's rollback then takes
    the winner's committed row with it, and the concurrency test measures the
    fixture instead of the lock. One connection per session is what makes
    "two concurrent triggers" mean anything here.
    """
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'runs.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
