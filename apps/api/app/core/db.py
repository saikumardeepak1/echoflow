"""Async SQLAlchemy engine and declarative base.

The engine is created once at import time from ``settings.database_url``
(the same asyncpg-based URL Alembic's ``env.py`` uses), so the application
and migrations always target the same database configuration. A
request-scoped session dependency lands alongside the first DB-backed
routes, in the schema issue that follows this one (see docs/ROADMAP.md,
Milestone 1).
"""

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)


class Base(DeclarativeBase):
    """Declarative base class shared by every ORM model."""
