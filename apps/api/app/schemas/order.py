"""Request/response schemas for /v1/orders routes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.order import Order


class OrderCreateRequest(BaseModel):
    """Seeds one order record for the caller's organization (dashboard
    staff standing in for a real order-system connector, see
    docs/PRD.md non-goals). ``contact_phone_number`` is the E.164 number the
    order's contact will call or text in on; the contact is found-or-created
    by that number, the same way inbound webhooks resolve a `Contact`.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "order_number": "ORD-10482",
                    "item_description": "2x ceramic espresso cups, 1x pour-over kit",
                    "contact_phone_number": "+15551234567",
                    "status": "pending",
                }
            ]
        }
    )

    order_number: str = Field(min_length=1, max_length=100)
    item_description: str = Field(min_length=1, max_length=500)
    contact_phone_number: str = Field(min_length=1, max_length=20)
    status: str = Field(default="pending", min_length=1, max_length=50)


class OrderResponse(BaseModel):
    """The persisted, listable view of an order."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "contact_id": "9c858901-8a57-4791-81fe-4c455b099bc9",
                    "order_number": "ORD-10482",
                    "item_description": "2x ceramic espresso cups, 1x pour-over kit",
                    "status": "pending",
                    "contact_phone_number": "+15551234567",
                    "created_at": "2026-07-23T09:15:00Z",
                }
            ]
        },
    )

    id: uuid.UUID
    organization_id: uuid.UUID
    contact_id: uuid.UUID
    order_number: str
    item_description: str
    status: str
    contact_phone_number: str
    created_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> "OrderResponse":
        """Builds the response from an `Order` ORM instance, pulling
        `contact_phone_number` off the loaded `contact` relationship since
        it lives on `Contact`, not `Order` itself.
        """
        return cls(
            id=order.id,
            organization_id=order.organization_id,
            contact_id=order.contact_id,
            order_number=order.order_number,
            item_description=order.item_description,
            status=order.status,
            contact_phone_number=order.contact.e164_number,
            created_at=order.created_at,
        )
