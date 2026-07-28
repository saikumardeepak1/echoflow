"""Integration test: insert one row per core table, following the full FK
chain from Organization down through Contact into Conversation/Message,
Order, and Appointment/Notification, and confirm every row round-trips
with the values it was written with.

Runs against a real (migrated) Postgres database, see conftest.py.
"""

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    ApiKey,
    Appointment,
    BusinessHours,
    Contact,
    Conversation,
    KnowledgeDocument,
    Message,
    Notification,
    Order,
    Organization,
    PhoneNumber,
    User,
)


async def test_full_fk_chain_round_trips(db_session: AsyncSession) -> None:
    organization = Organization(name="Riverside Dental")
    db_session.add(organization)
    await db_session.flush()

    user = User(
        organization_id=organization.id,
        email="front-desk@riverside.example",
        hashed_password="not-a-real-hash",
        role="admin",
    )
    api_key = ApiKey(
        organization_id=organization.id,
        prefix="efl_live_ab12",
        hashed_key="hashed-secret-value",
    )
    phone_number = PhoneNumber(
        organization_id=organization.id,
        e164_number="+15551234567",
    )
    knowledge_document = KnowledgeDocument(
        organization_id=organization.id,
        title="Office hours",
        content="We are open Monday through Friday, nine to five.",
    )
    business_hours = BusinessHours(
        organization_id=organization.id,
        day_of_week=0,
        opens_at=time(9, 0),
        closes_at=time(17, 0),
    )
    db_session.add_all([user, api_key, phone_number, knowledge_document, business_hours])
    await db_session.flush()

    contact = Contact(
        organization_id=organization.id,
        e164_number="+15559876543",
        display_name="Jamie Rivera",
    )
    db_session.add(contact)
    await db_session.flush()

    conversation = Conversation(
        organization_id=organization.id,
        contact_id=contact.id,
        channel="sms",
        status="open",
    )
    order = Order(
        organization_id=organization.id,
        contact_id=contact.id,
        order_number="ORD-1001",
        status="pending",
        item_description="Teeth whitening kit",
    )
    appointment = Appointment(
        organization_id=organization.id,
        contact_id=contact.id,
        scheduled_at=datetime.now(UTC) + timedelta(days=1),
        duration_minutes=30,
        status="scheduled",
        notes="First cleaning visit",
    )
    db_session.add_all([conversation, order, appointment])
    await db_session.flush()

    message = Message(
        conversation_id=conversation.id,
        role="user",
        content="Can I book a cleaning for tomorrow?",
        twilio_sid="SM1234567890",
    )
    notification = Notification(
        appointment_id=appointment.id,
        kind="confirmation",
        status="pending",
        send_at=datetime.now(UTC),
    )
    db_session.add_all([message, notification])
    await db_session.commit()

    # Re-fetch everything by primary key through fresh selects to confirm
    # the values actually persisted (not just held in the session identity map).
    fetched_org = (
        await db_session.execute(select(Organization).where(Organization.id == organization.id))
    ).scalar_one()
    assert fetched_org.name == "Riverside Dental"
    assert fetched_org.created_at is not None

    fetched_user = (await db_session.execute(select(User).where(User.id == user.id))).scalar_one()
    assert fetched_user.organization_id == organization.id
    assert fetched_user.email == "front-desk@riverside.example"

    fetched_key = (
        await db_session.execute(select(ApiKey).where(ApiKey.id == api_key.id))
    ).scalar_one()
    assert fetched_key.organization_id == organization.id
    assert fetched_key.revoked_at is None

    fetched_phone = (
        await db_session.execute(select(PhoneNumber).where(PhoneNumber.id == phone_number.id))
    ).scalar_one()
    assert fetched_phone.organization_id == organization.id
    assert fetched_phone.e164_number == "+15551234567"

    fetched_doc = (
        await db_session.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == knowledge_document.id)
        )
    ).scalar_one()
    assert fetched_doc.organization_id == organization.id
    assert fetched_doc.title == "Office hours"

    fetched_hours = (
        await db_session.execute(select(BusinessHours).where(BusinessHours.id == business_hours.id))
    ).scalar_one()
    assert fetched_hours.organization_id == organization.id
    assert fetched_hours.day_of_week == 0

    fetched_contact = (
        await db_session.execute(select(Contact).where(Contact.id == contact.id))
    ).scalar_one()
    assert fetched_contact.organization_id == organization.id
    assert fetched_contact.display_name == "Jamie Rivera"

    fetched_conversation = (
        await db_session.execute(select(Conversation).where(Conversation.id == conversation.id))
    ).scalar_one()
    assert fetched_conversation.organization_id == organization.id
    assert fetched_conversation.contact_id == contact.id
    assert fetched_conversation.channel == "sms"

    fetched_order = (
        await db_session.execute(select(Order).where(Order.id == order.id))
    ).scalar_one()
    assert fetched_order.organization_id == organization.id
    assert fetched_order.contact_id == contact.id
    assert fetched_order.order_number == "ORD-1001"

    fetched_appointment = (
        await db_session.execute(select(Appointment).where(Appointment.id == appointment.id))
    ).scalar_one()
    assert fetched_appointment.organization_id == organization.id
    assert fetched_appointment.contact_id == contact.id
    assert fetched_appointment.duration_minutes == 30

    fetched_message = (
        await db_session.execute(select(Message).where(Message.id == message.id))
    ).scalar_one()
    assert fetched_message.conversation_id == conversation.id
    assert fetched_message.role == "user"

    fetched_notification = (
        await db_session.execute(select(Notification).where(Notification.id == notification.id))
    ).scalar_one()
    assert fetched_notification.appointment_id == appointment.id
    assert fetched_notification.kind == "confirmation"
