"""Tests for app.services.conversation_service.

Runs against a real (migrated) Postgres database, see conftest.py. Each
function is exercised directly against the ORM rather than through the SMS
webhook, since this service is meant to be reused by the voice webhook too
(see docs/TDD.md section 3.2); tests/test_sms_webhook.py covers the full
webhook-to-persistence path.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Message, Organization, PhoneNumber
from app.services.conversation_service import (
    find_or_create_contact,
    find_or_create_open_conversation,
    persist_message,
    resolve_organization_by_number,
)


async def _make_org_with_number(db_session: AsyncSession, e164_number: str) -> Organization:
    organization = Organization(name="Riverside Dental")
    db_session.add(organization)
    await db_session.flush()
    db_session.add(PhoneNumber(organization_id=organization.id, e164_number=e164_number))
    await db_session.flush()
    return organization


# --- resolve_organization_by_number -----------------------------------------


async def test_resolve_organization_by_number_finds_owning_organization(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")

    resolved = await resolve_organization_by_number(db_session, "+15005550006")

    assert resolved is not None
    assert resolved.id == organization.id


async def test_resolve_organization_by_number_returns_none_for_unrecognized_number(
    db_session: AsyncSession,
) -> None:
    resolved = await resolve_organization_by_number(db_session, "+19995550000")

    assert resolved is None


# --- find_or_create_contact --------------------------------------------------


async def test_find_or_create_contact_creates_a_new_contact(db_session: AsyncSession) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")

    contact = await find_or_create_contact(db_session, organization.id, "+15551234567")

    assert contact.id is not None
    assert contact.organization_id == organization.id
    assert contact.e164_number == "+15551234567"


async def test_find_or_create_contact_reuses_existing_contact(db_session: AsyncSession) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")

    first = await find_or_create_contact(db_session, organization.id, "+15551234567")
    second = await find_or_create_contact(db_session, organization.id, "+15551234567")

    assert first.id == second.id
    result = await db_session.execute(
        select(Contact).where(
            Contact.organization_id == organization.id,
            Contact.e164_number == "+15551234567",
        )
    )
    assert len(result.scalars().all()) == 1


async def test_find_or_create_contact_is_scoped_per_organization(
    db_session: AsyncSession,
) -> None:
    first_org = await _make_org_with_number(db_session, "+15005550006")
    second_org = await _make_org_with_number(db_session, "+15005550007")

    first_contact = await find_or_create_contact(db_session, first_org.id, "+15551234567")
    second_contact = await find_or_create_contact(db_session, second_org.id, "+15551234567")

    assert first_contact.id != second_contact.id
    assert first_contact.organization_id == first_org.id
    assert second_contact.organization_id == second_org.id


# --- find_or_create_open_conversation ----------------------------------------


async def test_find_or_create_open_conversation_creates_a_new_open_conversation(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")
    contact = await find_or_create_contact(db_session, organization.id, "+15551234567")

    conversation = await find_or_create_open_conversation(
        db_session, organization.id, contact.id, channel="sms"
    )

    assert conversation.id is not None
    assert conversation.status == "open"
    assert conversation.channel == "sms"
    assert conversation.contact_id == contact.id


async def test_find_or_create_open_conversation_reuses_open_conversation_on_same_channel(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")
    contact = await find_or_create_contact(db_session, organization.id, "+15551234567")

    first = await find_or_create_open_conversation(
        db_session, organization.id, contact.id, channel="sms"
    )
    second = await find_or_create_open_conversation(
        db_session, organization.id, contact.id, channel="sms"
    )

    assert first.id == second.id


async def test_find_or_create_open_conversation_does_not_reuse_a_closed_conversation(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")
    contact = await find_or_create_contact(db_session, organization.id, "+15551234567")

    closed = await find_or_create_open_conversation(
        db_session, organization.id, contact.id, channel="sms"
    )
    closed.status = "closed"
    await db_session.flush()

    reopened = await find_or_create_open_conversation(
        db_session, organization.id, contact.id, channel="sms"
    )

    assert reopened.id != closed.id
    assert reopened.status == "open"


async def test_find_or_create_open_conversation_is_independent_per_channel(
    db_session: AsyncSession,
) -> None:
    """A contact can have an open SMS conversation and an open voice
    conversation at the same time; this is what lets the future voice
    webhook reuse this function without colliding with an open SMS thread.
    """
    organization = await _make_org_with_number(db_session, "+15005550006")
    contact = await find_or_create_contact(db_session, organization.id, "+15551234567")

    sms_conversation = await find_or_create_open_conversation(
        db_session, organization.id, contact.id, channel="sms"
    )
    voice_conversation = await find_or_create_open_conversation(
        db_session, organization.id, contact.id, channel="voice"
    )

    assert sms_conversation.id != voice_conversation.id
    assert sms_conversation.channel == "sms"
    assert voice_conversation.channel == "voice"


# --- persist_message ----------------------------------------------------------


async def test_persist_message_creates_a_message_row(db_session: AsyncSession) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")
    contact = await find_or_create_contact(db_session, organization.id, "+15551234567")
    conversation = await find_or_create_open_conversation(
        db_session, organization.id, contact.id, channel="sms"
    )

    message = await persist_message(
        db_session,
        conversation.id,
        role="user",
        content="Can I book a cleaning for tomorrow?",
        twilio_sid="SM1234567890",
    )

    assert message.id is not None
    fetched = (
        await db_session.execute(select(Message).where(Message.id == message.id))
    ).scalar_one()
    assert fetched.conversation_id == conversation.id
    assert fetched.role == "user"
    assert fetched.content == "Can I book a cleaning for tomorrow?"
    assert fetched.twilio_sid == "SM1234567890"


async def test_persist_message_without_twilio_sid_leaves_it_null(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")
    contact = await find_or_create_contact(db_session, organization.id, "+15551234567")
    conversation = await find_or_create_open_conversation(
        db_session, organization.id, contact.id, channel="sms"
    )

    message = await persist_message(
        db_session, conversation.id, role="assistant", content="See you at 3pm."
    )

    assert message.twilio_sid is None
