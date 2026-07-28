"""Twilio webhook routes.

See docs/TDD.md section 3.6 for the signature verification design,
docs/ARCHITECTURE.md "Request flow: inbound voice call" / "Request flow:
inbound SMS" for the flows this module implements, and docs/TDD.md section
3.3 for the agent graph both handlers call into for their replies.
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.graph import run_agent_turn
from app.core.db import get_session
from app.core.logging import set_correlation_id
from app.services.conversation_service import (
    find_or_create_contact,
    find_or_create_open_conversation,
    load_recent_history,
    persist_message,
    resolve_organization_by_number,
)
from app.services.twilio_service import (
    build_gather_speech,
    build_hangup,
    build_say_and_gather,
    build_sms_reply,
    require_twilio_signature,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

# The first thing a caller hears on pickup, before they have said anything
# for the agent to respond to, so there is no agent turn to run yet; every
# turn after this one is a real agent-generated reply (see voice_respond).
_VOICE_GREETING = "Thanks for calling. How can I help you today?"


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
    `Message`, run the agent on the conversation so far, persist its reply,
    and return the reply as TwiML.
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

    history = await load_recent_history(session, conversation.id)
    turn = await run_agent_turn(session, organization.id, contact.id, conversation.id, history)
    await persist_message(session, conversation.id, role="assistant", content=turn.reply_text)
    await session.commit()

    twiml = build_sms_reply(turn.reply_text)
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
    persist the caller's transcribed turn, run the agent on the conversation
    so far, and persist its reply.

    Ending the call (`<Hangup>`) versus continuing to listen (`<Gather>`) is
    the agent's own call: `run_agent_turn`'s `should_end_call` is `True` once
    the model has called the `end_call` tool (app/agent/tools.py) to say the
    conversation is resolved, so this handler defers to that flag rather
    than guessing at an end-of-call condition itself.
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

    history = await load_recent_history(session, conversation.id)
    turn = await run_agent_turn(session, organization.id, contact.id, conversation.id, history)
    await persist_message(session, conversation.id, role="assistant", content=turn.reply_text)
    await session.commit()

    if turn.should_end_call:
        twiml = build_hangup(turn.reply_text)
        return Response(content=twiml, media_type="application/xml")

    action_url = str(request.url_for("voice_respond"))
    twiml = build_say_and_gather(turn.reply_text, action_url)
    return Response(content=twiml, media_type="application/xml")
