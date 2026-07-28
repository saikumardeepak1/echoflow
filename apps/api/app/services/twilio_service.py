"""Twilio integration: inbound webhook signature verification, TwiML response
generation, and outbound SMS.

See docs/TDD.md section 3.6 for the webhook security design: Twilio signs
every webhook request with an HMAC-SHA1 of the full callback URL plus the
sorted POST parameters, keyed by the account's auth token, sent as the
`X-Twilio-Signature` header. `verify_signature` recomputes that signature via
the official `twilio` SDK's `RequestValidator` and compares it against the
header value; `require_twilio_signature` is the FastAPI dependency that
webhook routes use to reject unsigned or mis-signed requests with 403 before
any handler logic runs.
"""

from fastapi import HTTPException, Request, status
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import Gather, VoiceResponse

from app.core.config import settings


def verify_signature(url: str, params: dict[str, str], signature: str, auth_token: str) -> bool:
    """Recompute Twilio's request signature and compare it against the one supplied.

    :param url: the full callback URL Twilio requested, exactly as Twilio saw it
        (scheme, host, path, and query string all matter to the signature).
    :param params: the request's POST form parameters.
    :param signature: the value of the `X-Twilio-Signature` header.
    :param auth_token: the Twilio account auth token the signature is keyed on.
    :returns: True if the signature matches, False otherwise.
    """
    validator = RequestValidator(auth_token)
    return bool(validator.validate(url, params, signature))


async def require_twilio_signature(request: Request) -> None:
    """FastAPI dependency that verifies a Twilio webhook request's signature.

    Reads `X-Twilio-Signature`, reconstructs the callback URL and form
    parameters from the incoming request, and verifies them against
    `settings.twilio_auth_token`. Raises HTTP 403 if the header is missing or
    the signature does not match, so route handlers only ever run for
    requests genuinely sent by Twilio.

    Note: in production this process must sit behind a proxy configured to
    forward the original scheme and host (or run uvicorn with
    `--proxy-headers`) so `request.url` reflects the public HTTPS URL Twilio
    actually requested, matching the URL Twilio signed against.
    """
    signature = request.headers.get("X-Twilio-Signature")
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Missing Twilio signature"
        )

    form = await request.form()
    params: dict[str, str] = {
        key: value for key, value in form.multi_items() if isinstance(value, str)
    }
    url = str(request.url)

    if not verify_signature(url, params, signature, settings.twilio_auth_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Twilio signature"
        )


def build_gather_speech(prompt: str, action_url: str) -> str:
    """Build TwiML that speaks `prompt` while listening for speech input.

    The prompt is nested inside the `<Gather>` verb, so Twilio starts
    listening as soon as playback begins (the caller can speak over the
    prompt rather than waiting for it to finish). Used for the first prompt
    of a call, where a quick response matters more than a guaranteed full
    read of the prompt.
    """
    response = VoiceResponse()
    gather = Gather(input="speech", action=action_url, method="POST")
    gather.say(prompt)
    response.append(gather)
    return str(response)


def build_say_and_gather(message: str, action_url: str) -> str:
    """Build TwiML that speaks `message` in full, then listens for speech input.

    `<Say>` and `<Gather>` are separate, sequential verbs, so the caller
    hears the entire message before Twilio starts listening. Used mid
    conversation, where the agent's reply must be heard in full before the
    caller's next turn is collected.
    """
    response = VoiceResponse()
    response.say(message)
    response.gather(input="speech", action=action_url, method="POST")
    return str(response)


def build_hangup(message: str | None) -> str:
    """Build TwiML that optionally speaks `message`, then ends the call."""
    response = VoiceResponse()
    if message:
        response.say(message)
    response.hangup()
    return str(response)


def build_sms_reply(message: str) -> str:
    """Build TwiML for an SMS webhook response containing `message`."""
    response = MessagingResponse()
    response.message(message)
    return str(response)


def send_sms(to: str, body: str) -> str:
    """Send an outbound SMS via the Twilio REST API, returning the message SID.

    Used by the notification worker (see docs/TDD.md section 3.5) to send
    appointment confirmations and reminders outside of a webhook request.
    """
    client = Client(settings.twilio_account_sid, settings.twilio_auth_token)
    message = client.messages.create(to=to, from_=settings.twilio_phone_number, body=body)
    return str(message.sid)
