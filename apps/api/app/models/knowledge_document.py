"""KnowledgeDocument: a piece of business knowledge the agent can search.

``content_tsv`` backs Postgres full-text search (the agent's
``knowledge_search`` tool, see docs/TDD.md 3.3). It is kept in sync with
``content`` by a Postgres trigger (see the migration in alembic/versions/)
rather than set from application code, so it can never drift from the text
it indexes regardless of how a row is written or updated.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class KnowledgeDocument(UUIDPrimaryKeyMixin, Base):
    """A staff-authored knowledge base entry (hours, policies, FAQs, etc.)
    the agent draws answers from.
    """

    __tablename__ = "knowledge_documents"
    __table_args__ = (
        Index("ix_knowledge_documents_organization_id", "organization_id"),
        Index("ix_knowledge_documents_content_tsv", "content_tsv", postgresql_using="gin"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="knowledge_documents")
