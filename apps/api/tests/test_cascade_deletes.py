"""Tests for the ON DELETE CASCADE behavior wired into the schema.

Every tenant-scoped table's organization_id FK cascades, so deleting an
Organization removes the entire tenant's data in one statement (no manual
per-table cleanup required, and no orphaned rows left behind). The same
cascade chain applies one level down: deleting a Contact removes its
Conversations/Orders/Appointments, deleting a Conversation removes its
Messages, deleting an Appointment removes its Notifications, and deleting a
User removes its RefreshTokens.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ApiKey,
    Appointment,
    Contact,
    Conversation,
    KnowledgeDocument,
    Message,
    Notification,
    Order,
    Organization,
    PhoneNumber,
    RefreshToken,
    User,
)


async def test_deleting_organization_cascades_to_every_tenant_scoped_table(
    db_session: AsyncSession,
) -> None:
    organization = Organization(name="Cascade Test Co")
    db_session.add(organization)
    await db_session.flush()

    user = User(
        organization_id=organization.id,
        email="owner@cascade-test.example",
        hashed_password="not-a-real-hash",
        role="admin",
    )
    api_key = ApiKey(
        organization_id=organization.id,
        prefix="efl_live_cd01",
        hashed_key="hashed-secret-value",
    )
    phone_number = PhoneNumber(organization_id=organization.id, e164_number="+15550001111")
    knowledge_document = KnowledgeDocument(
        organization_id=organization.id, title="FAQ", content="We accept walk-ins."
    )
    contact = Contact(organization_id=organization.id, e164_number="+15550002222")
    db_session.add_all([user, api_key, phone_number, knowledge_document, contact])
    await db_session.flush()

    refresh_token = RefreshToken(
        user_id=user.id,
        hashed_token="hashed-refresh-token-value",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )
    db_session.add(refresh_token)
    await db_session.flush()

    conversation = Conversation(
        organization_id=organization.id, contact_id=contact.id, channel="voice", status="open"
    )
    order = Order(
        organization_id=organization.id,
        contact_id=contact.id,
        order_number="ORD-CASCADE-1",
        status="pending",
        item_description="Widget",
    )
    appointment = Appointment(
        organization_id=organization.id,
        contact_id=contact.id,
        scheduled_at=datetime.now(UTC) + timedelta(days=1),
        duration_minutes=15,
        status="scheduled",
    )
    db_session.add_all([conversation, order, appointment])
    await db_session.flush()

    message = Message(conversation_id=conversation.id, role="assistant", content="Hello!")
    notification = Notification(
        appointment_id=appointment.id,
        kind="confirmation",
        status="pending",
        send_at=datetime.now(UTC),
    )
    db_session.add_all([message, notification])
    await db_session.commit()

    organization_id = organization.id
    contact_id = contact.id
    conversation_id = conversation.id
    appointment_id = appointment.id
    user_id = user.id

    await db_session.delete(organization)
    await db_session.commit()

    assert (
        await db_session.execute(select(Organization).where(Organization.id == organization_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(User).where(User.organization_id == organization_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(ApiKey).where(ApiKey.organization_id == organization_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(PhoneNumber).where(PhoneNumber.organization_id == organization_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.organization_id == organization_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(Contact).where(Contact.id == contact_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(Order).where(Order.organization_id == organization_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(Appointment).where(Appointment.id == appointment_id))
    ).scalar_one_or_none() is None

    # Second-order cascade: deleting the Contact/Conversation/Appointment/User
    # (via the Organization delete) also removed their children.
    assert (
        await db_session.execute(select(RefreshToken).where(RefreshToken.user_id == user_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(Message).where(Message.conversation_id == conversation_id)
        )
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(
            select(Notification).where(Notification.appointment_id == appointment_id)
        )
    ).scalar_one_or_none() is None


async def test_deleting_contact_cascades_to_conversations_orders_and_appointments(
    db_session: AsyncSession,
) -> None:
    organization = Organization(name="Contact Cascade Co")
    db_session.add(organization)
    await db_session.flush()

    contact = Contact(organization_id=organization.id, e164_number="+15550003333")
    db_session.add(contact)
    await db_session.flush()

    conversation = Conversation(
        organization_id=organization.id, contact_id=contact.id, channel="sms", status="open"
    )
    order = Order(
        organization_id=organization.id,
        contact_id=contact.id,
        order_number="ORD-CASCADE-2",
        status="pending",
        item_description="Widget",
    )
    appointment = Appointment(
        organization_id=organization.id,
        contact_id=contact.id,
        scheduled_at=datetime.now(UTC) + timedelta(days=1),
        duration_minutes=15,
        status="scheduled",
    )
    db_session.add_all([conversation, order, appointment])
    await db_session.commit()

    conversation_id, order_id, appointment_id = conversation.id, order.id, appointment.id

    await db_session.delete(contact)
    await db_session.commit()

    assert (
        await db_session.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(Order).where(Order.id == order_id))
    ).scalar_one_or_none() is None
    assert (
        await db_session.execute(select(Appointment).where(Appointment.id == appointment_id))
    ).scalar_one_or_none() is None


async def test_deleting_appointment_cascades_to_notifications(db_session: AsyncSession) -> None:
    organization = Organization(name="Notification Cascade Co")
    db_session.add(organization)
    await db_session.flush()

    contact = Contact(organization_id=organization.id, e164_number="+15550004444")
    db_session.add(contact)
    await db_session.flush()

    appointment = Appointment(
        organization_id=organization.id,
        contact_id=contact.id,
        scheduled_at=datetime.now(UTC) + timedelta(days=1),
        duration_minutes=15,
        status="scheduled",
    )
    db_session.add(appointment)
    await db_session.flush()

    notification = Notification(
        appointment_id=appointment.id,
        kind="reminder",
        status="pending",
        send_at=datetime.now(UTC),
    )
    db_session.add(notification)
    await db_session.commit()

    notification_id = notification.id

    await db_session.delete(appointment)
    await db_session.commit()

    assert (
        await db_session.execute(select(Notification).where(Notification.id == notification_id))
    ).scalar_one_or_none() is None
