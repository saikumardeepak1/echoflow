"""Request/response schemas for /v1/phone-numbers routes."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PhoneNumberCreateRequest(BaseModel):
    """A Twilio number to register as belonging to the caller's organization."""

    model_config = ConfigDict(
        json_schema_extra={"examples": [{"e164_number": "+15551234567"}]}
    )

    e164_number: str = Field(min_length=1, max_length=20)


class PhoneNumberResponse(BaseModel):
    """The persisted, listable view of a phone number."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "organization_id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
                    "e164_number": "+15551234567",
                    "created_at": "2026-07-23T09:15:00Z",
                }
            ]
        },
    )

    id: uuid.UUID
    organization_id: uuid.UUID
    e164_number: str
    created_at: datetime
