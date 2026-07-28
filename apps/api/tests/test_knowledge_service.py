"""Tests for app.services.knowledge_service.

Runs against a real (migrated) Postgres database, see conftest.py. `search`
is exercised against fixture documents with known relevant and irrelevant
content, asserting the ranking makes sense and that empty/no-match queries
return an empty list rather than raising.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeDocument, Organization
from app.services import knowledge_service


async def _make_organization(
    db_session: AsyncSession, name: str = "Riverside Dental"
) -> Organization:
    organization = Organization(name=name)
    db_session.add(organization)
    await db_session.flush()
    return organization


# --- create_document ---------------------------------------------------------


async def test_create_document_persists_and_scopes_to_organization(
    db_session: AsyncSession,
) -> None:
    organization = await _make_organization(db_session)

    document = await knowledge_service.create_document(
        db_session,
        organization_id=organization.id,
        title="Parking",
        content="Free parking is available in the lot behind the building.",
    )

    assert document.id is not None
    assert document.organization_id == organization.id
    assert document.title == "Parking"


# --- list_documents -----------------------------------------------------------


async def test_list_documents_returns_only_the_callers_organization(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_organization(db_session, "Riverside Dental")
    org_b = await _make_organization(db_session, "Lakeside Clinic")

    await knowledge_service.create_document(
        db_session, organization_id=org_a.id, title="A doc", content="Org A content."
    )
    await knowledge_service.create_document(
        db_session, organization_id=org_b.id, title="B doc", content="Org B content."
    )

    results = await knowledge_service.list_documents(db_session, organization_id=org_a.id)

    assert len(results) == 1
    assert results[0].title == "A doc"


async def test_list_documents_orders_newest_first(db_session: AsyncSession) -> None:
    # created_at defaults to Postgres `now()`, which is fixed for the
    # lifetime of one transaction (transaction_timestamp() semantics), so two
    # rows created back-to-back inside this test's single wrapping
    # transaction would otherwise get an identical timestamp. Setting
    # distinct values directly exercises the ordering the query applies
    # without depending on real wall-clock elapsed time between two calls.
    organization = await _make_organization(db_session)

    first = await knowledge_service.create_document(
        db_session, organization_id=organization.id, title="First", content="First content."
    )
    first.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    second = await knowledge_service.create_document(
        db_session, organization_id=organization.id, title="Second", content="Second content."
    )
    second.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    await db_session.flush()

    results = await knowledge_service.list_documents(db_session, organization_id=organization.id)

    assert [d.id for d in results] == [second.id, first.id]


# --- get_document ---------------------------------------------------------


async def test_get_document_returns_none_for_another_organization(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_organization(db_session, "Riverside Dental")
    org_b = await _make_organization(db_session, "Lakeside Clinic")

    document = await knowledge_service.create_document(
        db_session, organization_id=org_a.id, title="A doc", content="Org A content."
    )

    result = await knowledge_service.get_document(
        db_session, organization_id=org_b.id, document_id=document.id
    )
    assert result is None


async def test_get_document_returns_none_for_unknown_id(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    result = await knowledge_service.get_document(
        db_session, organization_id=organization.id, document_id=uuid.uuid4()
    )
    assert result is None


# --- delete_document ---------------------------------------------------------


async def test_delete_document_removes_row_and_returns_true(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)
    document = await knowledge_service.create_document(
        db_session, organization_id=organization.id, title="Parking", content="Free parking."
    )

    deleted = await knowledge_service.delete_document(
        db_session, organization_id=organization.id, document_id=document.id
    )
    assert deleted is True

    result = await knowledge_service.get_document(
        db_session, organization_id=organization.id, document_id=document.id
    )
    assert result is None


async def test_delete_document_returns_false_for_another_organization(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_organization(db_session, "Riverside Dental")
    org_b = await _make_organization(db_session, "Lakeside Clinic")
    document = await knowledge_service.create_document(
        db_session, organization_id=org_a.id, title="A doc", content="Org A content."
    )

    deleted = await knowledge_service.delete_document(
        db_session, organization_id=org_b.id, document_id=document.id
    )
    assert deleted is False

    # The document is untouched, still resolvable within its own organization.
    result = await knowledge_service.get_document(
        db_session, organization_id=org_a.id, document_id=document.id
    )
    assert result is not None


async def test_delete_document_returns_false_for_unknown_id(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    deleted = await knowledge_service.delete_document(
        db_session, organization_id=organization.id, document_id=uuid.uuid4()
    )
    assert deleted is False


# --- search --------------------------------------------------------------


async def _make_fixture_documents(
    db_session: AsyncSession, organization_id: uuid.UUID
) -> dict[str, KnowledgeDocument]:
    """A small fixture set with clearly relevant and clearly irrelevant
    documents for a "parking" query, plus one that mentions parking only
    in passing so ranking has something to differentiate.
    """
    strongly_relevant = await knowledge_service.create_document(
        db_session,
        organization_id=organization_id,
        title="Parking",
        content=(
            "Free parking is available in the lot behind the building. "
            "Parking is open 24 hours a day and no permit is required."
        ),
    )
    weakly_relevant = await knowledge_service.create_document(
        db_session,
        organization_id=organization_id,
        title="Getting here",
        content=(
            "We are located downtown near the transit center. Street "
            "parking can be found nearby but fills up quickly."
        ),
    )
    irrelevant = await knowledge_service.create_document(
        db_session,
        organization_id=organization_id,
        title="Payment",
        content="We accept all major credit cards and mobile payments.",
    )
    return {
        "strongly_relevant": strongly_relevant,
        "weakly_relevant": weakly_relevant,
        "irrelevant": irrelevant,
    }


async def test_search_ranks_more_relevant_document_first(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)
    docs = await _make_fixture_documents(db_session, organization.id)

    results = await knowledge_service.search(db_session, organization.id, "parking")

    result_ids = [d.id for d in results]
    assert docs["strongly_relevant"].id in result_ids
    assert docs["irrelevant"].id not in result_ids
    # The document that mentions "parking" twice outranks the one that
    # mentions it once.
    assert result_ids.index(docs["strongly_relevant"].id) < result_ids.index(
        docs["weakly_relevant"].id
    )


async def test_search_excludes_documents_with_no_lexeme_overlap(
    db_session: AsyncSession,
) -> None:
    organization = await _make_organization(db_session)
    docs = await _make_fixture_documents(db_session, organization.id)

    results = await knowledge_service.search(db_session, organization.id, "credit card payment")

    result_ids = [d.id for d in results]
    assert result_ids == [docs["irrelevant"].id]


async def test_search_scopes_to_organization(db_session: AsyncSession) -> None:
    org_a = await _make_organization(db_session, "Riverside Dental")
    org_b = await _make_organization(db_session, "Lakeside Clinic")

    await knowledge_service.create_document(
        db_session,
        organization_id=org_a.id,
        title="Parking",
        content="Free parking is available in the lot.",
    )
    await knowledge_service.create_document(
        db_session,
        organization_id=org_b.id,
        title="Parking",
        content="Free parking is available in the lot.",
    )

    results = await knowledge_service.search(db_session, org_a.id, "parking")
    assert len(results) == 1
    assert results[0].organization_id == org_a.id


async def test_search_respects_limit(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)
    for i in range(8):
        await knowledge_service.create_document(
            db_session,
            organization_id=organization.id,
            title=f"Parking note {i}",
            content="Free parking is available in the lot behind the building.",
        )

    results = await knowledge_service.search(db_session, organization.id, "parking", limit=3)
    assert len(results) == 3


async def test_search_empty_query_returns_empty_list_not_error(
    db_session: AsyncSession,
) -> None:
    organization = await _make_organization(db_session)
    await _make_fixture_documents(db_session, organization.id)

    results = await knowledge_service.search(db_session, organization.id, "")
    assert results == []


async def test_search_whitespace_only_query_returns_empty_list(
    db_session: AsyncSession,
) -> None:
    organization = await _make_organization(db_session)
    await _make_fixture_documents(db_session, organization.id)

    results = await knowledge_service.search(db_session, organization.id, "   ")
    assert results == []


async def test_search_no_match_query_returns_empty_list_not_error(
    db_session: AsyncSession,
) -> None:
    organization = await _make_organization(db_session)
    await _make_fixture_documents(db_session, organization.id)

    results = await knowledge_service.search(
        db_session, organization.id, "spaceship dinosaur volcano"
    )
    assert results == []


async def test_search_with_no_documents_returns_empty_list(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    results = await knowledge_service.search(db_session, organization.id, "parking")
    assert results == []
