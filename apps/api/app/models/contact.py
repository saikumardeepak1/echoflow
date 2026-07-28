"""Contact: a caller/texter an organization has exchanged messages with."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.appointment import Appointment
    from app.models.conversation import Conversation
    from app.models.order import Order
    from app.models.organization import Organization


class Contact(UUIDPrimaryKeyMixin, Base):
    """A person who has called or texted one of an organization's numbers.
    Found-or-created by ``e164_number`` per organization on the first inbound
    webhook from that number (see docs/ARCHITECTURE.md, request flows).
    """

    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_organization_id", "organization_id"),
        UniqueConstraint("organization_id", "e164_number", name="uq_contacts_org_e164_number"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    e164_number: Mapped[str] = mapped_column(String(20), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="contacts")
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
    appointments: Mapped[list["Appointment"]] = relationship(
        back_populates="contact", cascade="all, delete-orphan"
    )
