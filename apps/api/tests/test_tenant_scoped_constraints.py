"""Tests for the schema's uniqueness constraints: which ones are scoped per
organization (a contact's number, an order number, a business-hours weekday)
versus global (a user's email, an API key hash, a phone number an
organization owns).
"""

from datetime import time

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BusinessHours, Contact, Order, Organization


async def _make_two_organizations(
    db_session: AsyncSession,
) -> tuple[Organization, Organization]:
    first = Organization(name="First Org")
    second = Organization(name="Second Org")
    db_session.add_all([first, second])
    await db_session.flush()
    return first, second


async def test_same_phone_number_can_be_a_contact_in_two_different_organizations(
    db_session: AsyncSession,
) -> None:
    first_org, second_org = await _make_two_organizations(db_session)

    db_session.add_all(
        [
            Contact(organization_id=first_org.id, e164_number="+15551112222"),
            Contact(organization_id=second_org.id, e164_number="+15551112222"),
        ]
    )
    # No IntegrityError: the unique constraint is scoped to (organization_id,
    # e164_number), not e164_number alone, since the same caller can text two
    # different businesses.
    await db_session.commit()


async def test_duplicate_contact_number_within_the_same_organization_is_rejected(
    db_session: AsyncSession,
) -> None:
    organization = Organization(name="Duplicate Contact Co")
    db_session.add(organization)
    await db_session.flush()

    db_session.add_all(
        [
            Contact(organization_id=organization.id, e164_number="+15553334444"),
            Contact(organization_id=organization.id, e164_number="+15553334444"),
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_same_order_number_can_be_reused_across_organizations(
    db_session: AsyncSession,
) -> None:
    first_org, second_org = await _make_two_organizations(db_session)

    first_contact = Contact(organization_id=first_org.id, e164_number="+15550000001")
    second_contact = Contact(organization_id=second_org.id, e164_number="+15550000002")
    db_session.add_all([first_contact, second_contact])
    await db_session.flush()

    db_session.add_all(
        [
            Order(
                organization_id=first_org.id,
                contact_id=first_contact.id,
                order_number="ORD-1",
                status="pending",
                item_description="Widget",
            ),
            Order(
                organization_id=second_org.id,
                contact_id=second_contact.id,
                order_number="ORD-1",
                status="pending",
                item_description="Gadget",
            ),
        ]
    )
    await db_session.commit()


async def test_duplicate_order_number_within_the_same_organization_is_rejected(
    db_session: AsyncSession,
) -> None:
    organization = Organization(name="Duplicate Order Co")
    db_session.add(organization)
    await db_session.flush()

    contact = Contact(organization_id=organization.id, e164_number="+15550000003")
    db_session.add(contact)
    await db_session.flush()

    db_session.add_all(
        [
            Order(
                organization_id=organization.id,
                contact_id=contact.id,
                order_number="ORD-DUP",
                status="pending",
                item_description="Widget",
            ),
            Order(
                organization_id=organization.id,
                contact_id=contact.id,
                order_number="ORD-DUP",
                status="pending",
                item_description="Another widget",
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()


async def test_duplicate_business_hours_weekday_within_the_same_organization_is_rejected(
    db_session: AsyncSession,
) -> None:
    organization = Organization(name="Duplicate Hours Co")
    db_session.add(organization)
    await db_session.flush()

    db_session.add_all(
        [
            BusinessHours(
                organization_id=organization.id,
                day_of_week=1,
                opens_at=time(9, 0),
                closes_at=time(17, 0),
            ),
            BusinessHours(
                organization_id=organization.id,
                day_of_week=1,
                opens_at=time(10, 0),
                closes_at=time(18, 0),
            ),
        ]
    )
    with pytest.raises(IntegrityError):
        await db_session.commit()
