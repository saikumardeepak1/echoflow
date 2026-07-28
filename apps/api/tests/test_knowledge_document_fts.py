"""Focused test for the knowledge_documents.content_tsv trigger and full-text
search.

Confirms that inserting and updating a document's `content` correctly
(re)populates `content_tsv`, and that a full-text query against it returns
the expected row and excludes an unrelated one.
"""

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeDocument, Organization


async def _make_organization(db_session: AsyncSession) -> Organization:
    organization = Organization(name="Riverside Dental")
    db_session.add(organization)
    await db_session.flush()
    return organization


async def test_insert_populates_content_tsv(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    document = KnowledgeDocument(
        organization_id=organization.id,
        title="Cancellation policy",
        content="Cancel or reschedule your appointment at least 24 hours in advance.",
    )
    db_session.add(document)
    await db_session.commit()

    await db_session.refresh(document)
    assert document.content_tsv is not None
    assert "cancel" in document.content_tsv
    assert "reschedul" in document.content_tsv


async def test_update_content_repopulates_content_tsv(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    document = KnowledgeDocument(
        organization_id=organization.id,
        title="Office hours",
        content="The original sentence about weekday hours.",
    )
    db_session.add(document)
    await db_session.commit()
    await db_session.refresh(document)

    original_tsv = document.content_tsv
    assert original_tsv is not None
    assert "weekday" in original_tsv

    document.content = "A completely different sentence about holiday closures."
    await db_session.commit()
    await db_session.refresh(document)

    assert document.content_tsv is not None
    assert document.content_tsv != original_tsv
    assert "holiday" in document.content_tsv
    assert "weekday" not in document.content_tsv


async def test_full_text_query_returns_expected_document(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    matching = KnowledgeDocument(
        organization_id=organization.id,
        title="Parking",
        content="Free parking is available in the lot behind the building.",
    )
    unrelated = KnowledgeDocument(
        organization_id=organization.id,
        title="Payment",
        content="We accept all major credit cards and mobile payments.",
    )
    db_session.add_all([matching, unrelated])
    await db_session.commit()

    query = func.plainto_tsquery("english", "free parking")
    result = await db_session.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.content_tsv.op("@@")(query))
    )
    matches = result.scalars().all()

    assert [d.id for d in matches] == [matching.id]

    ranked = await db_session.execute(
        select(
            KnowledgeDocument.id,
            text("ts_rank(content_tsv, plainto_tsquery('english', 'free parking')) AS rank"),
        )
        .where(KnowledgeDocument.content_tsv.op("@@")(query))
        .order_by(text("rank DESC"))
    )
    top = ranked.first()
    assert top is not None
    assert top.id == matching.id
