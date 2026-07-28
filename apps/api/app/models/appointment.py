"""Appointment: a booked slot for a contact, created by the agent or staff."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.contact import Contact
    from app.models.notification import Notification
    from app.models.organization import Organization


class Appointment(UUIDPrimaryKeyMixin, Base):
    """A booked appointment slot, created via the agent's ``book_appointment``
    tool or directly by staff through the dashboard API.
    """

    __tablename__ = "appointments"
    __table_args__ = (Index("ix_appointments_organization_id", "organization_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="scheduled")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="appointments")
    contact: Mapped["Contact"] = relationship(back_populates="appointments")
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="appointment", cascade="all, delete-orphan"
    )
