"""SQLAlchemy ORM models for the core schema (see docs/ARCHITECTURE.md).

No models yet: the schema (organizations, users, contacts, conversations,
messages, and the rest of the entities in ARCHITECTURE.md) lands in the next
issue. This package exists now so Alembic's env.py has a stable
``Base.metadata`` to point at, with zero tables, so `alembic upgrade head`
succeeds as a no-op until the first migration adds one.
"""

from app.core.db import Base

__all__ = ["Base"]
