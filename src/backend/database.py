"""SQLAlchemy 2.0 async engine + session factory.

The engine is created lazily on first use so importing this module is cheap
(important for tooling like ``ruff``, ``mypy``, and FastAPI's startup hooks).

Usage in routers::

    from fastapi import Depends
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.database import get_db

    @router.get("/")
    async def list_things(db: AsyncSession = Depends(get_db)) -> ...:
        result = await db.execute(select(Thing))
        return result.scalars().all()

Pooling notes (asyncpg + Supabase Supavisor):
- We use the **session pooler** (port 5432) by default, which supports prepared
  statements — no asyncpg quirks needed.
- If you switch ``DATABASE_URL`` to the transaction pooler (port 6543), append
  ``?prepared_statement_cache_size=0`` to disable prepared statements.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from backend.config import get_settings


class Base(DeclarativeBase):
    """Declarative base class for all ORM models.

    Models inherit from this so :func:`Base.metadata` can be used by tests
    that need to create tables in a throwaway database. In production we do
    **not** run ``Base.metadata.create_all`` — schema is owned by Supabase
    (see ``sql/schema.sql``).
    """


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_engine() -> AsyncEngine:
    """Construct the async engine from current settings."""
    settings = get_settings()
    url = settings.database_url.get_secret_value()

    connect_args: dict[str, Any] = {}
    if "prepared_statement_cache_size=0" in url:
        # Belt-and-braces for transaction-pooler mode.
        connect_args["statement_cache_size"] = 0

    return create_async_engine(
        url,
        echo=settings.app_env.value == "development" and settings.log_level == "debug",
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        connect_args=connect_args,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, building it on first use."""
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields an :class:`AsyncSession`.

    The session is committed on success and rolled back on any exception.
    """
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Dispose the engine — call from FastAPI's shutdown lifespan."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
