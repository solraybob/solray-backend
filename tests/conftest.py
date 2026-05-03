"""
Shared pytest fixtures for the Solray backend test suite.

We run every test against a fresh in-memory SQLite database so tests
are fast, isolated, and never touch the production schema or data.
The Postgres production database is never reachable from the test
process; if you find yourself needing real Postgres for a specific
test, mark it `@pytest.mark.slow` and gate it on an env var.
"""

import asyncio
import os
import uuid
from typing import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import the ORM models so Base.metadata.create_all sees every table.
# We import db.database first to register the models, then use Base
# directly. The module-level engine in db.database is the production
# engine; we ignore it and build our own in-memory engine here.
from db.database import (
    Base,
    User,
    UserMemory,
    Blueprint,
)


@pytest_asyncio.fixture
async def engine():
    """Per-test in-memory SQLite engine. Each test starts with a clean
    schema. Using StaticPool so the same in-memory DB is reused across
    sessions within a single test (necessary because :memory: is
    connection-scoped by default).
    """
    from sqlalchemy.pool import StaticPool
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncIterator[AsyncSession]:
    """Clean session for one test. Commits go straight to the in-memory
    DB; the whole thing is dropped when the engine fixture tears down.
    """
    Session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session


@pytest_asyncio.fixture
async def user(db) -> User:
    """A single test user with stable IDs for assertion convenience.

    Birth date / time are required by the User schema; we use Bob's
    actual chart (5 Sep 1989, Reykjavik) so any test that incidentally
    needs a real-feeling chart has one to work with.
    """
    u = User(
        id=str(uuid.uuid4()),
        email="test@solray.ai",
        password_hash="bcrypt$test",
        name="Test User",
        birth_date="1989-09-05",
        birth_time="12:00",
        birth_city="Reykjavik",
        birth_lat=64.1466,
        birth_lon=-21.9426,
        is_public=False,
        analytics_opt_out=False,
    )
    db.add(u)
    await db.commit()
    await db.refresh(u)
    return u
