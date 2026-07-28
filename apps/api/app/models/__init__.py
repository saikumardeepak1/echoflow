"""SQLAlchemy ORM models for the core schema (see docs/ARCHITECTURE.md).

Every model must be imported here so that ``Base.metadata`` (used by both
the app and Alembic's autogenerate) is fully populated as soon as this
package is imported.
"""

from app.core.db import Base
from app.models.api_key import ApiKey
from app.models.appointment import Appointment
from app.models.business_hours import BusinessHours
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.knowledge_document import KnowledgeDocument
from app.models.message import Message
from app.models.notification import Notification
from app.models.order import Order
from app.models.organization import Organization
from app.models.phone_number import PhoneNumber
from app.models.refresh_token import RefreshToken
from app.models.user import User

__all__ = [
    "Base",
    "Organization",
    "User",
    "ApiKey",
    "RefreshToken",
    "PhoneNumber",
    "Contact",
    "Conversation",
    "Message",
    "KnowledgeDocument",
    "Order",
    "BusinessHours",
    "Appointment",
    "Notification",
]
