"""Organization: the top-level tenant every other entity is scoped to."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.base import UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.api_key import ApiKey
    from app.models.appointment import Appointment
    from app.models.business_hours import BusinessHours
    from app.models.contact import Contact
    from app.models.conversation import Conversation
    from app.models.knowledge_document import KnowledgeDocument
    from app.models.order import Order
    from app.models.phone_number import PhoneNumber
    from app.models.user import User


class Organization(UUIDPrimaryKeyMixin, Base):
    """A tenant business. Every user, contact, and conversation belongs to
    exactly one.
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    users: Mapped[list["User"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    api_keys: Mapped[list["ApiKey"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    phone_numbers: Mapped[list["PhoneNumber"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    contacts: Mapped[list["Contact"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    knowledge_documents: Mapped[list["KnowledgeDocument"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    orders: Mapped[list["Order"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    business_hours: Mapped[list["BusinessHours"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
    appointments: Mapped[list["Appointment"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )
