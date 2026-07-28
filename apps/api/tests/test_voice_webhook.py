"""Integration tests for POST /v1/webhooks/voice/incoming and
POST /v1/webhooks/voice/respond.

Same approach as tests/test_sms_webhook.py: genuinely, validly-signed
Twilio POST requests (signed the same way tests/test_twilio_service.py signs
one) against the real ASGI app, backed by a real (migrated) Postgres
database via the db_session fixture (see conftest.py). Asserts the
Contact/Conversation/Message rows the handlers are supposed to create, the
TwiML response shape, the 404-for-unrecognized-number case, and a full
incoming-then-respond call flow.
"""

import base64
import hashlib
import hmac
import xml.etree.ElementTree as ET
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.main import app
from app.models import Contact, Conversation, Message, Organization, PhoneNumber

TEST_AUTH_TOKEN = "test-auth-token-for-ci"
INCOMING_URL = "http://test/v1/webhooks/voice/incoming"
RESPOND_URL = "http://test/v1/webhooks/voice/respond"


def _sign(url: str, params: dict[str, str], auth_token: str = TEST_AUTH_TOKEN) -> str:
    """Independently compute a Twilio request signature the way Twilio does:
    HMAC-SHA1 of the URL followed by each sorted param name+value,
    concatenated, base64-encoded. Mirrors tests/test_twilio_service.py.
    """
    data = url
    for key in sorted(params):
        data += key + params[key]
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


@pytest.fixture
def twilio_auth_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    return TEST_AUTH_TOKEN


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An AsyncClient bound to the real app, with the request-scoped DB
    session dependency overridden to the test's transactional db_session so
    the assertions below and the handlers' writes share one connection (and
    the whole thing rolls back after the test, per conftest.py).
    """

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as async_client:
            yield async_client
    finally:
        app.dependency_overrides.clear()


async def _make_org_with_number(db_session: AsyncSession, e164_number: str) -> Organization:
    organization = Organization(name="Riverside Dental")
    db_session.add(organization)
    await db_session.flush()
    db_session.add(PhoneNumber(organization_id=organization.id, e164_number=e164_number))
    await db_session.flush()
    return organization


# --- POST /v1/webhooks/voice/incoming --------------------------------------


async def test_valid_incoming_call_creates_contact_and_open_conversation(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")
    params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": "CA1234567890",
    }
    signature = _sign(INCOMING_URL, params, auth_token=twilio_auth_token)

    response = await client.post(
        "/v1/webhooks/voice/incoming",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")

    root = ET.fromstring(response.text)
    assert root.tag == "Response"
    gather = root.find("Gather")
    assert gather is not None
    assert gather.get("input") == "speech"
    assert gather.get("action") == "http://test/v1/webhooks/voice/respond"
    say = gather.find("Say")
    assert say is not None
    assert say.text

    contact = (
        await db_session.execute(
            select(Contact).where(
                Contact.organization_id == organization.id,
                Contact.e164_number == "+15551234567",
            )
        )
    ).scalar_one()

    conversation = (
        await db_session.execute(
            select(Conversation).where(Conversation.contact_id == contact.id)
        )
    ).scalar_one()
    assert conversation.organization_id == organization.id
    assert conversation.channel == "voice"
    assert conversation.status == "open"

    messages = (
        await db_session.execute(
            select(Message).where(Message.conversation_id == conversation.id)
        )
    ).scalars().all()
    assert messages == []


async def test_incoming_call_unrecognized_to_number_returns_404_and_persists_nothing(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    params = {
        "To": "+19995550000",
        "From": "+15551234567",
        "CallSid": "CA9999999999",
    }
    signature = _sign(INCOMING_URL, params, auth_token=twilio_auth_token)

    response = await client.post(
        "/v1/webhooks/voice/incoming",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 404

    contacts = (
        await db_session.execute(
            select(Contact).where(Contact.e164_number == "+15551234567")
        )
    ).scalars().all()
    assert contacts == []


async def test_incoming_call_missing_signature_is_rejected_before_any_db_work(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    await _make_org_with_number(db_session, "+15005550006")
    params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": "CA0000000000",
    }

    response = await client.post("/v1/webhooks/voice/incoming", data=params)

    assert response.status_code == 403
    contacts = (
        await db_session.execute(
            select(Contact).where(Contact.e164_number == "+15551234567")
        )
    ).scalars().all()
    assert contacts == []


# --- POST /v1/webhooks/voice/respond ----------------------------------------


async def test_valid_respond_persists_caller_turn_and_reply_then_gathers_again(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")
    params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": "CA1234567890",
        "SpeechResult": "Can I book a cleaning for tomorrow?",
    }
    signature = _sign(RESPOND_URL, params, auth_token=twilio_auth_token)

    response = await client.post(
        "/v1/webhooks/voice/respond",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")

    root = ET.fromstring(response.text)
    assert root.tag == "Response"
    children = list(root)
    assert [child.tag for child in children] == ["Say", "Gather"]
    assert children[0].text
    gather = children[1]
    assert gather.get("input") == "speech"
    assert gather.get("action") == "http://test/v1/webhooks/voice/respond"

    contact = (
        await db_session.execute(
            select(Contact).where(
                Contact.organization_id == organization.id,
                Contact.e164_number == "+15551234567",
            )
        )
    ).scalar_one()

    conversation = (
        await db_session.execute(
            select(Conversation).where(Conversation.contact_id == contact.id)
        )
    ).scalar_one()
    assert conversation.channel == "voice"
    assert conversation.status == "open"

    messages = (
        await db_session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
        )
    ).scalars().all()
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Can I book a cleaning for tomorrow?"
    assert messages[0].twilio_sid == "CA1234567890"
    assert messages[1].role == "assistant"
    assert messages[1].content


async def test_respond_unrecognized_to_number_returns_404_and_persists_nothing(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    params = {
        "To": "+19995550000",
        "From": "+15551234567",
        "CallSid": "CA9999999999",
        "SpeechResult": "Hello?",
    }
    signature = _sign(RESPOND_URL, params, auth_token=twilio_auth_token)

    response = await client.post(
        "/v1/webhooks/voice/respond",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 404

    contacts = (
        await db_session.execute(
            select(Contact).where(Contact.e164_number == "+15551234567")
        )
    ).scalars().all()
    assert contacts == []


async def test_respond_missing_signature_is_rejected_before_any_db_work(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    await _make_org_with_number(db_session, "+15005550006")
    params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": "CA0000000000",
        "SpeechResult": "Hello?",
    }

    response = await client.post("/v1/webhooks/voice/respond", data=params)

    assert response.status_code == 403
    contacts = (
        await db_session.execute(
            select(Contact).where(Contact.e164_number == "+15551234567")
        )
    ).scalars().all()
    assert contacts == []


# --- full call flow ----------------------------------------------------------


async def test_full_incoming_then_respond_flow_reuses_same_conversation(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")
    call_sid = "CA1111111111"

    incoming_params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": call_sid,
    }
    incoming_signature = _sign(INCOMING_URL, incoming_params, auth_token=twilio_auth_token)
    incoming_response = await client.post(
        "/v1/webhooks/voice/incoming",
        data=incoming_params,
        headers={"X-Twilio-Signature": incoming_signature},
    )
    assert incoming_response.status_code == 200

    first_respond_params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": call_sid,
        "SpeechResult": "I'd like to book an appointment.",
    }
    first_signature = _sign(RESPOND_URL, first_respond_params, auth_token=twilio_auth_token)
    first_response = await client.post(
        "/v1/webhooks/voice/respond",
        data=first_respond_params,
        headers={"X-Twilio-Signature": first_signature},
    )
    assert first_response.status_code == 200

    second_respond_params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": call_sid,
        "SpeechResult": "Tomorrow afternoon works.",
    }
    second_signature = _sign(RESPOND_URL, second_respond_params, auth_token=twilio_auth_token)
    second_response = await client.post(
        "/v1/webhooks/voice/respond",
        data=second_respond_params,
        headers={"X-Twilio-Signature": second_signature},
    )
    assert second_response.status_code == 200

    contacts = (
        await db_session.execute(
            select(Contact).where(
                Contact.organization_id == organization.id,
                Contact.e164_number == "+15551234567",
            )
        )
    ).scalars().all()
    assert len(contacts) == 1

    conversations = (
        await db_session.execute(
            select(Conversation).where(Conversation.contact_id == contacts[0].id)
        )
    ).scalars().all()
    assert len(conversations) == 1
    assert conversations[0].channel == "voice"

    messages = (
        await db_session.execute(
            select(Message).where(Message.conversation_id == conversations[0].id)
        )
    ).scalars().all()
    # Two respond turns, each persisting a caller turn plus a placeholder reply.
    assert len(messages) == 4
    user_contents = {m.content for m in messages if m.role == "user"}
    assert user_contents == {"I'd like to book an appointment.", "Tomorrow afternoon works."}
