"""Tests for app.services.twilio_service.

Signature verification is tested against a hand-rolled implementation of
Twilio's own algorithm (HMAC-SHA1 over the URL plus sorted, concatenated
param name/value pairs, base64-encoded) rather than by round-tripping
through the SDK's own signing helper, so the test is an independent check
rather than a tautology. No live Twilio account is used anywhere; the REST
client is mocked for the send_sms test.
"""

import base64
import hashlib
import hmac
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.services.twilio_service import (
    build_gather_speech,
    build_hangup,
    build_say_and_gather,
    build_sms_reply,
    require_twilio_signature,
    send_sms,
    verify_signature,
)

TEST_AUTH_TOKEN = "test-auth-token-for-ci"


def _sign(url: str, params: dict[str, str], auth_token: str = TEST_AUTH_TOKEN) -> str:
    """Independently compute a Twilio request signature the way Twilio does:
    HMAC-SHA1 of the URL followed by each sorted param name+value, concatenated,
    base64-encoded.
    """
    data = url
    for key in sorted(params):
        data += key + params[key]
    digest = hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("utf-8")


# --- verify_signature ---------------------------------------------------


def test_verify_signature_accepts_validly_signed_request() -> None:
    url = "https://example.com/v1/webhooks/voice/incoming"
    params = {"CallSid": "CA123", "From": "+15551234567", "To": "+15005550006"}
    signature = _sign(url, params)

    assert verify_signature(url, params, signature, TEST_AUTH_TOKEN) is True


def test_verify_signature_rejects_tampered_signature() -> None:
    url = "https://example.com/v1/webhooks/voice/incoming"
    params = {"CallSid": "CA123", "From": "+15551234567", "To": "+15005550006"}
    signature = _sign(url, params)
    tampered_signature = signature[:-1] + ("A" if signature[-1] != "A" else "B")

    assert verify_signature(url, params, tampered_signature, TEST_AUTH_TOKEN) is False


def test_verify_signature_rejects_tampered_params() -> None:
    url = "https://example.com/v1/webhooks/voice/incoming"
    params = {"CallSid": "CA123", "From": "+15551234567", "To": "+15005550006"}
    signature = _sign(url, params)

    tampered_params = dict(params, From="+15559999999")

    assert verify_signature(url, tampered_params, signature, TEST_AUTH_TOKEN) is False


def test_verify_signature_rejects_wrong_auth_token() -> None:
    url = "https://example.com/v1/webhooks/voice/incoming"
    params = {"CallSid": "CA123"}
    signature = _sign(url, params, auth_token="a-different-token")

    assert verify_signature(url, params, signature, TEST_AUTH_TOKEN) is False


# --- require_twilio_signature dependency --------------------------------


def _build_test_app() -> FastAPI:
    app = FastAPI()

    @app.post("/webhooks/test-signature", dependencies=[Depends(require_twilio_signature)])
    async def webhook() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.fixture
def twilio_auth_token(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(settings, "twilio_auth_token", TEST_AUTH_TOKEN)
    return TEST_AUTH_TOKEN


async def test_require_twilio_signature_allows_validly_signed_request(
    twilio_auth_token: str,
) -> None:
    app = _build_test_app()
    url = "http://test/webhooks/test-signature"
    params = {"CallSid": "CA123", "From": "+15551234567"}
    signature = _sign(url, params, auth_token=twilio_auth_token)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/test-signature",
            data=params,
            headers={"X-Twilio-Signature": signature},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_require_twilio_signature_rejects_missing_signature(
    twilio_auth_token: str,
) -> None:
    app = _build_test_app()
    params = {"CallSid": "CA123", "From": "+15551234567"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/webhooks/test-signature", data=params)

    assert response.status_code == 403


async def test_require_twilio_signature_rejects_tampered_signature(
    twilio_auth_token: str,
) -> None:
    app = _build_test_app()
    url = "http://test/webhooks/test-signature"
    params = {"CallSid": "CA123", "From": "+15551234567"}
    signature = _sign(url, params, auth_token=twilio_auth_token)
    tampered_signature = signature[:-1] + ("A" if signature[-1] != "A" else "B")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/test-signature",
            data=params,
            headers={"X-Twilio-Signature": tampered_signature},
        )

    assert response.status_code == 403


async def test_require_twilio_signature_rejects_mismatched_params(
    twilio_auth_token: str,
) -> None:
    app = _build_test_app()
    url = "http://test/webhooks/test-signature"
    params = {"CallSid": "CA123", "From": "+15551234567"}
    signature = _sign(url, params, auth_token=twilio_auth_token)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/test-signature",
            data={"CallSid": "CA123", "From": "+15559999999"},
            headers={"X-Twilio-Signature": signature},
        )

    assert response.status_code == 403


# --- TwiML builders -------------------------------------------------------


def test_build_gather_speech_produces_valid_gather_with_nested_say() -> None:
    xml = build_gather_speech("How can I help you today?", "https://example.com/respond")
    root = ET.fromstring(xml)

    assert root.tag == "Response"
    gather = root.find("Gather")
    assert gather is not None
    assert gather.get("input") == "speech"
    assert gather.get("action") == "https://example.com/respond"
    assert gather.get("method") == "POST"

    say = gather.find("Say")
    assert say is not None
    assert say.text == "How can I help you today?"


def test_build_say_and_gather_produces_sequential_say_then_gather() -> None:
    xml = build_say_and_gather("Your appointment is confirmed.", "https://example.com/respond")
    root = ET.fromstring(xml)

    assert root.tag == "Response"
    children = list(root)
    assert [child.tag for child in children] == ["Say", "Gather"]
    assert children[0].text == "Your appointment is confirmed."

    gather = children[1]
    assert gather.get("input") == "speech"
    assert gather.get("action") == "https://example.com/respond"
    assert gather.find("Say") is None


def test_build_hangup_with_message_says_then_hangs_up() -> None:
    xml = build_hangup("Goodbye.")
    root = ET.fromstring(xml)

    children = list(root)
    assert [child.tag for child in children] == ["Say", "Hangup"]
    assert children[0].text == "Goodbye."


def test_build_hangup_without_message_only_hangs_up() -> None:
    xml = build_hangup(None)
    root = ET.fromstring(xml)

    children = list(root)
    assert [child.tag for child in children] == ["Hangup"]


def test_build_sms_reply_produces_message_verb() -> None:
    xml = build_sms_reply("Thanks, see you at 3pm.")
    root = ET.fromstring(xml)

    assert root.tag == "Response"
    message = root.find("Message")
    assert message is not None
    assert message.text == "Thanks, see you at 3pm."


# --- send_sms --------------------------------------------------------------


def test_send_sms_returns_message_sid_and_uses_configured_client() -> None:
    mock_message = MagicMock()
    mock_message.sid = "SM123456"
    mock_client_instance = MagicMock()
    mock_client_instance.messages.create.return_value = mock_message

    with patch(
        "app.services.twilio_service.Client", return_value=mock_client_instance
    ) as mock_client_cls:
        sid = send_sms(to="+15551234567", body="Reminder: appointment tomorrow at 3pm.")

    assert sid == "SM123456"
    mock_client_cls.assert_called_once()
    mock_client_instance.messages.create.assert_called_once()
    _, kwargs = mock_client_instance.messages.create.call_args
    assert kwargs["to"] == "+15551234567"
    assert kwargs["body"] == "Reminder: appointment tomorrow at 3pm."
