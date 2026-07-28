"""PhoneNumber: a Twilio number an organization receives calls/texts on."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class PhoneNumber(UUIDPrimaryKeyMixin, Base):
    """A Twilio phone number in E.164 format, owned by exactly one
    organization. Inbound voice/SMS webhooks resolve the owning organization
    by looking up the ``To`` number here.
    """

    __tablename__ = "phone_numbers"
    __table_args__ = (Index("ix_phone_numbers_organization_id", "organization_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    e164_number: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="phone_numbers")
