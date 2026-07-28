"""Integration tests for app.workers.jobs.

Unlike most integration tests in this suite (see conftest.py's ``db_session``
fixture, built on pytest-asyncio's per-test event loop), these run job
functions through a real RQ worker against a real Redis (RQ's
``SimpleWorker`` in burst mode: runs every currently-enqueued job to
completion in this process, then returns, no fork involved). Each job opens
its own event loop and database connection via ``asyncio.run`` (see
app/workers/jobs.py's module docstring for why), and ``asyncio.run`` refuses
to nest inside an already-running loop -- so these tests are plain,
synchronous functions rather than the ``async def`` style used elsewhere in
this suite, and every async database operation they need (setup, teardown,
assertions) goes through the same ``asyncio.run``-per-call pattern the jobs
themselves use, via the local ``_run`` helper below, rather than an
``await``.

That also means every row a test needs a burst-processed job to see has to
be a real, committed row: the job's own connection is a different Postgres
connection than an ``async_session_factory()`` call made from a *different*
``asyncio.run`` invocation, and under ordinary transaction isolation an
uncommitted insert on one connection is invisible to another. So fixtures
here commit for real (through ``async_session_factory`` directly, the same
factory app/workers/jobs.py uses) and delete what they created in teardown,
rather than relying on a rollback like ``db_session`` does.
"""

import asyncio
import uuid
from collections.abc import Coroutine, Generator
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar
from unittest.mock import patch

import pytest
from redis import Redis
from rq import Queue, SimpleWorker
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory, engine
from app.models import Appointment, Contact, Notification, Organization
from app.services import appointment_service
from app.workers.jobs import enqueue_due_reminders, notify_appointment

_T = TypeVar("_T")


def _run(coro: Coroutine[Any, _T, _T]) -> _T:
    """Run one async database operation in its own event loop, then dispose
    the shared engine's connection pool before that loop closes.

    Mirrors app/workers/jobs.py's own ``_run`` (see its docstring): without
    the dispose, a connection opened on this call's loop would sit in the
    pool and get handed to the *next* ``asyncio.run`` call's brand-new loop
    (this test's next helper call, or a job processed by ``_burst``), which
    crashes since asyncpg connections are loop-bound.
    """

    async def _with_engine_cleanup() -> _T:
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_with_engine_cleanup())


@pytest.fixture
def redis_queue() -> Generator[Queue, None, None]:
    """The real "default" RQ queue, emptied before and after the test (same
    convention as tests/test_appointment_service.py's fixture of the same
    name), so leftover jobs from a previous run never leak into an assertion
    about exactly what a test enqueued.
    """
    connection = Redis.from_url(settings.redis_url)
    queue = Queue("default", connection=connection)
    queue.empty()  # type: ignore[no-untyped-call]
    _clear_failed_job_registry(queue)
    yield queue
    queue.empty()  # type: ignore[no-untyped-call]
    _clear_failed_job_registry(queue)


def _clear_failed_job_registry(queue: Queue) -> None:
    """``Queue.empty()`` only clears jobs still waiting to run; a job that
    already failed (see test_notify_appointment_send_failure_records_failed_notification)
    lives on in the failed job registry with its own TTL, so a leftover
    failure from an earlier test run would otherwise make
    ``test_notify_appointment_missing_appointment_is_a_noop``'s "no failures"
    assertion flaky depending on run order/history.
    """
    registry = queue.failed_job_registry
    for job_id in registry.get_job_ids():
        registry.remove(job_id, delete_job=True)


def _burst(queue: Queue) -> None:
    """Run every job currently on ``queue`` to completion, synchronously, in
    this process. ``SimpleWorker`` (unlike RQ's default ``Worker``) never
    forks, so patches applied in the test process (e.g. mocking
    ``twilio_service.send_sms``) are visible to the job while it runs. Must
    be called with no ``asyncio`` event loop running on this thread (see
    module docstring), which is why every test in this file is a plain
    ``def``, not ``async def``.
    """
    worker = SimpleWorker([queue], connection=queue.connection)
    worker.work(burst=True)


async def _create_org_and_contact(session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    organization = Organization(name="Riverside Dental")
    session.add(organization)
    await session.flush()
    contact = Contact(organization_id=organization.id, e164_number="+15551234567")
    session.add(contact)
    await session.commit()
    return organization.id, contact.id


async def _commit_org_and_contact() -> tuple[uuid.UUID, uuid.UUID]:
    async with async_session_factory() as session:
        return await _create_org_and_contact(session)


async def _delete_org(organization_id: uuid.UUID) -> None:
    async with async_session_factory() as session:
        await session.execute(delete(Organization).where(Organization.id == organization_id))
        await session.commit()


async def _create_appointment(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    scheduled_at: datetime,
    status: str = "scheduled",
) -> uuid.UUID:
    appointment = Appointment(
        organization_id=organization_id,
        contact_id=contact_id,
        scheduled_at=scheduled_at,
        duration_minutes=30,
        status=status,
    )
    session.add(appointment)
    await session.commit()
    return appointment.id


async def _commit_appointment(
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    scheduled_at: datetime,
    status: str = "scheduled",
) -> uuid.UUID:
    async with async_session_factory() as session:
        return await _create_appointment(
            session, organization_id, contact_id, scheduled_at, status
        )


async def _create_reminder_notification(appointment_id: uuid.UUID, at: datetime) -> None:
    async with async_session_factory() as session:
        session.add(
            Notification(
                appointment_id=appointment_id,
                kind="reminder",
                status="sent",
                send_at=at,
                sent_at=at,
            )
        )
        await session.commit()


async def _get_notifications(appointment_id: uuid.UUID) -> list[Notification]:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Notification).where(Notification.appointment_id == appointment_id)
        )
        return list(result.scalars().all())


@pytest.fixture
def org_and_contact() -> Generator[tuple[uuid.UUID, uuid.UUID], None, None]:
    organization_id, contact_id = _run(_commit_org_and_contact())
    yield organization_id, contact_id
    _run(_delete_org(organization_id))


@pytest.fixture
def committed_appointment(
    org_and_contact: tuple[uuid.UUID, uuid.UUID],
) -> uuid.UUID:
    organization_id, contact_id = org_and_contact
    scheduled_at = datetime.now(UTC) + timedelta(hours=2)
    return _run(_commit_appointment(organization_id, contact_id, scheduled_at))


# --- notify_appointment, processed by a real RQ worker ----------------------


def test_notify_appointment_confirmation_sends_sms_and_records_sent_notification(
    redis_queue: Queue, committed_appointment: uuid.UUID
) -> None:
    with patch("app.services.twilio_service.send_sms", return_value="SM123") as mock_send:
        redis_queue.enqueue(notify_appointment, str(committed_appointment), kind="confirmation")
        _burst(redis_queue)

    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert kwargs["to"] == "+15551234567"
    assert "confirmed" in kwargs["body"].lower()

    notifications = _run(_get_notifications(committed_appointment))
    assert len(notifications) == 1
    notification = notifications[0]
    assert notification.kind == "confirmation"
    assert notification.status == "sent"
    assert notification.sent_at is not None


def test_notify_appointment_reminder_message_mentions_reminder(
    redis_queue: Queue, committed_appointment: uuid.UUID
) -> None:
    with patch("app.services.twilio_service.send_sms", return_value="SM124") as mock_send:
        redis_queue.enqueue(notify_appointment, str(committed_appointment), kind="reminder")
        _burst(redis_queue)

    _, kwargs = mock_send.call_args
    assert "reminder" in kwargs["body"].lower()

    notifications = _run(_get_notifications(committed_appointment))
    assert notifications[0].kind == "reminder"
    assert notifications[0].status == "sent"


def test_notify_appointment_missing_appointment_is_a_noop(redis_queue: Queue) -> None:
    with patch("app.services.twilio_service.send_sms") as mock_send:
        redis_queue.enqueue(notify_appointment, str(uuid.uuid4()), kind="confirmation")
        _burst(redis_queue)

    mock_send.assert_not_called()
    assert redis_queue.failed_job_registry.count == 0


def test_notify_appointment_send_failure_records_failed_notification(
    redis_queue: Queue, committed_appointment: uuid.UUID
) -> None:
    with patch("app.services.twilio_service.send_sms", side_effect=RuntimeError("Twilio is down")):
        job = redis_queue.enqueue(notify_appointment, str(committed_appointment), kind="reminder")
        _burst(redis_queue)

    assert job.id in redis_queue.failed_job_registry.get_job_ids()

    notifications = _run(_get_notifications(committed_appointment))
    assert len(notifications) == 1
    assert notifications[0].status == "failed"
    assert notifications[0].sent_at is None


# --- booking -> confirmation job, end to end ---------------------------------


def test_booking_confirmation_job_is_consumed_end_to_end(redis_queue: Queue) -> None:
    """`appointment_service.book` enqueues `notify_appointment(kind="confirmation")`
    (see app/services/appointment_service.py); this asserts a real worker
    actually consumes and completes that job, not just that it was enqueued
    (see tests/test_appointment_service.py for the enqueue-only checks
    against the pre-worker behavior).
    """
    organization_id, contact_id = _run(_commit_org_and_contact())
    try:
        scheduled_at = (datetime.now(UTC) + timedelta(days=2)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )

        async def _book() -> uuid.UUID:
            async with async_session_factory() as session:
                await appointment_service.set_business_hours(
                    session,
                    organization_id,
                    day_of_week=scheduled_at.weekday(),
                    opens_at=scheduled_at.time().replace(hour=0, minute=0),
                    closes_at=scheduled_at.time().replace(hour=23, minute=59),
                )
                appointment = await appointment_service.book(
                    session, organization_id, contact_id, scheduled_at, duration_minutes=30
                )
                await session.commit()
                return appointment.id

        with patch("app.services.twilio_service.send_sms", return_value="SM555") as mock_send:
            appointment_id = _run(_book())
            _burst(redis_queue)

        mock_send.assert_called_once()
        notifications = _run(_get_notifications(appointment_id))
        assert len(notifications) == 1
        assert notifications[0].kind == "confirmation"
        assert notifications[0].status == "sent"
    finally:
        _run(_delete_org(organization_id))


# --- enqueue_due_reminders ----------------------------------------------------


def test_enqueue_due_reminders_selects_only_due_and_unreminded_appointments(
    redis_queue: Queue,
) -> None:
    organization_id, contact_id = _run(_commit_org_and_contact())
    try:
        now = datetime.now(UTC)

        due_without_reminder = _run(
            _commit_appointment(organization_id, contact_id, now + timedelta(hours=12))
        )
        due_with_reminder = _run(
            _commit_appointment(organization_id, contact_id, now + timedelta(hours=12))
        )
        outside_lead_time = _run(
            _commit_appointment(organization_id, contact_id, now + timedelta(hours=48))
        )
        cancelled_but_due = _run(
            _commit_appointment(
                organization_id, contact_id, now + timedelta(hours=12), status="cancelled"
            )
        )

        _run(_create_reminder_notification(due_with_reminder, now))

        enqueued_count = enqueue_due_reminders()

        job_appointment_ids = {
            job.args[0] for job in redis_queue.jobs if job.func_name.endswith("notify_appointment")
        }

        assert str(due_without_reminder) in job_appointment_ids
        assert str(due_with_reminder) not in job_appointment_ids
        assert str(outside_lead_time) not in job_appointment_ids
        assert str(cancelled_but_due) not in job_appointment_ids
        assert enqueued_count == 1
    finally:
        _run(_delete_org(organization_id))


def test_enqueue_due_reminders_returns_zero_when_nothing_is_due(redis_queue: Queue) -> None:
    organization_id, contact_id = _run(_commit_org_and_contact())
    try:
        scheduled_at = datetime.now(UTC) + timedelta(hours=48)
        _run(_commit_appointment(organization_id, contact_id, scheduled_at))

        assert enqueue_due_reminders() == 0
        assert len(redis_queue.jobs) == 0
    finally:
        _run(_delete_org(organization_id))
