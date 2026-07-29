"""Request/response schemas for /v1/appointments and /v1/business-hours routes."""

import uuid
from datetime import datetime, time

from pydantic import BaseModel, ConfigDict, Field


class BusinessHoursRequest(BaseModel):
    """Configures a single weekday's open/close window. Posting again for the
    same ``day_of_week`` replaces it rather than adding a duplicate.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{"day_of_week": 0, "opens_at": "09:00:00", "closes_at": "17:00:00"}]
        }
    )

    day_of_week: int = Field(ge=0, le=6, description="0 = Monday .. 6 = Sunday")
    opens_at: time
    closes_at: time


class BusinessHoursResponse(BaseModel):
    """The persisted view of a single weekday's business hours."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "day_of_week": 0,
                    "opens_at": "09:00:00",
                    "closes_at": "17:00:00",
                }
            ]
        },
    )

    id: uuid.UUID
    day_of_week: int
    opens_at: time
    closes_at: time


class AppointmentCreateRequest(BaseModel):
    """Books a new appointment for the caller's organization."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "contact_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "scheduled_at": "2026-07-28T15:00:00Z",
                    "duration_minutes": 30,
                    "notes": "Follow-up cleaning",
                }
            ]
        }
    )

    contact_id: uuid.UUID
    scheduled_at: datetime
    duration_minutes: int = Field(gt=0)
    notes: str | None = None


class AppointmentUpdateRequest(BaseModel):
    """All fields optional: only the ones the caller sets are applied
    (see app.services.appointment_service.update_appointment).
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "scheduled_at": "2026-07-29T16:00:00Z",
                    "duration_minutes": 45,
                    "notes": "Patient asked to push back an hour",
                    "status": "confirmed",
                }
            ]
        }
    )

    scheduled_at: datetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    notes: str | None = None
    status: str | None = None


class AppointmentResponse(BaseModel):
    """The persisted, listable view of an appointment."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                    "contact_id": "9c858901-8a57-4791-81fe-4c455b099bc9",
                    "scheduled_at": "2026-07-28T15:00:00Z",
                    "duration_minutes": 30,
                    "status": "confirmed",
                    "notes": "Follow-up cleaning",
                    "created_at": "2026-07-23T09:15:00Z",
                }
            ]
        },
    )

    id: uuid.UUID
    contact_id: uuid.UUID
    scheduled_at: datetime
    duration_minutes: int
    status: str
    notes: str | None
    created_at: datetime
