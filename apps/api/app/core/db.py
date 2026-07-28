"""Async SQLAlchemy engine, declarative base, and request-scoped session
dependency.

The engine is created once at import time from ``settings.database_url``
(the same asyncpg-based URL Alembic's ``env.py`` uses), so the application
and migrations always target the same database configuration.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Declarative base class shared by every ORM model."""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped ``AsyncSession``.

    Each request gets its own session, closed once the request finishes
    regardless of outcome. Route handlers are responsible for calling
    ``commit()`` when their work should be durable; an unhandled exception
    leaves the session's transaction uncommitted, and closing it discards
    whatever was not committed.
    """
    async with async_session_factory() as session:
        yield session
