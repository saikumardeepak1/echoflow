"""Tests for app.services.analytics_service.

Runs against a real (migrated) Postgres database (see conftest.py).
Conversations, messages, and appointments are seeded directly via the ORM
(rather than through the webhook/booking endpoints) since these tests are
about the aggregation math, not about how the underlying rows got there;
tests/test_analytics_api.py covers the full HTTP path including session
auth and org scoping.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Contact, Conversation, Message, Organization
from app.services import analytics_service

_RANGE_START = date(2026, 7, 1)
_RANGE_END = date(2026, 7, 31)
_IN_RANGE = datetime(2026, 7, 15, tzinfo=UTC)
_BEFORE_RANGE = datetime(2026, 6, 1, tzinfo=UTC)
_AFTER_RANGE = datetime(2026, 8, 1, tzinfo=UTC)


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


async def _make_conversation(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    channel: str,
    created_at: datetime,
    message_count: int = 0,
) -> Conversation:
    conversation = Conversation(
        organization_id=organization_id,
        contact_id=contact_id,
        channel=channel,
        status="closed",
    )
    db_session.add(conversation)
    await db_session.flush()
    conversation.created_at = created_at
    for i in range(message_count):
        db_session.add(
            Message(
                conversation_id=conversation.id,
                role="user" if i % 2 == 0 else "assistant",
                content=f"message {i}",
            )
        )
    await db_session.flush()
    return conversation


async def _make_appointment(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    created_at: datetime,
) -> Appointment:
    appointment = Appointment(
        organization_id=organization_id,
        contact_id=contact_id,
        scheduled_at=created_at + timedelta(days=1),
        duration_minutes=30,
        status="scheduled",
    )
    db_session.add(appointment)
    await db_session.flush()
    appointment.created_at = created_at
    await db_session.flush()
    return appointment


async def test_get_overview_counts_call_and_sms_volume_separately(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    await _make_conversation(db_session, organization.id, contact.id, "voice", _IN_RANGE)
    await _make_conversation(db_session, organization.id, contact.id, "voice", _IN_RANGE)
    await _make_conversation(db_session, organization.id, contact.id, "voice", _IN_RANGE)
    await _make_conversation(db_session, organization.id, contact.id, "sms", _IN_RANGE)
    await _make_conversation(db_session, organization.id, contact.id, "sms", _IN_RANGE)

    overview = await analytics_service.get_overview(
        db_session, organization.id, _RANGE_START, _RANGE_END
    )

    assert overview.call_volume == 3
    assert overview.sms_volume == 2


async def test_get_overview_excludes_conversations_outside_the_date_range(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    await _make_conversation(db_session, organization.id, contact.id, "voice", _IN_RANGE)
    await _make_conversation(db_session, organization.id, contact.id, "voice", _BEFORE_RANGE)
    await _make_conversation(db_session, organization.id, contact.id, "voice", _AFTER_RANGE)

    overview = await analytics_service.get_overview(
        db_session, organization.id, _RANGE_START, _RANGE_END
    )

    assert overview.call_volume == 1


async def test_get_overview_is_scoped_per_organization(db_session: AsyncSession) -> None:
    organization_a = await _make_org(db_session)
    organization_b = await _make_org(db_session)
    contact_a = await _make_contact(db_session, organization_a.id)
    contact_b = await _make_contact(db_session, organization_b.id)

    await _make_conversation(db_session, organization_a.id, contact_a.id, "voice", _IN_RANGE)
    await _make_conversation(db_session, organization_b.id, contact_b.id, "voice", _IN_RANGE)
    await _make_conversation(db_session, organization_b.id, contact_b.id, "voice", _IN_RANGE)

    overview_a = await analytics_service.get_overview(
        db_session, organization_a.id, _RANGE_START, _RANGE_END
    )
    overview_b = await analytics_service.get_overview(
        db_session, organization_b.id, _RANGE_START, _RANGE_END
    )

    assert overview_a.call_volume == 1
    assert overview_b.call_volume == 2


async def test_get_overview_counts_appointments_booked_in_range_by_created_at(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    await _make_appointment(db_session, organization.id, contact.id, _IN_RANGE)
    await _make_appointment(db_session, organization.id, contact.id, _IN_RANGE)
    await _make_appointment(db_session, organization.id, contact.id, _BEFORE_RANGE)

    overview = await analytics_service.get_overview(
        db_session, organization.id, _RANGE_START, _RANGE_END
    )

    assert overview.appointments_booked == 2


async def test_get_overview_computes_average_conversation_length_by_message_count(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    await _make_conversation(
        db_session, organization.id, contact.id, "sms", _IN_RANGE, message_count=4
    )
    await _make_conversation(
        db_session, organization.id, contact.id, "sms", _IN_RANGE, message_count=2
    )
    await _make_conversation(
        db_session, organization.id, contact.id, "voice", _IN_RANGE, message_count=0
    )

    overview = await analytics_service.get_overview(
        db_session, organization.id, _RANGE_START, _RANGE_END
    )

    # (4 + 2 + 0) messages across 3 conversations.
    assert overview.average_conversation_length == pytest.approx(2.0)


async def test_get_overview_average_conversation_length_ignores_out_of_range_conversations(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    await _make_conversation(
        db_session, organization.id, contact.id, "sms", _IN_RANGE, message_count=6
    )
    await _make_conversation(
        db_session, organization.id, contact.id, "sms", _BEFORE_RANGE, message_count=100
    )

    overview = await analytics_service.get_overview(
        db_session, organization.id, _RANGE_START, _RANGE_END
    )

    assert overview.average_conversation_length == pytest.approx(6.0)


async def test_get_overview_returns_zero_metrics_when_no_data_in_range(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)

    overview = await analytics_service.get_overview(
        db_session, organization.id, _RANGE_START, _RANGE_END
    )

    assert overview.call_volume == 0
    assert overview.sms_volume == 0
    assert overview.appointments_booked == 0
    assert overview.average_conversation_length == 0.0


async def test_get_overview_boundaries_are_inclusive_of_start_and_end_date(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    start_of_range = datetime.combine(_RANGE_START, datetime.min.time(), tzinfo=UTC)
    end_of_range = datetime.combine(_RANGE_END, datetime.min.time(), tzinfo=UTC) + timedelta(
        hours=23, minutes=59
    )

    await _make_conversation(db_session, organization.id, contact.id, "voice", start_of_range)
    await _make_conversation(db_session, organization.id, contact.id, "sms", end_of_range)

    overview = await analytics_service.get_overview(
        db_session, organization.id, _RANGE_START, _RANGE_END
    )

    assert overview.call_volume == 1
    assert overview.sms_volume == 1


async def test_get_overview_issues_a_constant_number_of_queries_at_scale(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seeds a couple hundred conversations/messages/appointments and asserts
    ``get_overview`` still issues the same fixed handful of SQL statements it
    would for a single row, proving it aggregates in SQL rather than looping
    over rows in Python. Issue #19's acceptance criteria calls this out
    explicitly: reasonable performance at a few thousand conversations, a
    single aggregate query per metric, not N+1.
    """
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    for i in range(250):
        channel = "voice" if i % 2 == 0 else "sms"
        await _make_conversation(
            db_session, organization.id, contact.id, channel, _IN_RANGE, message_count=3
        )
    for _ in range(20):
        await _make_appointment(db_session, organization.id, contact.id, _IN_RANGE)

    original_execute = AsyncSession.execute
    call_count = 0

    async def counting_execute(self: AsyncSession, *args: object, **kwargs: object) -> object:
        nonlocal call_count
        call_count += 1
        return await original_execute(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(AsyncSession, "execute", counting_execute)

    overview = await analytics_service.get_overview(
        db_session, organization.id, _RANGE_START, _RANGE_END
    )

    assert call_count == 3
    assert overview.call_volume == 125
    assert overview.sms_volume == 125
    assert overview.appointments_booked == 20
    assert overview.average_conversation_length == pytest.approx(3.0)
