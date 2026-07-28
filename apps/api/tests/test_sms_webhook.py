"""Integration tests for POST /v1/webhooks/sms/incoming.

Runs the full webhook end to end: a genuinely, validly-signed Twilio POST
request (signed the same way tests/test_twilio_service.py signs one) against
the real ASGI app, backed by a real (migrated) Postgres database via the
db_session fixture (see conftest.py). Asserts the Contact/Conversation/
Message rows the handler is supposed to create, scoped to the right
organization, actually land in the database, plus the TwiML response shape
and the 404-for-unrecognized-number case.

The agent's Gemini calls are mocked exactly the way tests/test_agent_graph.py
mocks them (`gemini_client.generate_content` monkeypatched to a scripted
sequence of canned responses): these tests need no GEMINI_API_KEY and no
network access, and assert the webhook's TwiML reply and persisted
`assistant` `Message` rows are the agent's real generated text, not the
static placeholder the handler used before this module was wired up to
app.agent.graph.run_agent_turn.
"""

import base64
import hashlib
import hmac
import xml.etree.ElementTree as ET
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from google.genai import types
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import gemini_client
from app.agent import tools as agent_tools
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


class _StubGenerateContent:
    """A callable returning successive canned `GenerateContentResponse`
    values on each call. Mirrors tests/test_agent_graph.py's helper of the
    same name.
    """

    def __init__(self, *responses: types.GenerateContentResponse) -> None:
        self._responses = iter(responses)

    def __call__(self, **kwargs: Any) -> types.GenerateContentResponse:
        return next(self._responses)


def _text_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))
        ]
    )


def _function_call_response(*calls: tuple[str, dict[str, Any]]) -> types.GenerateContentResponse:
    parts = [
        types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in calls
    ]
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


def _mock_gemini(
    monkeypatch: pytest.MonkeyPatch, *responses: types.GenerateContentResponse
) -> None:
    monkeypatch.setattr(gemini_client, "generate_content", _StubGenerateContent(*responses))


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


async def test_valid_sms_webhook_returns_the_agents_real_reply(
    client: AsyncClient,
    db_session: AsyncSession,
    twilio_auth_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_gemini(
        monkeypatch,
        _text_response("We have a 2pm slot open tomorrow, would that work?"),
    )
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
    # The real agent-generated reply, not the old static placeholder.
    assert message_verb.text == "We have a 2pm slot open tomorrow, would that work?"

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
    assert messages[0].twilio_sid == "SM1234567890"
    assert messages[1].role == "assistant"
    assert messages[1].content == "We have a 2pm slot open tomorrow, would that work?"


async def test_multi_turn_sms_conversation_with_a_tool_call_then_a_plain_reply(
    client: AsyncClient,
    db_session: AsyncSession,
    twilio_auth_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full two-message SMS conversation: the first turn's reply comes
    straight from a plain-text Gemini response, the second turn's reply
    only comes after the model calls a tool first (the scripted
    tool-call-then-plain-response sequence the agent graph tests use),
    exercising `run_agent_turn`'s multi-round loop through the webhook
    rather than calling the graph directly.
    """
    organization = await _make_org_with_number(db_session, "+15005550006")

    async def _send(body: str, message_sid: str) -> str:
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
        message_verb = ET.fromstring(response.text).find("Message")
        assert message_verb is not None and message_verb.text
        return message_verb.text

    _mock_gemini(monkeypatch, _text_response("Sure, are you open Saturday? Yes, 9 to 5."))
    first_reply = await _send("Are you open Saturday?", "SM1111111111")
    assert first_reply == "Sure, are you open Saturday? Yes, 9 to 5."

    _mock_gemini(
        monkeypatch,
        _function_call_response(("check_availability", {"date": "2026-08-03"})),
        _text_response("We have 10am and 2pm open Saturday, which works better?"),
    )
    execute_tool_mock = AsyncMock(
        return_value={"slots": [{"start": "2026-08-03T10:00:00", "end": "2026-08-03T10:30:00"}]}
    )
    monkeypatch.setattr(agent_tools, "execute_tool", execute_tool_mock)
    second_reply = await _send("Great, what time?", "SM2222222222")
    assert second_reply == "We have 10am and 2pm open Saturday, which works better?"
    execute_tool_mock.assert_awaited_once()

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
            select(Message)
            .where(Message.conversation_id == conversations[0].id)
            .order_by(Message.created_at)
        )
    ).scalars().all()
    # Two inbound turns, each followed by a persisted assistant reply.
    assert [message.role for message in messages] == ["user", "assistant", "user", "assistant"]
    assert [message.content for message in messages] == [
        "Are you open Saturday?",
        "Sure, are you open Saturday? Yes, 9 to 5.",
        "Great, what time?",
        "We have 10am and 2pm open Saturday, which works better?",
    ]


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
