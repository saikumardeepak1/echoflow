"""Integration tests for POST /v1/webhooks/sms/incoming.

Runs the full webhook end to end: a genuinely, validly-signed Twilio POST
request (signed the same way tests/test_twilio_service.py signs one) against
the real ASGI app, backed by a real (migrated) Postgres database via the
db_session fixture (see conftest.py). Asserts the Contact/Conversation/
Message rows the handler is supposed to create, scoped to the right
organization, actually land in the database, plus the TwiML response shape
and the 404-for-unrecognized-number case.
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
WEBHOOK_URL = "http://test/v1/webhooks/sms/incoming"


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
    the assertions below and the handler's writes share one connection (and
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


async def test_valid_sms_webhook_creates_contact_conversation_and_message(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")
    params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "Body": "Can I book a cleaning for tomorrow?",
        "MessageSid": "SM1234567890",
    }
    signature = _sign(WEBHOOK_URL, params, auth_token=twilio_auth_token)

    response = await client.post(
        "/v1/webhooks/sms/incoming",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")

    root = ET.fromstring(response.text)
    assert root.tag == "Response"
    message_verb = root.find("Message")
    assert message_verb is not None
    assert message_verb.text

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
    assert conversation.channel == "sms"
    assert conversation.status == "open"

    message = (
        await db_session.execute(
            select(Message).where(Message.conversation_id == conversation.id)
        )
    ).scalar_one()
    assert message.role == "user"
    assert message.content == "Can I book a cleaning for tomorrow?"
    assert message.twilio_sid == "SM1234567890"


async def test_second_message_from_same_contact_reuses_contact_and_open_conversation(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    organization = await _make_org_with_number(db_session, "+15005550006")

    async def _send(body: str, message_sid: str) -> None:
        params = {
            "To": "+15005550006",
            "From": "+15551234567",
            "Body": body,
            "MessageSid": message_sid,
        }
        signature = _sign(WEBHOOK_URL, params, auth_token=twilio_auth_token)
        response = await client.post(
            "/v1/webhooks/sms/incoming",
            data=params,
            headers={"X-Twilio-Signature": signature},
        )
        assert response.status_code == 200

    await _send("Hi, are you open Saturday?", "SM1111111111")
    await _send("Great, what time?", "SM2222222222")

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

    messages = (
        await db_session.execute(
            select(Message).where(Message.conversation_id == conversations[0].id)
        )
    ).scalars().all()
    assert len(messages) == 2
    assert {message.content for message in messages} == {
        "Hi, are you open Saturday?",
        "Great, what time?",
    }


async def test_unrecognized_to_number_returns_404_and_persists_nothing(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    params = {
        "To": "+19995550000",
        "From": "+15551234567",
        "Body": "Hello?",
        "MessageSid": "SM9999999999",
    }
    signature = _sign(WEBHOOK_URL, params, auth_token=twilio_auth_token)

    response = await client.post(
        "/v1/webhooks/sms/incoming",
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


async def test_missing_signature_is_rejected_before_any_db_work(
    client: AsyncClient, db_session: AsyncSession, twilio_auth_token: str
) -> None:
    await _make_org_with_number(db_session, "+15005550006")
    params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "Body": "Hello?",
        "MessageSid": "SM0000000000",
    }

    response = await client.post("/v1/webhooks/sms/incoming", data=params)

    assert response.status_code == 403
    contacts = (
        await db_session.execute(
            select(Contact).where(Contact.e164_number == "+15551234567")
        )
    ).scalars().all()
    assert contacts == []
