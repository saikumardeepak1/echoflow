"""Conversation persistence for inbound Twilio webhooks.

See docs/TDD.md section 3.2: this service resolves the owning organization
for an inbound webhook, finds-or-creates the `Contact` and `Conversation`
rows for it, and persists each turn as a `Message` row. It is deliberately
channel-agnostic (`channel` is a parameter, never hardcoded) so both the SMS
webhook (app/api/webhooks.py) and the voice webhook (a follow-up issue) share
this exact logic rather than duplicating it.

None of these functions call `session.commit()`; they `flush()` so
newly-created rows get their generated primary keys and are visible to
subsequent queries within the same session, but committing (and therefore
choosing the transaction boundary) is left to the caller.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.organization import Organization
from app.models.phone_number import PhoneNumber


async def resolve_organization_by_number(
    session: AsyncSession, to_number: str
) -> Organization | None:
    """Resolve the `Organization` that owns `to_number`.

    Looks up `PhoneNumber.e164_number`, per docs/TDD.md section 3.6: webhook
    handlers resolve the organization from the Twilio `To` number before
    touching any other tenant-scoped table, so one business's data is never
    reachable through another's phone number. Returns `None` if no
    `PhoneNumber` row matches, which the caller should treat as an
    unrecognized number (404).
    """
    result = await session.execute(
        select(Organization)
        .join(PhoneNumber, PhoneNumber.organization_id == Organization.id)
        .where(PhoneNumber.e164_number == to_number)
    )
    return result.scalar_one_or_none()


async def find_or_create_contact(
    session: AsyncSession, organization_id: uuid.UUID, from_number: str
) -> Contact:
    """Find the `Contact` for `from_number` within `organization_id`,
    creating one on first contact from that number (see
    docs/ARCHITECTURE.md request flows).
    """
    result = await session.execute(
        select(Contact).where(
            Contact.organization_id == organization_id,
            Contact.e164_number == from_number,
        )
    )
    contact = result.scalar_one_or_none()
    if contact is not None:
        return contact

    contact = Contact(organization_id=organization_id, e164_number=from_number)
    session.add(contact)
    await session.flush()
    return contact


async def find_or_create_open_conversation(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    channel: str,
) -> Conversation:
    """Find an already-open `Conversation` on `channel` for `contact_id`,
    creating one if none is open.

    `channel` is a plain parameter rather than hardcoded so this same
    function serves both the SMS webhook (`channel="sms"`) and the voice
    webhook that follows in a later issue (`channel="voice"`); a contact can
    have an open conversation on each channel independently.
    """
    result = await session.execute(
        select(Conversation).where(
            Conversation.organization_id == organization_id,
            Conversation.contact_id == contact_id,
            Conversation.channel == channel,
            Conversation.status == "open",
        )
    )
    conversation = result.scalar_one_or_none()
    if conversation is not None:
        return conversation

    conversation = Conversation(
        organization_id=organization_id,
        contact_id=contact_id,
        channel=channel,
        status="open",
    )
    session.add(conversation)
    await session.flush()
    return conversation


async def persist_message(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    twilio_sid: str | None = None,
) -> Message:
    """Persist one turn of a conversation.

    `role` is `"user"` for an inbound turn or `"assistant"` for a reply;
    `twilio_sid` is the Twilio call/message SID the turn originated from,
    when there is one.
    """
    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        twilio_sid=twilio_sid,
    )
    session.add(message)
    await session.flush()
    return message
