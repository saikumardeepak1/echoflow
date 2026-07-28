"""Integration tests for POST /v1/webhooks/voice/incoming and
POST /v1/webhooks/voice/respond.

Same approach as tests/test_sms_webhook.py: genuinely, validly-signed
Twilio POST requests (signed the same way tests/test_twilio_service.py signs
one) against the real ASGI app, backed by a real (migrated) Postgres
database via the db_session fixture (see conftest.py). Asserts the
Contact/Conversation/Message rows the handlers are supposed to create, the
TwiML response shape, the 404-for-unrecognized-number case, and a full
incoming-then-respond call flow.

`voice/respond`'s reply comes from the agent graph, so the same
`gemini_client.generate_content` mocking tests/test_agent_graph.py uses
covers it here too: these tests need no GEMINI_API_KEY and no network
access. The call-ending tests script Gemini into calling the `end_call` tool
(app/agent/tools.py) and assert the handler returns `<Hangup>` instead of
looping back into another `<Gather>`.
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


async def test_valid_respond_returns_the_agents_real_reply_then_gathers_again(
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
    # The real agent-generated reply, not the old static placeholder.
    assert children[0].text == "We have a 2pm slot open tomorrow, would that work?"
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
    assert messages[1].content == "We have a 2pm slot open tomorrow, would that work?"


async def test_respond_with_a_tool_call_then_a_plain_reply_still_gathers_again(
    client: AsyncClient,
    db_session: AsyncSession,
    twilio_auth_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scripted tool-call-then-plain-response sequence, this time on the
    voice channel: the model looks something up before it has a reply
    ready, and the turn still ends in a normal `<Say>`/`<Gather>` loop since
    it never called `end_call`.
    """
    _mock_gemini(
        monkeypatch,
        _function_call_response(("check_availability", {"date": "2026-08-03"})),
        _text_response("We have 10am and 2pm open Saturday, which works better?"),
    )
    execute_tool_mock = AsyncMock(
        return_value={"slots": [{"start": "2026-08-03T10:00:00", "end": "2026-08-03T10:30:00"}]}
    )
    monkeypatch.setattr(agent_tools, "execute_tool", execute_tool_mock)
    await _make_org_with_number(db_session, "+15005550006")
    params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": "CA1234567890",
        "SpeechResult": "What times are open Saturday?",
    }
    signature = _sign(RESPOND_URL, params, auth_token=twilio_auth_token)

    response = await client.post(
        "/v1/webhooks/voice/respond",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )

    assert response.status_code == 200
    root = ET.fromstring(response.text)
    children = list(root)
    assert [child.tag for child in children] == ["Say", "Gather"]
    assert children[0].text == "We have 10am and 2pm open Saturday, which works better?"
    execute_tool_mock.assert_awaited_once()


async def test_respond_returns_hangup_when_the_agent_signals_resolution(
    client: AsyncClient,
    db_session: AsyncSession,
    twilio_auth_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once the model calls `end_call`, the handler returns `<Hangup>`
    (speaking the agent's closing line first) instead of looping back into
    another `<Gather>`.
    """
    _mock_gemini(
        monkeypatch,
        _function_call_response(("end_call", {})),
        _text_response("Glad I could help. Have a great day!"),
    )
    execute_tool_mock = AsyncMock(return_value={"acknowledged": True})
    monkeypatch.setattr(agent_tools, "execute_tool", execute_tool_mock)
    organization = await _make_org_with_number(db_session, "+15005550006")
    params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": "CA1234567890",
        "SpeechResult": "That's everything, thanks!",
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
    assert [child.tag for child in children] == ["Say", "Hangup"]
    assert children[0].text == "Glad I could help. Have a great day!"

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
    messages = (
        await db_session.execute(
            select(Message)
            .where(Message.conversation_id == conversation.id)
            .order_by(Message.created_at)
        )
    ).scalars().all()
    assert len(messages) == 2
    assert messages[1].role == "assistant"
    assert messages[1].content == "Glad I could help. Have a great day!"


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
    client: AsyncClient,
    db_session: AsyncSession,
    twilio_auth_token: str,
    monkeypatch: pytest.MonkeyPatch,
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

    _mock_gemini(monkeypatch, _text_response("Sure, what time works for you?"))
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
    first_children = list(ET.fromstring(first_response.text))
    assert [child.tag for child in first_children] == ["Say", "Gather"]
    assert first_children[0].text == "Sure, what time works for you?"

    second_respond_params = {
        "To": "+15005550006",
        "From": "+15551234567",
        "CallSid": call_sid,
        "SpeechResult": "Tomorrow afternoon works.",
    }
    second_signature = _sign(RESPOND_URL, second_respond_params, auth_token=twilio_auth_token)

    _mock_gemini(
        monkeypatch,
        _function_call_response(
            (
                "book_appointment",
                {"scheduled_at": "2026-08-03T14:00:00", "duration_minutes": 30},
            ),
            ("end_call", {}),
        ),
        _text_response("You're booked for tomorrow at 2pm. See you then!"),
    )
    monkeypatch.setattr(
        agent_tools,
        "execute_tool",
        AsyncMock(side_effect=[{"booked": True}, {"acknowledged": True}]),
    )
    second_response = await client.post(
        "/v1/webhooks/voice/respond",
        data=second_respond_params,
        headers={"X-Twilio-Signature": second_signature},
    )
    assert second_response.status_code == 200
    second_children = list(ET.fromstring(second_response.text))
    # The agent booked the appointment and signalled resolution in the same
    # turn: the call ends with <Hangup> instead of another <Gather>.
    assert [child.tag for child in second_children] == ["Say", "Hangup"]
    assert second_children[0].text == "You're booked for tomorrow at 2pm. See you then!"

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
            select(Message)
            .where(Message.conversation_id == conversations[0].id)
            .order_by(Message.created_at)
        )
    ).scalars().all()
    # Two respond turns, each persisting a caller turn plus the agent's real reply.
    assert len(messages) == 4
    assert [message.role for message in messages] == ["user", "assistant", "user", "assistant"]
    assert [message.content for message in messages] == [
        "I'd like to book an appointment.",
        "Sure, what time works for you?",
        "Tomorrow afternoon works.",
        "You're booked for tomorrow at 2pm. See you then!",
    ]
