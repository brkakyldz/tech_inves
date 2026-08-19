from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from techinves.api.deps import DbSession
from techinves.api.main import create_app
from techinves.api.seed_mock import seed
from techinves.db.session import get_sessionmaker


@pytest_asyncio.fixture
async def seeded_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    await seed(engine, reset=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def empty_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def client(seeded_engine):
    app = create_app()
    session_maker = get_sessionmaker(seeded_engine)

    async def _override_get_session():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[DbSession] = _override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
