"""Conversation transcript viewer (``GET /v1/conversations``,
``GET /v1/conversations/{id}``) for the dashboard's conversation log viewer
(see docs/TDD.md section 3.2 and issue #9). Session-authenticated and
org-scoped, same pattern as app/api/orders.py and
app/api/knowledge_documents.py: a dashboard user only ever sees their own
organization's conversations, whether they were created via the SMS or
voice webhook.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_session
from app.models import User
from app.schemas.conversation import ConversationDetailResponse, ConversationResponse
from app.services import conversation_service

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    channel: str | None = None,
    contact_id: uuid.UUID | None = None,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> list[ConversationResponse]:
    """All conversations belonging to the caller's organization, most
    recently created first. Optionally filtered by ``channel`` (``voice`` or
    ``sms``) and/or ``contact_id``.

    Requires a JWT session (``Authorization: Bearer <jwt>``).
    """
    conversations = await conversation_service.list_conversations(
        session,
        organization_id=current_user.organization_id,
        channel=channel,
        contact_id=contact_id,
    )
    return [ConversationResponse.from_conversation(conversation) for conversation in conversations]


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: uuid.UUID,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> ConversationDetailResponse:
    """A single conversation belonging to the caller's organization, with
    its full transcript in chronological order.

    Requires a JWT session (``Authorization: Bearer <jwt>``). 404s (rather
    than 403s) for a conversation that exists but belongs to another
    organization, so a caller cannot distinguish "not found" from "not
    yours" and probe for other orgs' conversation ids.
    """
    conversation = await conversation_service.get_conversation_with_messages(
        session, organization_id=current_user.organization_id, conversation_id=conversation_id
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return ConversationDetailResponse.from_conversation(conversation)
