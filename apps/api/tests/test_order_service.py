"""Unit tests for app.services.order_service, run directly against the ORM
(not through the /v1/orders HTTP routes, those are covered in
tests/test_orders_routes.py) against a real (migrated) Postgres database,
see conftest.py.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Organization
from app.services import order_service


async def _make_org(db_session: AsyncSession, name: str = "Riverside Dental") -> Organization:
    organization = Organization(name=name)
    db_session.add(organization)
    await db_session.flush()
    return organization


async def _make_contact(
    db_session: AsyncSession, organization_id: uuid.UUID, e164_number: str
) -> Contact:
    contact = Contact(organization_id=organization_id, e164_number=e164_number)
    db_session.add(contact)
    await db_session.flush()
    return contact


# --- create -------------------------------------------------------------


async def test_create_creates_contact_and_order_when_contact_is_new(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)

    order = await order_service.create(
        db_session,
        organization_id=organization.id,
        order_number="ORD-1",
        item_description="2x ceramic mug",
        contact_phone_number="+15551234567",
    )

    assert order.order_number == "ORD-1"
    assert order.status == "pending"
    assert order.contact.e164_number == "+15551234567"


async def test_create_reuses_existing_contact_for_same_phone_number(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    existing_contact = await _make_contact(db_session, organization.id, "+15551234567")

    order = await order_service.create(
        db_session,
        organization_id=organization.id,
        order_number="ORD-2",
        item_description="1x pour-over kit",
        contact_phone_number="+15551234567",
    )

    assert order.contact_id == existing_contact.id


# --- list_for_organization ------------------------------------------------


async def test_list_for_organization_only_returns_that_organizations_orders(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_org(db_session, "Org A")
    org_b = await _make_org(db_session, "Org B")

    await order_service.create(
        db_session,
        organization_id=org_a.id,
        order_number="ORD-A1",
        item_description="A's item",
        contact_phone_number="+15551110000",
    )
    await order_service.create(
        db_session,
        organization_id=org_b.id,
        order_number="ORD-B1",
        item_description="B's item",
        contact_phone_number="+15552220000",
    )

    org_a_orders = await order_service.list_for_organization(db_session, org_a.id)

    assert [order.order_number for order in org_a_orders] == ["ORD-A1"]


# --- get --------------------------------------------------------------------


async def test_get_returns_order_scoped_to_organization(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    created = await order_service.create(
        db_session,
        organization_id=organization.id,
        order_number="ORD-3",
        item_description="1x kettle",
        contact_phone_number="+15551234567",
    )

    fetched = await order_service.get(db_session, organization.id, created.id)

    assert fetched is not None
    assert fetched.id == created.id


async def test_get_returns_none_for_order_belonging_to_another_organization(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_org(db_session, "Org A")
    org_b = await _make_org(db_session, "Org B")
    created = await order_service.create(
        db_session,
        organization_id=org_a.id,
        order_number="ORD-4",
        item_description="A's item",
        contact_phone_number="+15551110000",
    )

    fetched = await order_service.get(db_session, org_b.id, created.id)

    assert fetched is None


async def test_get_returns_none_for_unknown_order_id(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)

    fetched = await order_service.get(db_session, organization.id, uuid.uuid4())

    assert fetched is None


# --- lookup -------------------------------------------------------------


async def test_lookup_matches_by_order_number(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    await order_service.create(
        db_session,
        organization_id=organization.id,
        order_number="ORD-100",
        item_description="1x french press",
        contact_phone_number="+15551234567",
    )

    result = await order_service.lookup(
        db_session, organization.id, order_number="ORD-100"
    )

    assert result.found is True
    assert result.order is not None
    assert result.order.order_number == "ORD-100"


async def test_lookup_matches_by_phone_number(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    await order_service.create(
        db_session,
        organization_id=organization.id,
        order_number="ORD-101",
        item_description="1x grinder",
        contact_phone_number="+15559998888",
    )

    result = await order_service.lookup(
        db_session, organization.id, phone_number="+15559998888"
    )

    assert result.found is True
    assert result.order is not None
    assert result.order.order_number == "ORD-101"


async def test_lookup_by_phone_number_returns_most_recent_order_when_contact_has_several(
    db_session: AsyncSession,
) -> None:
    # Postgres's `now()` (what `Order.created_at`'s server default uses)
    # returns the enclosing transaction's start time, not the statement
    # time, so two inserts made in the same test transaction (as these are,
    # via the shared `db_session` fixture) would otherwise get an identical
    # `created_at` and make "most recent" ordering nondeterministic here even
    # though it is well-defined across separate, real requests in
    # production. Setting `created_at` explicitly keeps this test about
    # `lookup`'s ordering logic rather than about Postgres transaction
    # semantics.
    organization = await _make_org(db_session)
    older = await order_service.create(
        db_session,
        organization_id=organization.id,
        order_number="ORD-200",
        item_description="first order",
        contact_phone_number="+15551112222",
    )
    most_recent = await order_service.create(
        db_session,
        organization_id=organization.id,
        order_number="ORD-201",
        item_description="second, more recent order",
        contact_phone_number="+15551112222",
    )
    older.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    most_recent.created_at = datetime(2026, 1, 2, tzinfo=UTC)
    await db_session.flush()

    result = await order_service.lookup(
        db_session, organization.id, phone_number="+15551112222"
    )

    assert result.found is True
    assert result.order is not None
    assert result.order.id == most_recent.id


async def test_lookup_returns_not_found_when_order_number_does_not_match(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)

    result = await order_service.lookup(
        db_session, organization.id, order_number="does-not-exist"
    )

    assert result.found is False
    assert result.order is None


async def test_lookup_returns_not_found_when_phone_number_does_not_match(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)

    result = await order_service.lookup(
        db_session, organization.id, phone_number="+19995550000"
    )

    assert result.found is False
    assert result.order is None


async def test_lookup_returns_not_found_when_neither_argument_is_given(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)

    result = await order_service.lookup(db_session, organization.id)

    assert result.found is False
    assert result.order is None


async def test_lookup_is_scoped_to_organization_for_order_number(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_org(db_session, "Org A")
    org_b = await _make_org(db_session, "Org B")
    await order_service.create(
        db_session,
        organization_id=org_a.id,
        order_number="ORD-SHARED",
        item_description="A's item",
        contact_phone_number="+15551110000",
    )

    result = await order_service.lookup(db_session, org_b.id, order_number="ORD-SHARED")

    assert result.found is False


async def test_lookup_is_scoped_to_organization_for_phone_number(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_org(db_session, "Org A")
    org_b = await _make_org(db_session, "Org B")
    await order_service.create(
        db_session,
        organization_id=org_a.id,
        order_number="ORD-SHARED-2",
        item_description="A's item",
        contact_phone_number="+15551110000",
    )

    result = await order_service.lookup(db_session, org_b.id, phone_number="+15551110000")

    assert result.found is False


async def test_lookup_prefers_order_number_match_over_phone_number(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    by_number = await order_service.create(
        db_session,
        organization_id=organization.id,
        order_number="ORD-300",
        item_description="the one that should match",
        contact_phone_number="+15551112222",
    )
    await order_service.create(
        db_session,
        organization_id=organization.id,
        order_number="ORD-301",
        item_description="a different order for the same phone number",
        contact_phone_number="+15553334444",
    )

    result = await order_service.lookup(
        db_session,
        organization.id,
        order_number="ORD-300",
        phone_number="+15553334444",
    )

    assert result.found is True
    assert result.order is not None
    assert result.order.id == by_number.id
