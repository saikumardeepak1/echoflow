"""Business-hours-aware availability calculation and appointment booking.

See docs/TDD.md section 3.2: this service backs both the agent's
``check_availability``/``book_appointment`` tools and the dashboard's
``/v1/appointments`` and ``/v1/business-hours`` routes, so the two entry
points never diverge on what counts as an open slot or a valid booking.

Times are treated as UTC throughout (``BusinessHours.opens_at``/
``closes_at`` are naive ``time`` values interpreted as UTC-of-day; a naive
``scheduled_at`` passed in is likewise assumed to already be UTC), since the
schema has no per-organization timezone field yet.

None of these functions call ``session.commit()``; they ``flush()`` so a
newly-created or modified row is visible to subsequent queries within the
same session, but committing is left to the caller (see
app/services/conversation_service.py for the same convention).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import date as date_type
from datetime import time as time_type

from redis import Redis
from rq import Queue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.appointment import Appointment
from app.models.business_hours import BusinessHours
from app.workers.jobs import notify_appointment

_VALID_STATUSES = {"scheduled", "cancelled", "completed"}


class AppointmentServiceError(Exception):
    """Base class for domain errors this service raises, so route handlers
    can catch either the specific subclass or this base class.
    """


class OutsideBusinessHoursError(AppointmentServiceError):
    """Raised when a requested slot falls outside the organization's
    configured business hours for that weekday, or no hours are configured
    for that weekday at all.
    """


class AppointmentConflictError(AppointmentServiceError):
    """Raised when a requested slot overlaps an existing, non-cancelled
    appointment for the same organization.
    """


class AppointmentNotFoundError(AppointmentServiceError):
    """Raised when an appointment id does not exist, or exists but belongs
    to a different organization (treated identically so a caller cannot
    probe for other orgs' appointment ids).
    """


@dataclass(frozen=True)
class AvailableSlot:
    """One contiguous open window on a given day, after business hours have
    had existing appointments subtracted out.
    """

    start: datetime
    end: datetime


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC, treating a naive value as already UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _merge_intervals(
    intervals: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Sort and merge overlapping/adjacent intervals into their union, so
    two back-to-back or overlapping appointments are treated as one busy
    block rather than producing a spurious zero-length free slot between
    them.
    """
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda interval: interval[0])
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _subtract_intervals(
    window: tuple[datetime, datetime], busy: list[tuple[datetime, datetime]]
) -> list[tuple[datetime, datetime]]:
    """Subtract a set of already-merged, sorted busy intervals from a single
    window, returning the remaining free sub-intervals in order.
    """
    window_start, window_end = window
    free: list[tuple[datetime, datetime]] = []
    cursor = window_start
    for busy_start, busy_end in busy:
        clipped_start = max(busy_start, window_start)
        clipped_end = min(busy_end, window_end)
        if clipped_start >= clipped_end:
            # Entirely outside the window (or zero-width after clipping):
            # doesn't consume any of it.
            continue
        if clipped_start > cursor:
            free.append((cursor, clipped_start))
        cursor = max(cursor, clipped_end)
    if cursor < window_end:
        free.append((cursor, window_end))
    return free


def _default_queue() -> Queue:
    connection = Redis.from_url(settings.redis_url)
    return Queue("default", connection=connection)


async def _get_business_hours(
    session: AsyncSession, organization_id: uuid.UUID, day_of_week: int
) -> BusinessHours | None:
    result = await session.execute(
        select(BusinessHours).where(
            BusinessHours.organization_id == organization_id,
            BusinessHours.day_of_week == day_of_week,
        )
    )
    return result.scalar_one_or_none()


async def _find_conflicting_appointment(
    session: AsyncSession,
    organization_id: uuid.UUID,
    start: datetime,
    end: datetime,
    exclude_appointment_id: uuid.UUID | None = None,
) -> Appointment | None:
    """Find a non-cancelled appointment for ``organization_id`` whose
    [scheduled_at, scheduled_at + duration) interval overlaps [start, end).

    Filters broadly in SQL (organization, not-cancelled, started within a
    day of ``start``, a generous bound since no appointment in this domain
    is expected to run 24+ hours) then checks the precise overlap in
    Python, since duration is stored as a plain integer rather than a range
    type.
    """
    result = await session.execute(
        select(Appointment).where(
            Appointment.organization_id == organization_id,
            Appointment.status != "cancelled",
            Appointment.scheduled_at < end,
            Appointment.scheduled_at >= start - timedelta(days=1),
        )
    )
    for candidate in result.scalars().all():
        if exclude_appointment_id is not None and candidate.id == exclude_appointment_id:
            continue
        candidate_end = candidate.scheduled_at + timedelta(minutes=candidate.duration_minutes)
        if candidate.scheduled_at < end and candidate_end > start:
            return candidate
    return None


async def _validate_slot(
    session: AsyncSession,
    organization_id: uuid.UUID,
    scheduled_at: datetime,
    duration_minutes: int,
    exclude_appointment_id: uuid.UUID | None = None,
) -> None:
    """Raise ``OutsideBusinessHoursError`` or ``AppointmentConflictError`` if
    [scheduled_at, scheduled_at + duration_minutes) is not a bookable slot.
    Used by both ``book`` and ``update_appointment`` (when rescheduling) so
    the two never diverge on what counts as valid.
    """
    business_hours = await _get_business_hours(session, organization_id, scheduled_at.weekday())
    if business_hours is None:
        raise OutsideBusinessHoursError(
            f"No business hours configured for {scheduled_at.date():%A}"
        )

    window_start = datetime.combine(scheduled_at.date(), business_hours.opens_at, tzinfo=UTC)
    window_end = datetime.combine(scheduled_at.date(), business_hours.closes_at, tzinfo=UTC)
    appointment_end = scheduled_at + timedelta(minutes=duration_minutes)

    if scheduled_at < window_start or appointment_end > window_end:
        raise OutsideBusinessHoursError(
            f"{scheduled_at.isoformat()} is outside business hours "
            f"({business_hours.opens_at}-{business_hours.closes_at} on "
            f"{scheduled_at.date():%A}s)"
        )

    conflict = await _find_conflicting_appointment(
        session,
        organization_id,
        scheduled_at,
        appointment_end,
        exclude_appointment_id=exclude_appointment_id,
    )
    if conflict is not None:
        raise AppointmentConflictError(
            f"Slot conflicts with an existing appointment at "
            f"{conflict.scheduled_at.isoformat()}"
        )


async def set_business_hours(
    session: AsyncSession,
    organization_id: uuid.UUID,
    day_of_week: int,
    opens_at: time_type,
    closes_at: time_type,
) -> BusinessHours:
    """Configure one weekday's availability window for ``organization_id``.

    Upserts: calling this again for a ``day_of_week`` that already has a row
    (there is at most one per organization per day, per the
    ``uq_business_hours_org_day_of_week`` constraint) replaces its window
    rather than creating a duplicate.
    """
    if not 0 <= day_of_week <= 6:
        raise ValueError("day_of_week must be between 0 (Monday) and 6 (Sunday)")
    if opens_at >= closes_at:
        raise ValueError("opens_at must be before closes_at")

    business_hours = await _get_business_hours(session, organization_id, day_of_week)
    if business_hours is not None:
        business_hours.opens_at = opens_at
        business_hours.closes_at = closes_at
    else:
        business_hours = BusinessHours(
            organization_id=organization_id,
            day_of_week=day_of_week,
            opens_at=opens_at,
            closes_at=closes_at,
        )
        session.add(business_hours)
    await session.flush()
    return business_hours


async def check_availability(
    session: AsyncSession, organization_id: uuid.UUID, date: date_type
) -> list[AvailableSlot]:
    """Compute the open slots for ``organization_id`` on ``date``: that
    weekday's configured business hours window, minus any non-cancelled
    appointment already booked that day.

    Returns an empty list if no business hours are configured for that
    weekday at all (nothing is bookable), or if the whole window is already
    booked out.
    """
    business_hours = await _get_business_hours(session, organization_id, date.weekday())
    if business_hours is None:
        return []

    window_start = datetime.combine(date, business_hours.opens_at, tzinfo=UTC)
    window_end = datetime.combine(date, business_hours.closes_at, tzinfo=UTC)
    if window_start >= window_end:
        return []

    day_start = datetime.combine(date, time_type.min, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    result = await session.execute(
        select(Appointment).where(
            Appointment.organization_id == organization_id,
            Appointment.status != "cancelled",
            Appointment.scheduled_at < day_end,
            Appointment.scheduled_at >= day_start - timedelta(days=1),
        )
    )

    busy: list[tuple[datetime, datetime]] = []
    for appointment in result.scalars().all():
        appt_start = appointment.scheduled_at
        appt_end = appt_start + timedelta(minutes=appointment.duration_minutes)
        # Clip to the business-hours window; an appointment entirely outside
        # it (shouldn't normally exist, but business hours can change after
        # a booking) doesn't shrink the window at all.
        if appt_end <= window_start or appt_start >= window_end:
            continue
        busy.append((appt_start, appt_end))

    free = _subtract_intervals((window_start, window_end), _merge_intervals(busy))
    return [AvailableSlot(start=start, end=end) for start, end in free]


async def book(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    scheduled_at: datetime,
    duration_minutes: int,
    notes: str | None = None,
) -> Appointment:
    """Book an appointment, validating the slot is within business hours and
    does not conflict with an existing appointment first.

    On success, enqueues a ``notify_appointment(kind="confirmation")`` RQ job
    (see docs/TDD.md section 3.5) so the caller (webhook handler or dashboard
    API route) isn't blocked on an outbound Twilio REST call; the worker that
    consumes this queue lands in a later issue, so this only asserts the job
    was enqueued, not that it ran.

    Raises ``OutsideBusinessHoursError`` or ``AppointmentConflictError`` for
    an invalid slot; raises ``ValueError`` for a non-positive duration.
    """
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive")

    scheduled_at = _as_utc(scheduled_at)
    await _validate_slot(session, organization_id, scheduled_at, duration_minutes)

    appointment = Appointment(
        organization_id=organization_id,
        contact_id=contact_id,
        scheduled_at=scheduled_at,
        duration_minutes=duration_minutes,
        status="scheduled",
        notes=notes,
    )
    session.add(appointment)
    await session.flush()

    queue = _default_queue()
    queue.enqueue(notify_appointment, str(appointment.id), kind="confirmation")

    return appointment


async def list_appointments(
    session: AsyncSession, organization_id: uuid.UUID, on_date: date_type | None = None
) -> list[Appointment]:
    """List an organization's appointments, most-imminent first, optionally
    filtered to a single calendar day (UTC) for the dashboard's day view.
    """
    query = select(Appointment).where(Appointment.organization_id == organization_id)
    if on_date is not None:
        day_start = datetime.combine(on_date, time_type.min, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        query = query.where(
            Appointment.scheduled_at >= day_start, Appointment.scheduled_at < day_end
        )
    result = await session.execute(query.order_by(Appointment.scheduled_at))
    return list(result.scalars().all())


async def update_appointment(
    session: AsyncSession,
    organization_id: uuid.UUID,
    appointment_id: uuid.UUID,
    updates: dict[str, object],
) -> Appointment:
    """Apply a partial update to an appointment scoped to ``organization_id``.

    ``updates`` should only contain keys the caller actually supplied (e.g.
    via Pydantic's ``model_dump(exclude_unset=True)``), so an omitted field
    is left untouched rather than reset to ``None``. Rescheduling
    (``scheduled_at`` and/or ``duration_minutes`` present) re-runs the same
    business-hours and conflict validation as ``book``, excluding this
    appointment itself from the conflict check.

    Raises ``AppointmentNotFoundError`` for an unknown id or one belonging
    to another organization; ``ValueError`` for an invalid ``status``.
    """
    appointment = await session.get(Appointment, appointment_id)
    if appointment is None or appointment.organization_id != organization_id:
        raise AppointmentNotFoundError(f"Appointment {appointment_id} not found")

    if "status" in updates:
        new_status = updates["status"]
        if new_status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}")
        appointment.status = new_status  # type: ignore[assignment]

    if "notes" in updates:
        appointment.notes = updates["notes"]  # type: ignore[assignment]

    if "scheduled_at" in updates or "duration_minutes" in updates:
        new_scheduled_at = _as_utc(updates.get("scheduled_at") or appointment.scheduled_at)  # type: ignore[arg-type]
        new_duration = updates.get("duration_minutes", appointment.duration_minutes)
        await _validate_slot(
            session,
            organization_id,
            new_scheduled_at,
            new_duration,  # type: ignore[arg-type]
            exclude_appointment_id=appointment.id,
        )
        appointment.scheduled_at = new_scheduled_at
        appointment.duration_minutes = new_duration  # type: ignore[assignment]

    session.add(appointment)
    await session.flush()
    return appointment
