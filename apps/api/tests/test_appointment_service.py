"""Tests for app.services.appointment_service.

Runs against a real (migrated) Postgres database, see conftest.py, plus a
real Redis (see the redis_queue fixture below) for the ``book`` tests that
assert a confirmation job actually lands on the queue -- nothing consumes
that queue yet (the worker lands in a later issue), so these tests only
assert enqueueing, never that the job runs.
"""

import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime, time, timedelta

import pytest
from redis import Redis
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Appointment, Contact, Organization
from app.services import appointment_service


@pytest.fixture
def redis_queue() -> Generator[Queue, None, None]:
    """The real "default" RQ queue, emptied before and after the test so
    leftover jobs from a previous run (or a shared local Redis) never leak
    into an assertion about exactly what this test enqueued.
    """
    connection = Redis.from_url(settings.redis_url)
    queue = Queue("default", connection=connection)
    queue.empty()  # type: ignore[no-untyped-call]
    yield queue
    queue.empty()  # type: ignore[no-untyped-call]


async def _make_org(db_session: AsyncSession) -> Organization:
    organization = Organization(name="Riverside Dental")
    db_session.add(organization)
    await db_session.flush()
    return organization


async def _make_contact(db_session: AsyncSession, organization_id: uuid.UUID) -> Contact:
    contact = Contact(organization_id=organization_id, e164_number="+15551234567")
    db_session.add(contact)
    await db_session.flush()
    return contact


# A Tuesday, chosen arbitrarily and used across tests so day_of_week (1) is
# consistent between the business-hours setup and the scheduled_at/date
# values under test.
_TUESDAY = date(2026, 7, 28)
assert _TUESDAY.weekday() == 1


def _at(hour: int, minute: int = 0, day: date = _TUESDAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=UTC)


# --- set_business_hours -------------------------------------------------------


async def test_set_business_hours_creates_a_new_row(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)

    business_hours = await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )

    assert business_hours.id is not None
    assert business_hours.organization_id == organization.id
    assert business_hours.day_of_week == 1
    assert business_hours.opens_at == time(9, 0)
    assert business_hours.closes_at == time(17, 0)


async def test_set_business_hours_upserts_same_day_rather_than_duplicating(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)

    first = await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    second = await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(10, 0), closes_at=time(18, 0)
    )

    assert first.id == second.id
    fetched = await appointment_service._get_business_hours(db_session, organization.id, 1)
    assert fetched is not None
    assert fetched.opens_at == time(10, 0)
    assert fetched.closes_at == time(18, 0)


async def test_set_business_hours_rejects_out_of_range_day_of_week(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)

    with pytest.raises(ValueError, match="day_of_week"):
        await appointment_service.set_business_hours(
            db_session, organization.id, day_of_week=7, opens_at=time(9, 0), closes_at=time(17, 0)
        )


async def test_set_business_hours_rejects_opens_at_not_before_closes_at(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)

    with pytest.raises(ValueError, match="opens_at"):
        await appointment_service.set_business_hours(
            db_session, organization.id, day_of_week=1, opens_at=time(17, 0), closes_at=time(9, 0)
        )


# --- check_availability --------------------------------------------------------


async def test_check_availability_with_no_business_hours_returns_no_slots(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    assert slots == []


async def test_check_availability_with_no_appointments_returns_the_full_window(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    assert slots == [
        appointment_service.AvailableSlot(start=_at(9), end=_at(17)),
    ]


async def test_check_availability_excludes_a_booked_slot_in_the_middle(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    db_session.add(
        Appointment(
            organization_id=organization.id,
            contact_id=contact.id,
            scheduled_at=_at(12),
            duration_minutes=60,
            status="scheduled",
        )
    )
    await db_session.flush()

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    assert slots == [
        appointment_service.AvailableSlot(start=_at(9), end=_at(12)),
        appointment_service.AvailableSlot(start=_at(13), end=_at(17)),
    ]


async def test_check_availability_merges_overlapping_appointments(
    db_session: AsyncSession,
) -> None:
    """Two appointments that overlap each other should be treated as one
    contiguous busy block, not leave a spurious sliver of "free" time
    between them.
    """
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    db_session.add_all(
        [
            Appointment(
                organization_id=organization.id,
                contact_id=contact.id,
                scheduled_at=_at(12),
                duration_minutes=90,  # 12:00-13:30
                status="scheduled",
            ),
            Appointment(
                organization_id=organization.id,
                contact_id=contact.id,
                scheduled_at=_at(13),
                duration_minutes=60,  # 13:00-14:00, overlaps the first
                status="scheduled",
            ),
        ]
    )
    await db_session.flush()

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    # The two busy blocks (12:00-13:30 and 13:00-14:00) overlap, so their
    # union is one 12:00-14:00 block, not two separate gaps.
    assert slots == [
        appointment_service.AvailableSlot(start=_at(9), end=_at(12)),
        appointment_service.AvailableSlot(start=_at(14), end=_at(17)),
    ]


async def test_check_availability_two_separate_appointments_leave_three_free_slots(
    db_session: AsyncSession,
) -> None:
    """Two appointments that do *not* overlap should each carve out their
    own gap, unlike the overlapping case above.
    """
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    db_session.add_all(
        [
            Appointment(
                organization_id=organization.id,
                contact_id=contact.id,
                scheduled_at=_at(10),
                duration_minutes=30,
                status="scheduled",
            ),
            Appointment(
                organization_id=organization.id,
                contact_id=contact.id,
                scheduled_at=_at(14),
                duration_minutes=30,
                status="scheduled",
            ),
        ]
    )
    await db_session.flush()

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    assert slots == [
        appointment_service.AvailableSlot(start=_at(9), end=_at(10)),
        appointment_service.AvailableSlot(start=_at(10, 30), end=_at(14)),
        appointment_service.AvailableSlot(start=_at(14, 30), end=_at(17)),
    ]


async def test_check_availability_ignores_an_appointment_left_outside_narrowed_hours(
    db_session: AsyncSession,
) -> None:
    """If business hours are narrowed after an appointment was already
    booked under the wider window, that now-out-of-window appointment must
    not shrink the (already smaller) available window any further.
    """
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(8, 0), closes_at=time(18, 0)
    )
    await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(8), duration_minutes=30
    )
    # Narrow the window so the appointment above (8:00-8:30) now falls
    # entirely before it opens.
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    assert slots == [appointment_service.AvailableSlot(start=_at(9), end=_at(17))]


async def test_check_availability_edge_of_day_appointment_at_opening_time(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    db_session.add(
        Appointment(
            organization_id=organization.id,
            contact_id=contact.id,
            scheduled_at=_at(9),
            duration_minutes=30,
            status="scheduled",
        )
    )
    await db_session.flush()

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    assert slots == [appointment_service.AvailableSlot(start=_at(9, 30), end=_at(17))]


async def test_check_availability_edge_of_day_appointment_ending_at_closing_time(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    db_session.add(
        Appointment(
            organization_id=organization.id,
            contact_id=contact.id,
            scheduled_at=_at(16, 30),
            duration_minutes=30,
            status="scheduled",
        )
    )
    await db_session.flush()

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    assert slots == [appointment_service.AvailableSlot(start=_at(9), end=_at(16, 30))]


async def test_check_availability_fully_booked_day_returns_no_slots(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    db_session.add(
        Appointment(
            organization_id=organization.id,
            contact_id=contact.id,
            scheduled_at=_at(9),
            duration_minutes=8 * 60,
            status="scheduled",
        )
    )
    await db_session.flush()

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    assert slots == []


async def test_check_availability_ignores_cancelled_appointments(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    db_session.add(
        Appointment(
            organization_id=organization.id,
            contact_id=contact.id,
            scheduled_at=_at(12),
            duration_minutes=60,
            status="cancelled",
        )
    )
    await db_session.flush()

    slots = await appointment_service.check_availability(db_session, organization.id, _TUESDAY)

    assert slots == [appointment_service.AvailableSlot(start=_at(9), end=_at(17))]


async def test_check_availability_is_scoped_per_organization(db_session: AsyncSession) -> None:
    org_a = await _make_org(db_session)
    org_b = await _make_org(db_session)
    contact_b = await _make_contact(db_session, org_b.id)
    await appointment_service.set_business_hours(
        db_session, org_a.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    await appointment_service.set_business_hours(
        db_session, org_b.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    db_session.add(
        Appointment(
            organization_id=org_b.id,
            contact_id=contact_b.id,
            scheduled_at=_at(9),
            duration_minutes=8 * 60,
            status="scheduled",
        )
    )
    await db_session.flush()

    slots_a = await appointment_service.check_availability(db_session, org_a.id, _TUESDAY)

    assert slots_a == [appointment_service.AvailableSlot(start=_at(9), end=_at(17))]


# --- book ------------------------------------------------------------------


async def test_book_creates_a_scheduled_appointment_within_business_hours(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )

    appointment = await appointment_service.book(
        db_session,
        organization.id,
        contact.id,
        scheduled_at=_at(10),
        duration_minutes=30,
        notes="First cleaning",
    )

    assert appointment.id is not None
    assert appointment.organization_id == organization.id
    assert appointment.contact_id == contact.id
    assert appointment.scheduled_at == _at(10)
    assert appointment.duration_minutes == 30
    assert appointment.status == "scheduled"
    assert appointment.notes == "First cleaning"


async def test_book_enqueues_a_confirmation_notification_job(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )

    appointment = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
    )

    # Assert the job landed on the queue -- nothing consumes it yet (the
    # worker lands in a later issue), so this deliberately does not assert
    # that the job ran, only that it was enqueued with the right arguments.
    assert redis_queue.count == 1
    job = redis_queue.jobs[0]
    assert job.func_name == "app.workers.jobs.notify_appointment"
    assert job.args == (str(appointment.id),)
    assert job.kwargs == {"kind": "confirmation"}


async def test_book_rejects_a_slot_with_no_business_hours_configured(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    with pytest.raises(appointment_service.OutsideBusinessHoursError):
        await appointment_service.book(
            db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
        )
    assert redis_queue.count == 0


async def test_book_rejects_a_slot_starting_before_opening_time(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )

    with pytest.raises(appointment_service.OutsideBusinessHoursError):
        await appointment_service.book(
            db_session, organization.id, contact.id, scheduled_at=_at(8, 30), duration_minutes=30
        )
    assert redis_queue.count == 0


async def test_book_rejects_a_slot_ending_after_closing_time(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )

    with pytest.raises(appointment_service.OutsideBusinessHoursError):
        await appointment_service.book(
            db_session,
            organization.id,
            contact.id,
            scheduled_at=_at(16, 45),
            duration_minutes=30,
        )
    assert redis_queue.count == 0


async def test_book_rejects_a_conflicting_slot(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=60
    )
    redis_queue.empty()  # type: ignore[no-untyped-call]  # only care about the second booking

    with pytest.raises(appointment_service.AppointmentConflictError):
        await appointment_service.book(
            db_session,
            organization.id,
            contact.id,
            scheduled_at=_at(10, 30),
            duration_minutes=30,
        )
    assert redis_queue.count == 0


async def test_book_allows_back_to_back_non_overlapping_slots(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=60
    )

    second = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(11), duration_minutes=30
    )

    assert second.scheduled_at == _at(11)


async def test_book_treats_a_naive_scheduled_at_as_already_utc(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    naive_scheduled_at = datetime.combine(_TUESDAY, time(10, 0))
    assert naive_scheduled_at.tzinfo is None

    appointment = await appointment_service.book(
        db_session,
        organization.id,
        contact.id,
        scheduled_at=naive_scheduled_at,
        duration_minutes=30,
    )

    assert appointment.scheduled_at == _at(10)


async def test_book_rejects_non_positive_duration(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )

    with pytest.raises(ValueError, match="duration_minutes"):
        await appointment_service.book(
            db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=0
        )
    assert redis_queue.count == 0


# --- list_appointments -------------------------------------------------------


async def test_list_appointments_is_scoped_per_organization_and_sorted(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_org(db_session)
    org_b = await _make_org(db_session)
    contact_a = await _make_contact(db_session, org_a.id)
    contact_b = await _make_contact(db_session, org_b.id)
    await appointment_service.set_business_hours(
        db_session, org_a.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    await appointment_service.set_business_hours(
        db_session, org_b.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    later = await appointment_service.book(
        db_session, org_a.id, contact_a.id, scheduled_at=_at(14), duration_minutes=30
    )
    earlier = await appointment_service.book(
        db_session, org_a.id, contact_a.id, scheduled_at=_at(10), duration_minutes=30
    )
    await appointment_service.book(
        db_session, org_b.id, contact_b.id, scheduled_at=_at(9), duration_minutes=30
    )

    appointments = await appointment_service.list_appointments(db_session, org_a.id)

    assert [a.id for a in appointments] == [earlier.id, later.id]


async def test_list_appointments_filters_by_date(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=2, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    tuesday_appointment = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
    )
    await appointment_service.book(
        db_session,
        organization.id,
        contact.id,
        scheduled_at=_at(10, day=_TUESDAY + timedelta(days=1)),
        duration_minutes=30,
    )

    appointments = await appointment_service.list_appointments(
        db_session, organization.id, on_date=_TUESDAY
    )

    assert [a.id for a in appointments] == [tuesday_appointment.id]


# --- update_appointment -------------------------------------------------------


async def test_update_appointment_changes_notes_without_touching_schedule(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    appointment = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
    )

    updated = await appointment_service.update_appointment(
        db_session, organization.id, appointment.id, {"notes": "Patient requested reminder call"}
    )

    assert updated.notes == "Patient requested reminder call"
    assert updated.scheduled_at == _at(10)


async def test_update_appointment_cancelling_frees_the_slot_for_rebooking(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    appointment = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
    )

    cancelled = await appointment_service.update_appointment(
        db_session, organization.id, appointment.id, {"status": "cancelled"}
    )
    assert cancelled.status == "cancelled"

    rebooked = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
    )
    assert rebooked.id != appointment.id


async def test_update_appointment_rejects_invalid_status(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    appointment = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
    )

    with pytest.raises(ValueError, match="status"):
        await appointment_service.update_appointment(
            db_session, organization.id, appointment.id, {"status": "not-a-real-status"}
        )


async def test_update_appointment_reschedule_revalidates_business_hours(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    appointment = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
    )

    with pytest.raises(appointment_service.OutsideBusinessHoursError):
        await appointment_service.update_appointment(
            db_session, organization.id, appointment.id, {"scheduled_at": _at(18)}
        )


async def test_update_appointment_reschedule_does_not_conflict_with_itself(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    appointment = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
    )

    updated = await appointment_service.update_appointment(
        db_session, organization.id, appointment.id, {"scheduled_at": _at(11)}
    )

    assert updated.scheduled_at == _at(11)


async def test_update_appointment_reschedule_rejects_conflict_with_another_appointment(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    first = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(10), duration_minutes=30
    )
    second = await appointment_service.book(
        db_session, organization.id, contact.id, scheduled_at=_at(14), duration_minutes=30
    )

    with pytest.raises(appointment_service.AppointmentConflictError):
        await appointment_service.update_appointment(
            db_session, organization.id, second.id, {"scheduled_at": first.scheduled_at}
        )


async def test_update_appointment_unknown_id_raises_not_found(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)

    with pytest.raises(appointment_service.AppointmentNotFoundError):
        await appointment_service.update_appointment(
            db_session, organization.id, uuid.uuid4(), {"notes": "x"}
        )


async def test_update_appointment_belonging_to_another_organization_raises_not_found(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_org(db_session)
    org_b = await _make_org(db_session)
    contact_b = await _make_contact(db_session, org_b.id)
    await appointment_service.set_business_hours(
        db_session, org_b.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    appointment = await appointment_service.book(
        db_session, org_b.id, contact_b.id, scheduled_at=_at(10), duration_minutes=30
    )

    with pytest.raises(appointment_service.AppointmentNotFoundError):
        await appointment_service.update_appointment(
            db_session, org_a.id, appointment.id, {"notes": "x"}
        )


# --- interval helpers (pure functions, no DB needed) --------------------------


def test_merge_intervals_of_empty_list_is_empty() -> None:
    assert appointment_service._merge_intervals([]) == []


def test_merge_intervals_merges_adjacent_and_overlapping_but_not_disjoint() -> None:
    a = _at(9)
    b = _at(10)
    c = _at(10, 30)
    d = _at(11)
    e = _at(13)
    f = _at(14)

    merged = appointment_service._merge_intervals(
        [(a, b), (b, c), (d, e), (e, f)]  # [9-10, 10-10:30] touch; [11-13, 13-14] touch
    )

    assert merged == [(a, c), (d, f)]


def test_subtract_intervals_with_no_busy_time_returns_the_whole_window() -> None:
    window = (_at(9), _at(17))

    free = appointment_service._subtract_intervals(window, [])

    assert free == [window]


def test_subtract_intervals_ignores_a_busy_block_entirely_outside_the_window() -> None:
    """Direct unit test of the defensive clipping in ``_subtract_intervals``:
    a busy interval that doesn't overlap the window at all (e.g. business
    hours narrowed after the appointment was booked, see
    test_check_availability_ignores_an_appointment_left_outside_narrowed_hours
    for the same scenario exercised through check_availability) must not
    shrink the window.
    """
    window = (_at(9), _at(17))
    entirely_before = (_at(6), _at(8))
    entirely_after = (_at(18), _at(19))

    free = appointment_service._subtract_intervals(window, [entirely_before, entirely_after])

    assert free == [window]


def test_subtract_intervals_clips_a_busy_block_that_straddles_the_window_edge() -> None:
    window = (_at(9), _at(17))
    straddles_start = (_at(8), _at(10))
    straddles_end = (_at(16), _at(18))

    free = appointment_service._subtract_intervals(window, [straddles_start, straddles_end])

    assert free == [(_at(10), _at(16))]
