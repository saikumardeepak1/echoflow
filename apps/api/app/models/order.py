"""Order: a business's order record, looked up by the agent's lookup_order tool."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.contact import Contact
    from app.models.organization import Organization


class Order(UUIDPrimaryKeyMixin, Base):
    """An order placed by a contact, seeded by staff in v1 (see
    docs/TDD.md 10: stands in for a real order-system connector).
    """

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_organization_id", "organization_id"),
        UniqueConstraint("organization_id", "order_number", name="uq_orders_org_order_number"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False
    )
    order_number: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    item_description: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    organization: Mapped["Organization"] = relationship(back_populates="orders")
    contact: Mapped["Contact"] = relationship(back_populates="orders")
