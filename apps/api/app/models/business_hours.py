"""BusinessHours: one weekday's open/close window for an organization."""

import uuid
from datetime import time as time_type
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, Time, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.organization import Organization


class BusinessHours(UUIDPrimaryKeyMixin, Base):
    """A single weekday's availability window, used by the agent's
    ``check_availability`` tool. ``day_of_week`` is 0 (Monday) through 6
    (Sunday), one row per day per organization.
    """

    __tablename__ = "business_hours"
    __table_args__ = (
        Index("ix_business_hours_organization_id", "organization_id"),
        UniqueConstraint(
            "organization_id", "day_of_week", name="uq_business_hours_org_day_of_week"
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)
    opens_at: Mapped[time_type] = mapped_column(Time, nullable=False)
    closes_at: Mapped[time_type] = mapped_column(Time, nullable=False)

    organization: Mapped["Organization"] = relationship(back_populates="business_hours")
