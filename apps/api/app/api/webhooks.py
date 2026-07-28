"""Twilio webhook routes.

See docs/TDD.md section 3.6 for the signature verification design and
docs/ARCHITECTURE.md "Request flow: inbound SMS" for the flow this module
implements. The voice webhooks (`/v1/webhooks/voice/incoming`,
`/v1/webhooks/voice/respond`) land in a follow-up issue and will reuse
`app.services.conversation_service` the same way this one does.
"""

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, status
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
from app.services.twilio_service import build_sms_reply, require_twilio_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

# Real agent-generated replies land in a later issue (see docs/ROADMAP.md);
# for now every inbound SMS gets this static acknowledgement so the full
# webhook -> persistence -> TwiML reply loop is exercised end to end without
# waiting on the agent orchestration work.
_ACKNOWLEDGEMENT_REPLY = "Thanks for reaching out. We'll get back to you shortly."


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
