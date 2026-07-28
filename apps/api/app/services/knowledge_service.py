"""Knowledge base CRUD and full-text search.

See docs/TDD.md section 3.2: this service backs both the dashboard's
`/v1/knowledge-documents` CRUD endpoints (app/api/knowledge_documents.py) and
the agent's `knowledge_search` tool (app/agent/, a later issue). `search`
ranks matches with Postgres `ts_rank` over the `content_tsv` column, which a
database trigger keeps in sync with `content` (see
app/models/knowledge_document.py and the migration in alembic/versions/), so
application code never computes or writes `content_tsv` directly.

None of these functions call `session.commit()`; they `flush()` so a
newly-created row gets its generated primary key and is visible to
subsequent queries within the same session, but committing (and therefore
choosing the transaction boundary) is left to the caller, matching the
convention in conversation_service.py.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.knowledge_document import KnowledgeDocument


async def create_document(
    session: AsyncSession,
    organization_id: uuid.UUID,
    title: str,
    content: str,
) -> KnowledgeDocument:
    """Create a new knowledge document for `organization_id`."""
    document = KnowledgeDocument(
        organization_id=organization_id,
        title=title,
        content=content,
    )
    session.add(document)
    await session.flush()
    return document


async def list_documents(
    session: AsyncSession, organization_id: uuid.UUID
) -> list[KnowledgeDocument]:
    """List every knowledge document belonging to `organization_id`, newest first."""
    result = await session.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.organization_id == organization_id)
        .order_by(KnowledgeDocument.created_at.desc())
    )
    return list(result.scalars().all())


async def get_document(
    session: AsyncSession, organization_id: uuid.UUID, document_id: uuid.UUID
) -> KnowledgeDocument | None:
    """Fetch a single knowledge document, scoped to `organization_id`.

    Returns `None` both when no document with `document_id` exists at all and
    when one exists but belongs to a different organization, so callers can't
    distinguish "not found" from "not yours" (see app/api/knowledge_documents.py).
    """
    result = await session.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.organization_id == organization_id,
        )
    )
    return result.scalar_one_or_none()


async def delete_document(
    session: AsyncSession, organization_id: uuid.UUID, document_id: uuid.UUID
) -> bool:
    """Delete a knowledge document, scoped to `organization_id`.

    Returns `True` if a document was found (within this organization) and
    deleted, `False` if no such document exists for this organization.
    """
    document = await get_document(session, organization_id, document_id)
    if document is None:
        return False

    await session.delete(document)
    await session.flush()
    return True


async def search(
    session: AsyncSession,
    organization_id: uuid.UUID,
    query: str,
    limit: int = 5,
) -> list[KnowledgeDocument]:
    """Full-text search a organization's knowledge documents, ranked by
    relevance.

    Uses `websearch_to_tsquery` (rather than `plainto_tsquery`) on the query
    side so callers can pass ordinary free-text search phrases, including
    quoted phrases and `-exclusions`, the same syntax web search engines
    accept; it degrades gracefully (matches nothing rather than raising) for
    a query with no recognizable lexemes. Results are scored with `ts_rank`
    over `content_tsv` (kept in sync with `content` by a database trigger,
    see app/models/knowledge_document.py) and returned best-match first.

    An empty, whitespace-only, or no-match query returns an empty list, never
    an error: `websearch_to_tsquery` parses such input to an empty tsquery,
    which matches no row under `@@` rather than raising or matching
    everything.
    """
    tsquery = func.websearch_to_tsquery("english", query)
    rank = func.ts_rank(KnowledgeDocument.content_tsv, tsquery).label("rank")

    result = await session.execute(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.organization_id == organization_id,
            KnowledgeDocument.content_tsv.op("@@")(tsquery),
        )
        .order_by(rank.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
