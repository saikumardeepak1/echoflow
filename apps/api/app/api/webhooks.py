"""Twilio webhook routes.

See docs/TDD.md section 3.6 for the signature verification design and
docs/ARCHITECTURE.md "Request flow: inbound voice call" / "Request flow:
inbound SMS" for the flows this module implements.
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.logging import set_correlation_id
from app.services.conversation_service import (
    find_or_create_contact,
    find_or_create_open_conversation,
    persist_message,
    resolve_organization_by_number,
)
from app.services.twilio_service import (
    build_gather_speech,
    build_say_and_gather,
    build_sms_reply,
    require_twilio_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

# Real agent-generated replies land in a later issue (see docs/ROADMAP.md);
# for now every inbound SMS gets this static acknowledgement so the full
# webhook -> persistence -> TwiML reply loop is exercised end to end without
# waiting on the agent orchestration work.
_ACKNOWLEDGEMENT_REPLY = "Thanks for reaching out. We'll get back to you shortly."

# Same placeholder strategy as the SMS webhook above, applied to voice: the
# real agent graph (docs/TDD.md section 3.3) generates replies in a later
# issue. For now every call gets a greeting on pickup and this static
# acknowledgement after each turn.
_VOICE_GREETING = "Thanks for calling. How can I help you today?"
_VOICE_ACKNOWLEDGEMENT_REPLY = "Got it. Someone from our team will follow up shortly."


@router.post(
    "/sms/incoming",
    dependencies=[Depends(require_twilio_signature)],
    response_class=Response,
)
async def sms_incoming(
    to: str = Form(..., alias="To"),
    from_: str = Form(..., alias="From"),
    body: str = Form(..., alias="Body"),
    message_sid: str = Form(..., alias="MessageSid"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Handle an inbound SMS: resolve the organization by the `To` number,
    find-or-create the `Contact`/`Conversation`, persist the inbound
    `Message`, and reply with a static TwiML acknowledgement.
    """
    # Once the payload is parsed, tag the rest of this request's log lines
    # with the Twilio MessageSid rather than the generic request id the
    # correlation middleware generated at request start (see
    # app/core/middleware.py and docs/TDD.md section 8), so a text's full
    # log trail is traceable by its Twilio SID.
    set_correlation_id(message_sid)

    organization = await resolve_organization_by_number(session, to)
    if organization is None:
        logger.warning("sms webhook received for unrecognized number", extra={"to": to})
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unrecognized phone number"
        )

    contact = await find_or_create_contact(session, organization.id, from_)
    conversation = await find_or_create_open_conversation(
        session, organization.id, contact.id, channel="sms"
    )
    await persist_message(
        session,
        conversation.id,
        role="user",
        content=body,
        twilio_sid=message_sid,
    )
    await session.commit()

    twiml = build_sms_reply(_ACKNOWLEDGEMENT_REPLY)
    return Response(content=twiml, media_type="application/xml")


@router.post(
    "/voice/incoming",
    dependencies=[Depends(require_twilio_signature)],
    response_class=Response,
    name="voice_incoming",
)
async def voice_incoming(
    request: Request,
    to: str = Form(..., alias="To"),
    from_: str = Form(..., alias="From"),
    call_sid: str = Form(..., alias="CallSid"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Handle an inbound call: resolve the organization by the `To` number,
    find-or-create the `Contact`/`Conversation` (`channel="voice"`), and
    greet the caller with TwiML that gathers their first spoken turn (see
    docs/ARCHITECTURE.md "Request flow: inbound voice call").
    """
    set_correlation_id(call_sid)

    organization = await resolve_organization_by_number(session, to)
    if organization is None:
        logger.warning("voice webhook received for unrecognized number", extra={"to": to})
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unrecognized phone number"
        )

    contact = await find_or_create_contact(session, organization.id, from_)
    await find_or_create_open_conversation(session, organization.id, contact.id, channel="voice")
    await session.commit()

    action_url = str(request.url_for("voice_respond"))
    twiml = build_gather_speech(_VOICE_GREETING, action_url)
    return Response(content=twiml, media_type="application/xml")


@router.post(
    "/voice/respond",
    dependencies=[Depends(require_twilio_signature)],
    response_class=Response,
    name="voice_respond",
)
async def voice_respond(
    request: Request,
    to: str = Form(..., alias="To"),
    from_: str = Form(..., alias="From"),
    call_sid: str = Form(..., alias="CallSid"),
    speech_result: str = Form("", alias="SpeechResult"),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Handle one turn of an in-progress call: resolve the organization and
    the already-open `Conversation` the same way `voice_incoming` did,
    persist the caller's transcribed turn plus a placeholder reply, and
    gather the next turn.

    Ending the call (`<Hangup>`) is a decision the real agent graph makes
    once it exists (docs/TDD.md section 3.3, a later issue); until then this
    handler always loops with another `<Gather>` rather than guessing at an
    end-of-call condition, so the caller is never cut off mid-conversation
    by placeholder logic standing in for the agent.
    """
    set_correlation_id(call_sid)

    organization = await resolve_organization_by_number(session, to)
    if organization is None:
        logger.warning("voice webhook received for unrecognized number", extra={"to": to})
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unrecognized phone number"
        )

    contact = await find_or_create_contact(session, organization.id, from_)
    conversation = await find_or_create_open_conversation(
        session, organization.id, contact.id, channel="voice"
    )
    await persist_message(
        session,
        conversation.id,
        role="user",
        content=speech_result,
        twilio_sid=call_sid,
    )
    await persist_message(
        session,
        conversation.id,
        role="assistant",
        content=_VOICE_ACKNOWLEDGEMENT_REPLY,
    )
    await session.commit()

    action_url = str(request.url_for("voice_respond"))
    twiml = build_say_and_gather(_VOICE_ACKNOWLEDGEMENT_REPLY, action_url)
    return Response(content=twiml, media_type="application/xml")
