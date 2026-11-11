import pytest
from httpx import ASGITransport, AsyncClient

from app.core.db import engine
from app.main import app
from app.models.entities import Base


async def _recreate_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
async def clean_database():
    # asyncpg connections are bound to the event loop that created them, and pytest-asyncio
    # gives every test a fresh loop: without dispose the second PR test talks to a closed loop
    # ("Event loop is closed"). SQLite tolerated the stale pool; PostgreSQL does not.
    await engine.dispose()
    await _recreate_schema()
    yield
    await engine.dispose()


@pytest.fixture
async def client():
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
    ):
        yield ac
