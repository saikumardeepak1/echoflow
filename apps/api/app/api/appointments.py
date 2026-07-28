"""Appointment and business-hours dashboard routes (see docs/TDD.md sections
3.2 and 3.4). Session-authenticated: a logged-in dashboard user manages
their own organization's business hours and appointments, in addition to the
agent's own ``check_availability``/``book_appointment`` tools calling the
same underlying service.
"""

import uuid
from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_session
from app.models import User
from app.schemas.appointment import (
    AppointmentCreateRequest,
    AppointmentResponse,
    AppointmentUpdateRequest,
    BusinessHoursRequest,
    BusinessHoursResponse,
)
from app.services import appointment_service

router = APIRouter(tags=["appointments"])


@router.post(
    "/v1/business-hours",
    response_model=BusinessHoursResponse,
    status_code=status.HTTP_201_CREATED,
)
async def set_business_hours(
    body: BusinessHoursRequest,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> BusinessHoursResponse:
    """Configure one weekday's business hours for the caller's organization."""
    try:
        business_hours = await appointment_service.set_business_hours(
            session,
            current_user.organization_id,
            body.day_of_week,
            body.opens_at,
            body.closes_at,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    await session.refresh(business_hours)
    return BusinessHoursResponse.model_validate(business_hours)


@router.get("/v1/appointments", response_model=list[AppointmentResponse])
async def list_appointments(
    date: date_type | None = Query(default=None, description="Filter to one calendar day (UTC)"),
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> list[AppointmentResponse]:
    """List the caller's organization's appointments, soonest first."""
    appointments = await appointment_service.list_appointments(
        session, current_user.organization_id, on_date=date
    )
    return [AppointmentResponse.model_validate(appointment) for appointment in appointments]


@router.post(
    "/v1/appointments",
    response_model=AppointmentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_appointment(
    body: AppointmentCreateRequest,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> AppointmentResponse:
    """Book an appointment for the caller's organization (staff-initiated, in
    addition to the agent's own ``book_appointment`` tool). Rejects a slot
    outside business hours (422) or conflicting with an existing appointment
    (409); enqueues a confirmation notification job on success.
    """
    try:
        appointment = await appointment_service.book(
            session,
            current_user.organization_id,
            body.contact_id,
            body.scheduled_at,
            body.duration_minutes,
            notes=body.notes,
        )
    except appointment_service.OutsideBusinessHoursError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except appointment_service.AppointmentConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(appointment)
    return AppointmentResponse.model_validate(appointment)


@router.patch("/v1/appointments/{appointment_id}", response_model=AppointmentResponse)
async def update_appointment(
    appointment_id: uuid.UUID,
    body: AppointmentUpdateRequest,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> AppointmentResponse:
    """Partially update an appointment (reschedule, add notes, or change
    status) scoped to the caller's organization. 404s for an appointment
    that doesn't exist or belongs to another organization, so a caller can't
    distinguish "not found" from "not yours".
    """
    updates = body.model_dump(exclude_unset=True)
    try:
        appointment = await appointment_service.update_appointment(
            session, current_user.organization_id, appointment_id, updates
        )
    except appointment_service.AppointmentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except appointment_service.OutsideBusinessHoursError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except appointment_service.AppointmentConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    await session.commit()
    await session.refresh(appointment)
    return AppointmentResponse.model_validate(appointment)
