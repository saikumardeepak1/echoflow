"""Integration tests for GET /v1/conversations and GET /v1/conversations/{id},
run against a real (test) Postgres via the ``client`` and ``db_session``
fixtures (see conftest.py).

Conversations and messages are seeded the same way real ones come into
being: genuinely, validly-signed Twilio POST requests to the SMS and voice
webhook paths (same signing helper as tests/test_sms_webhook.py and
tests/test_voice_webhook.py), rather than writing rows directly. That way
these tests exercise the transcript API against data shaped exactly like
production data, including the assistant's own replies (app.agent.graph,
wired into both webhooks in app/api/webhooks.py).

These tests are about the transcript API, not the agent itself (see
tests/test_agent_graph.py for that), so `_mock_gemini_reply` below (autouse)
stubs `gemini_client.generate_content` for every test in this module with a
deterministic plain-text reply per call, numbered by call order ("Reply 1",
"Reply 2", ...) rather than by echoing the conversation content: every
`_send_sms`/`_start_voice_call` in this file shares one test-scoped
transaction (see conftest.py's `db_session`), so `Message.created_at` (a
Postgres `now()` server default, constant for the whole transaction) ties
across every row regardless of insertion order, the same reason
test_list_conversations_orders_most_recently_created_first and
test_get_conversation_returns_messages_in_chronological_order below assign
`created_at` explicitly rather than relying on it as inserted. A reply keyed
by call order stays unique and predictable even though which row a
tied-`created_at` query happens to return first is not.
"""

import base64
import hashlib
import hmac
import itertools
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from google.genai import types
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import gemini_client
from app.core.config import settings
from app.models import Contact, Conversation, Message, PhoneNumber

TEST_AUTH_TOKEN = "test-auth-token-for-ci"
SMS_WEBHOOK_URL = "http://test/v1/webhooks/sms/incoming"
VOICE_INCOMING_URL = "http://test/v1/webhooks/voice/incoming"


@pytest.fixture(autouse=True)
def _mock_gemini_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    call_numbers = itertools.count(1)

    def _stub_generate_content(**kwargs: Any) -> types.GenerateContentResponse:
        reply_text = f"Reply {next(call_numbers)}"
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(role="model", parts=[types.Part(text=reply_text)])
                )
            ]
        )

    monkeypatch.setattr(gemini_client, "generate_content", _stub_generate_content)


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


async def _register_and_get_access_token(
    client: AsyncClient, organization_name: str = "Riverside Dental"
) -> tuple[str, str]:
    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
    response = await client.post(
        "/v1/auth/register",
        json={
            "organization_name": organization_name,
            "email": email,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["user"]["organization_id"]


async def _add_phone_number(
    db_session: AsyncSession, organization_id: str, e164_number: str
) -> None:
    db_session.add(
        PhoneNumber(organization_id=uuid.UUID(organization_id), e164_number=e164_number)
    )
    await db_session.flush()


async def _send_sms(
    client: AsyncClient,
    to_number: str,
    from_number: str,
    body: str,
    message_sid: str,
) -> None:
    params = {"To": to_number, "From": from_number, "Body": body, "MessageSid": message_sid}
    signature = _sign(SMS_WEBHOOK_URL, params)
    response = await client.post(
        "/v1/webhooks/sms/incoming",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )
    assert response.status_code == 200, response.text


async def _start_voice_call(
    client: AsyncClient, to_number: str, from_number: str, call_sid: str
) -> None:
    params = {"To": to_number, "From": from_number, "CallSid": call_sid}
    signature = _sign(VOICE_INCOMING_URL, params)
    response = await client.post(
        "/v1/webhooks/voice/incoming",
        data=params,
        headers={"X-Twilio-Signature": signature},
    )
    assert response.status_code == 200, response.text


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


# --- GET /v1/conversations -------------------------------------------------


async def test_list_conversations_returns_only_the_callers_organization_conversations(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    access_token_a, org_id_a = await _register_and_get_access_token(client, "Org A")
    access_token_b, org_id_b = await _register_and_get_access_token(client, "Org B")
    await _add_phone_number(db_session, org_id_a, "+15005550006")
    await _add_phone_number(db_session, org_id_b, "+15005550007")

    await _send_sms(client, "+15005550006", "+15551110000", "Hi from org A", "SM-A-1")
    await _send_sms(client, "+15005550007", "+15552220000", "Hi from org B", "SM-B-1")

    response_a = await client.get("/v1/conversations", headers=_auth_headers(access_token_a))
    assert response_a.status_code == 200
    body_a = response_a.json()
    assert len(body_a) == 1
    assert body_a[0]["organization_id"] == org_id_a
    assert body_a[0]["contact_phone_number"] == "+15551110000"

    response_b = await client.get("/v1/conversations", headers=_auth_headers(access_token_b))
    assert response_b.status_code == 200
    body_b = response_b.json()
    assert len(body_b) == 1
    assert body_b[0]["organization_id"] == org_id_b
    assert body_b[0]["contact_phone_number"] == "+15552220000"


async def test_list_conversations_returns_empty_list_for_organization_with_none(
    client: AsyncClient,
) -> None:
    access_token, _ = await _register_and_get_access_token(client)

    response = await client.get("/v1/conversations", headers=_auth_headers(access_token))

    assert response.status_code == 200
    assert response.json() == []


async def test_list_conversations_requires_session_auth(client: AsyncClient) -> None:
    response = await client.get("/v1/conversations")
    assert response.status_code == 401


async def test_list_conversations_filters_by_channel(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    access_token, org_id = await _register_and_get_access_token(client)
    await _add_phone_number(db_session, org_id, "+15005550006")

    await _send_sms(client, "+15005550006", "+15551110000", "SMS thread", "SM-CHANNEL-1")
    await _start_voice_call(client, "+15005550006", "+15552220000", "CA-CHANNEL-1")

    sms_response = await client.get(
        "/v1/conversations", params={"channel": "sms"}, headers=_auth_headers(access_token)
    )
    assert sms_response.status_code == 200
    sms_body = sms_response.json()
    assert len(sms_body) == 1
    assert sms_body[0]["channel"] == "sms"

    voice_response = await client.get(
        "/v1/conversations", params={"channel": "voice"}, headers=_auth_headers(access_token)
    )
    assert voice_response.status_code == 200
    voice_body = voice_response.json()
    assert len(voice_body) == 1
    assert voice_body[0]["channel"] == "voice"


async def test_list_conversations_filters_by_contact_id(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    access_token, org_id = await _register_and_get_access_token(client)
    await _add_phone_number(db_session, org_id, "+15005550006")

    await _send_sms(client, "+15005550006", "+15551110000", "From contact one", "SM-C1-1")
    await _send_sms(client, "+15005550006", "+15552220000", "From contact two", "SM-C2-1")

    all_response = await client.get("/v1/conversations", headers=_auth_headers(access_token))
    assert all_response.status_code == 200
    all_body = all_response.json()
    assert len(all_body) == 2
    target_contact_id = next(
        c["contact_id"] for c in all_body if c["contact_phone_number"] == "+15551110000"
    )

    filtered_response = await client.get(
        "/v1/conversations",
        params={"contact_id": target_contact_id},
        headers=_auth_headers(access_token),
    )
    assert filtered_response.status_code == 200
    filtered_body = filtered_response.json()
    assert len(filtered_body) == 1
    assert filtered_body[0]["contact_id"] == target_contact_id
    assert filtered_body[0]["contact_phone_number"] == "+15551110000"


async def test_list_conversations_orders_most_recently_created_first(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Both conversations are created in the same test transaction (via the
    # shared db_session/client fixtures), and Postgres's `now()` (the server
    # default backing `created_at`) returns the enclosing transaction's start
    # time rather than the statement time, so they would otherwise get an
    # identical `created_at` here even though ordering is well-defined across
    # separate, real requests in production. Setting `created_at` explicitly
    # after creation (same approach as test_orders_routes.py) keeps this test
    # about the endpoint's ordering rather than about Postgres transaction
    # semantics.
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    access_token, org_id = await _register_and_get_access_token(client)
    await _add_phone_number(db_session, org_id, "+15005550006")

    await _send_sms(client, "+15005550006", "+15551110000", "First thread", "SM-ORDER-1")
    await _send_sms(client, "+15005550006", "+15552220000", "Second thread", "SM-ORDER-2")

    contacts = (
        await db_session.execute(
            select(Contact).where(Contact.organization_id == uuid.UUID(org_id))
        )
    ).scalars().all()
    contact_id_by_number = {c.e164_number: c.id for c in contacts}

    conversations = (
        await db_session.execute(
            select(Conversation).where(Conversation.organization_id == uuid.UUID(org_id))
        )
    ).scalars().all()
    conversation_by_contact_id = {c.contact_id: c for c in conversations}

    conversation_by_contact_id[
        contact_id_by_number["+15551110000"]
    ].created_at = datetime(2026, 1, 1, tzinfo=UTC)
    conversation_by_contact_id[
        contact_id_by_number["+15552220000"]
    ].created_at = datetime(2026, 1, 2, tzinfo=UTC)
    await db_session.flush()

    response = await client.get("/v1/conversations", headers=_auth_headers(access_token))
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    numbers_in_order = [c["contact_phone_number"] for c in body]
    assert numbers_in_order.index("+15552220000") < numbers_in_order.index("+15551110000")


# --- GET /v1/conversations/{id} --------------------------------------------


async def test_get_conversation_returns_messages_in_chronological_order(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # As in test_list_conversations_orders_most_recently_created_first: all
    # six messages (three inbound turns, each with the agent's persisted
    # reply) land in the same test transaction, so Postgres's `now()` (the
    # server default backing `created_at`) would otherwise give them an
    # identical timestamp. Setting `created_at` explicitly, in an order
    # deliberately different from insertion order, makes this test prove the
    # endpoint sorts by `created_at` rather than merely returning rows in
    # whatever order Postgres happened to store them; each turn's assistant
    # reply is kept immediately after its own user turn, matching how a real
    # conversation reads.
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    access_token, org_id = await _register_and_get_access_token(client)
    await _add_phone_number(db_session, org_id, "+15005550006")

    await _send_sms(client, "+15005550006", "+15551110000", "First message", "SM-ORD-1")
    await _send_sms(client, "+15005550006", "+15551110000", "Second message", "SM-ORD-2")
    await _send_sms(client, "+15005550006", "+15551110000", "Third message", "SM-ORD-3")

    messages = (
        await db_session.execute(
            select(Message).join(Conversation, Conversation.id == Message.conversation_id).where(
                Conversation.organization_id == uuid.UUID(org_id)
            )
        )
    ).scalars().all()
    # `_mock_gemini_reply` numbers its replies by call order ("Reply 1",
    # "Reply 2", "Reply 3"), one per `_send_sms` above in the same order, so
    # every one of the six rows has unique, predictable content to address.
    message_by_content = {m.content: m for m in messages}
    # Deliberately assign timestamps out of insertion order, turn by turn.
    message_by_content["Third message"].created_at = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    message_by_content["Reply 3"].created_at = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    message_by_content["First message"].created_at = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
    message_by_content["Reply 1"].created_at = datetime(2026, 1, 2, 0, 1, tzinfo=UTC)
    message_by_content["Second message"].created_at = datetime(2026, 1, 3, 0, 0, tzinfo=UTC)
    message_by_content["Reply 2"].created_at = datetime(2026, 1, 3, 0, 1, tzinfo=UTC)
    await db_session.flush()

    list_response = await client.get("/v1/conversations", headers=_auth_headers(access_token))
    conversation_id = list_response.json()[0]["id"]

    detail_response = await client.get(
        f"/v1/conversations/{conversation_id}", headers=_auth_headers(access_token)
    )
    assert detail_response.status_code == 200
    body = detail_response.json()
    assert body["id"] == conversation_id
    assert body["channel"] == "sms"
    assert body["contact_phone_number"] == "+15551110000"
    contents = [m["content"] for m in body["messages"]]
    assert contents == [
        "Third message",
        "Reply 3",
        "First message",
        "Reply 1",
        "Second message",
        "Reply 2",
    ]


async def test_get_conversation_requires_session_auth(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    access_token, org_id = await _register_and_get_access_token(client)
    await _add_phone_number(db_session, org_id, "+15005550006")
    await _send_sms(client, "+15005550006", "+15551110000", "Hello", "SM-AUTH-1")
    list_response = await client.get("/v1/conversations", headers=_auth_headers(access_token))
    conversation_id = list_response.json()[0]["id"]

    response = await client.get(f"/v1/conversations/{conversation_id}")

    assert response.status_code == 401


async def test_get_conversation_from_another_organization_returns_404(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    access_token_a, org_id_a = await _register_and_get_access_token(client, "Org A")
    access_token_b, org_id_b = await _register_and_get_access_token(client, "Org B")
    await _add_phone_number(db_session, org_id_a, "+15005550006")
    await _add_phone_number(db_session, org_id_b, "+15005550007")

    await _send_sms(client, "+15005550006", "+15551110000", "Org A's message", "SM-XORG-1")
    list_response = await client.get("/v1/conversations", headers=_auth_headers(access_token_a))
    conversation_id = list_response.json()[0]["id"]

    response = await client.get(
        f"/v1/conversations/{conversation_id}", headers=_auth_headers(access_token_b)
    )

    assert response.status_code == 404


async def test_get_conversation_returns_404_for_unknown_id(
    client: AsyncClient,
) -> None:
    access_token, _ = await _register_and_get_access_token(client)

    response = await client.get(
        f"/v1/conversations/{uuid.uuid4()}", headers=_auth_headers(access_token)
    )

    assert response.status_code == 404
