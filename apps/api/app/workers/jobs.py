"""RQ job definitions.

``notify_appointment`` is enqueued by ``app.services.appointment_service.book``
(see docs/TDD.md section 3.5) whenever an appointment is booked, so the
booking request is not blocked on an outbound Twilio REST call. It is also
enqueued by ``enqueue_due_reminders`` below, on a schedule driven by
``app/workers/worker.py``, for the reminder case.

RQ job functions run as plain synchronous callables inside the worker
process, outside of any FastAPI request scope, so there is no request-scoped
``AsyncSession`` to reuse (see ``app.core.db.get_session``). Rather than add a
second, sync database stack (a sync engine, a sync session factory, a second
set of query patterns) purely for the worker, each job opens its own fresh
``AsyncSession`` from the app's existing ``async_session_factory`` and drives
it to completion with ``asyncio.run``. This keeps every query in this module
using the exact same SQLAlchemy async API (and the exact same engine
configuration) as the rest of the app, at the cost of an event loop
start/stop per job, which is negligible next to the Twilio REST call each job
already makes.
"""

import asyncio
import logging
import uuid
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from redis import Redis
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import async_session_factory, engine
from app.models.appointment import Appointment
from app.models.notification import Notification
from app.services import twilio_service

logger = logging.getLogger(__name__)

# How far ahead of an appointment's start time the reminder sweep looks.
# 24 hours, per docs/ROADMAP.md's "outbound appointment notifications
# (confirmation & reminder)" milestone entry.
REMINDER_LEAD_TIME = timedelta(hours=24)

_T = TypeVar("_T")


def _run(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run an async database operation from a synchronous RQ job function.

    See the module docstring for why each job gets its own event loop and
    session rather than sharing one across jobs. ``asyncio.run`` tears the
    event loop down when ``coro`` finishes, but the async engine's
    connection pool doesn't know that: it holds onto asyncpg connections
    between checkouts for reuse *within the same event loop*. Left alone, a
    connection opened by one job's loop would sit in the pool and get handed
    to the next job's brand-new loop, which crashes (asyncpg connections are
    loop-bound). Disposing the pool here, still inside this call's own loop,
    closes out every connection this run opened before that loop goes away,
    so the next job or sweep iteration always starts from a clean pool.
    """

    async def _with_engine_cleanup() -> _T:
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_with_engine_cleanup())


def _default_queue() -> Queue:
    connection = Redis.from_url(settings.redis_url)
    return Queue("default", connection=connection)


def _build_message(kind: str, appointment: Appointment) -> str:
    """Build the outbound SMS body for a confirmation or reminder."""
    when = appointment.scheduled_at.strftime("%A, %B %-d at %-I:%M %p UTC")
    if kind == "confirmation":
        return f"Your appointment is confirmed for {when}."
    if kind == "reminder":
        return f"Reminder: you have an appointment on {when}."
    raise ValueError(f"Unknown notification kind: {kind!r}")


def notify_appointment(appointment_id: str, kind: str) -> None:
    """Send an outbound notification for an appointment.

    ``kind`` is ``"confirmation"`` (enqueued on booking by
    ``appointment_service.book``) or ``"reminder"`` (enqueued by
    ``enqueue_due_reminders`` below). Loads the appointment and its contact,
    sends an SMS via ``twilio_service.send_sms``, and writes a
    ``Notification`` row recording the attempt: ``status="sent"`` on
    success, ``status="failed"`` (and the exception re-raised, so RQ reports
    the job as failed) if the Twilio call raises.

    A missing appointment (e.g. deleted between enqueue and processing) logs
    a warning and returns without raising, since there is nothing left to
    notify about and retrying would never succeed.
    """
    _run(_notify_appointment(appointment_id, kind))


async def _notify_appointment(appointment_id: str, kind: str) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Appointment)
            .options(selectinload(Appointment.contact))
            .where(Appointment.id == uuid.UUID(appointment_id))
        )
        appointment = result.scalar_one_or_none()
        if appointment is None:
            logger.warning(
                "notify_appointment: appointment not found, skipping",
                extra={"appointment_id": appointment_id, "kind": kind},
            )
            return

        now = datetime.now(UTC)
        notification = Notification(
            appointment_id=appointment.id,
            kind=kind,
            status="pending",
            send_at=now,
        )
        session.add(notification)
        await session.flush()

        message = _build_message(kind, appointment)
        try:
            message_sid = twilio_service.send_sms(to=appointment.contact.e164_number, body=message)
        except Exception:
            notification.status = "failed"
            await session.commit()
            logger.exception(
                "notify_appointment: send_sms failed",
                extra={"appointment_id": appointment_id, "kind": kind},
            )
            raise

        notification.status = "sent"
        notification.sent_at = datetime.now(UTC)
        await session.commit()
        logger.info(
            "notify_appointment: sent",
            extra={
                "appointment_id": appointment_id,
                "kind": kind,
                "message_sid": message_sid,
            },
        )


def enqueue_due_reminders() -> int:
    """Enqueue a ``notify_appointment(kind="reminder")`` job for every
    non-cancelled appointment starting within ``REMINDER_LEAD_TIME`` that
    does not already have a reminder ``Notification`` row.

    Called by ``app/workers/worker.py`` on worker startup and then on an
    interval (see docs/TDD.md section 3.5). Returns the number of jobs
    enqueued, so the caller can log something useful.
    """
    return _run(_enqueue_due_reminders())


async def _enqueue_due_reminders() -> int:
    now = datetime.now(UTC)
    cutoff = now + REMINDER_LEAD_TIME

    async with async_session_factory() as session:
        due = await session.execute(
            select(Appointment).where(
                Appointment.status != "cancelled",
                Appointment.scheduled_at >= now,
                Appointment.scheduled_at <= cutoff,
            )
        )
        due_appointments = list(due.scalars().all())
        if not due_appointments:
            return 0

        appointment_ids = [appointment.id for appointment in due_appointments]
        already_reminded_result = await session.execute(
            select(Notification.appointment_id).where(
                Notification.kind == "reminder",
                Notification.appointment_id.in_(appointment_ids),
            )
        )
        already_reminded = {row[0] for row in already_reminded_result.all()}

    queue = _default_queue()
    enqueued = 0
    for appointment in due_appointments:
        if appointment.id in already_reminded:
            continue
        queue.enqueue(notify_appointment, str(appointment.id), kind="reminder")
        enqueued += 1
    return enqueued
