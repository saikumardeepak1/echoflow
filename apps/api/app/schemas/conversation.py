"""Request/response schemas for /v1/conversations routes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.conversation import Conversation
from app.models.message import Message


class MessageResponse(BaseModel):
    """One turn in a conversation's transcript."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "role": "user",
                    "content": "Can I book a cleaning for tomorrow?",
                    "created_at": "2026-07-23T09:15:00Z",
                }
            ]
        },
    )

    id: uuid.UUID
    role: str
    content: str
    created_at: datetime

    @classmethod
    def from_message(cls, message: Message) -> "MessageResponse":
        return cls(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
        )


class ConversationResponse(BaseModel):
    """The listable view of a conversation: metadata only, no messages.
    Used by ``GET /v1/conversations``.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "contact_id": "9c858901-8a57-4791-81fe-4c455b099bc9",
                    "channel": "sms",
                    "status": "open",
                    "contact_phone_number": "+15551234567",
                    "created_at": "2026-07-23T09:15:00Z",
                }
            ]
        },
    )

    id: uuid.UUID
    organization_id: uuid.UUID
    contact_id: uuid.UUID
    channel: str
    status: str
    contact_phone_number: str
    created_at: datetime

    @classmethod
    def from_conversation(cls, conversation: Conversation) -> "ConversationResponse":
        """Builds the response from a `Conversation` ORM instance, pulling
        `contact_phone_number` off the loaded `contact` relationship since it
        lives on `Contact`, not `Conversation` itself.
        """
        return cls(
            id=conversation.id,
            organization_id=conversation.organization_id,
            contact_id=conversation.contact_id,
            channel=conversation.channel,
            status=conversation.status,
            contact_phone_number=conversation.contact.e164_number,
            created_at=conversation.created_at,
        )


class ConversationDetailResponse(ConversationResponse):
    """The detail view of a conversation: metadata plus its full transcript,
    ordered chronologically. Used by ``GET /v1/conversations/{id}``.
    """

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "contact_id": "9c858901-8a57-4791-81fe-4c455b099bc9",
                    "channel": "sms",
                    "status": "open",
                    "contact_phone_number": "+15551234567",
                    "created_at": "2026-07-23T09:15:00Z",
                    "messages": [
                        {
                            "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                            "role": "user",
                            "content": "Can I book a cleaning for tomorrow?",
                            "created_at": "2026-07-23T09:15:00Z",
                        }
                    ],
                }
            ]
        },
    )

    messages: list[MessageResponse]

    @classmethod
    def from_conversation(cls, conversation: Conversation) -> "ConversationDetailResponse":
        return cls(
            id=conversation.id,
            organization_id=conversation.organization_id,
            contact_id=conversation.contact_id,
            channel=conversation.channel,
            status=conversation.status,
            contact_phone_number=conversation.contact.e164_number,
            created_at=conversation.created_at,
            messages=[MessageResponse.from_message(message) for message in conversation.messages],
        )
