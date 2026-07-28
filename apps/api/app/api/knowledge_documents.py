"""Knowledge base management (POST/GET/DELETE /v1/knowledge-documents).
Session-authenticated (a dashboard user manages their own organization's
knowledge base); see docs/TDD.md section 3.2 and 3.4.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_session
from app.models import User
from app.schemas.knowledge_document import (
    KnowledgeDocumentCreateRequest,
    KnowledgeDocumentResponse,
)
from app.services import knowledge_service

router = APIRouter(prefix="/v1/knowledge-documents", tags=["knowledge-documents"])


@router.post("", response_model=KnowledgeDocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge_document(
    body: KnowledgeDocumentCreateRequest,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> KnowledgeDocumentResponse:
    """Create a knowledge document for the caller's organization.

    Requires a JWT session (``Authorization: Bearer <jwt>``). The document is
    always created under ``current_user.organization_id``; a caller can never
    write into another organization's knowledge base.
    """
    document = await knowledge_service.create_document(
        session,
        organization_id=current_user.organization_id,
        title=body.title,
        content=body.content,
    )
    await session.commit()
    await session.refresh(document)
    return KnowledgeDocumentResponse.model_validate(document)


@router.get("", response_model=list[KnowledgeDocumentResponse])
async def list_knowledge_documents(
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> list[KnowledgeDocumentResponse]:
    """List every knowledge document belonging to the caller's organization,
    newest first.

    Requires a JWT session (``Authorization: Bearer <jwt>``).
    """
    documents = await knowledge_service.list_documents(
        session, organization_id=current_user.organization_id
    )
    return [KnowledgeDocumentResponse.model_validate(document) for document in documents]


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_document(
    document_id: uuid.UUID,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a knowledge document belonging to the caller's organization.

    Requires a JWT session (``Authorization: Bearer <jwt>``). 404s (rather
    than 403s) for a document that exists but belongs to another
    organization, so a caller cannot distinguish "not found" from "not
    yours" and probe for other orgs' document ids.
    """
    deleted = await knowledge_service.delete_document(
        session, organization_id=current_user.organization_id, document_id=document_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge document not found"
        )
    await session.commit()
