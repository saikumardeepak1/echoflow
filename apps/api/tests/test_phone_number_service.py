"""Tests for app.services.phone_number_service.

Runs against a real (migrated) Postgres database, see conftest.py.
`is_valid_e164` is exercised directly for the pure validation logic;
`create`/`list_for_organization`/`get`/`delete` are exercised against the
database, including the duplicate-across-organizations rejection that backs
the unique constraint on `phone_numbers.e164_number`.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Organization
from app.services import phone_number_service


async def _make_organization(
    db_session: AsyncSession, name: str = "Riverside Dental"
) -> Organization:
    organization = Organization(name=name)
    db_session.add(organization)
    await db_session.flush()
    return organization


# --- is_valid_e164 ------------------------------------------------------------


@pytest.mark.parametrize(
    "number",
    [
        "+15551234567",
        "+442071838750",
        "+12",  # minimum length: two digits total
        "+123456789012345",  # maximum length: 15 digits total
    ],
)
def test_is_valid_e164_accepts_valid_numbers(number: str) -> None:
    assert phone_number_service.is_valid_e164(number) is True


@pytest.mark.parametrize(
    "number",
    [
        "15551234567",  # missing leading +
        "+0551234567",  # leading zero after +
        "+1234567890123456",  # 16 digits, too long
        "+1 555 123 4567",  # spaces
        "+1-555-123-4567",  # dashes
        "not-a-number",
        "",
        "+",
    ],
)
def test_is_valid_e164_rejects_invalid_numbers(number: str) -> None:
    assert phone_number_service.is_valid_e164(number) is False


# --- create --------------------------------------------------------------


async def test_create_persists_and_scopes_to_organization(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    phone_number = await phone_number_service.create(
        db_session, organization_id=organization.id, e164_number="+15551234567"
    )

    assert phone_number.id is not None
    assert phone_number.organization_id == organization.id
    assert phone_number.e164_number == "+15551234567"


async def test_create_rejects_invalid_e164_format(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    with pytest.raises(phone_number_service.InvalidE164NumberError):
        await phone_number_service.create(
            db_session, organization_id=organization.id, e164_number="555-1234"
        )


async def test_create_rejects_duplicate_within_same_organization(
    db_session: AsyncSession,
) -> None:
    organization = await _make_organization(db_session)
    await phone_number_service.create(
        db_session, organization_id=organization.id, e164_number="+15551234567"
    )

    with pytest.raises(phone_number_service.DuplicatePhoneNumberError):
        await phone_number_service.create(
            db_session, organization_id=organization.id, e164_number="+15551234567"
        )


async def test_create_rejects_duplicate_across_organizations(db_session: AsyncSession) -> None:
    org_a = await _make_organization(db_session, "Riverside Dental")
    org_b = await _make_organization(db_session, "Lakeside Clinic")
    await phone_number_service.create(
        db_session, organization_id=org_a.id, e164_number="+15551234567"
    )

    with pytest.raises(phone_number_service.DuplicatePhoneNumberError):
        await phone_number_service.create(
            db_session, organization_id=org_b.id, e164_number="+15551234567"
        )


# --- list_for_organization -----------------------------------------------


async def test_list_for_organization_returns_only_the_callers_organization(
    db_session: AsyncSession,
) -> None:
    org_a = await _make_organization(db_session, "Riverside Dental")
    org_b = await _make_organization(db_session, "Lakeside Clinic")

    await phone_number_service.create(
        db_session, organization_id=org_a.id, e164_number="+15551234567"
    )
    await phone_number_service.create(
        db_session, organization_id=org_b.id, e164_number="+15557654321"
    )

    results = await phone_number_service.list_for_organization(
        db_session, organization_id=org_a.id
    )

    assert len(results) == 1
    assert results[0].e164_number == "+15551234567"


# --- get -------------------------------------------------------------------


async def test_get_returns_none_for_another_organization(db_session: AsyncSession) -> None:
    org_a = await _make_organization(db_session, "Riverside Dental")
    org_b = await _make_organization(db_session, "Lakeside Clinic")
    phone_number = await phone_number_service.create(
        db_session, organization_id=org_a.id, e164_number="+15551234567"
    )

    result = await phone_number_service.get(
        db_session, organization_id=org_b.id, phone_number_id=phone_number.id
    )
    assert result is None


async def test_get_returns_none_for_unknown_id(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    result = await phone_number_service.get(
        db_session, organization_id=organization.id, phone_number_id=uuid.uuid4()
    )
    assert result is None


# --- delete ------------------------------------------------------------------


async def test_delete_removes_row_and_returns_true(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)
    phone_number = await phone_number_service.create(
        db_session, organization_id=organization.id, e164_number="+15551234567"
    )

    deleted = await phone_number_service.delete(
        db_session, organization_id=organization.id, phone_number_id=phone_number.id
    )
    assert deleted is True

    result = await phone_number_service.get(
        db_session, organization_id=organization.id, phone_number_id=phone_number.id
    )
    assert result is None


async def test_delete_returns_false_for_another_organization(db_session: AsyncSession) -> None:
    org_a = await _make_organization(db_session, "Riverside Dental")
    org_b = await _make_organization(db_session, "Lakeside Clinic")
    phone_number = await phone_number_service.create(
        db_session, organization_id=org_a.id, e164_number="+15551234567"
    )

    deleted = await phone_number_service.delete(
        db_session, organization_id=org_b.id, phone_number_id=phone_number.id
    )
    assert deleted is False

    result = await phone_number_service.get(
        db_session, organization_id=org_a.id, phone_number_id=phone_number.id
    )
    assert result is not None


async def test_delete_returns_false_for_unknown_id(db_session: AsyncSession) -> None:
    organization = await _make_organization(db_session)

    deleted = await phone_number_service.delete(
        db_session, organization_id=organization.id, phone_number_id=uuid.uuid4()
    )
    assert deleted is False
