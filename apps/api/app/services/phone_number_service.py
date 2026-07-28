"""PhoneNumber CRUD: which Twilio number(s) belong to an organization.

See docs/TDD.md section 3.6: inbound voice/SMS webhooks resolve the owning
`Organization` by looking up the `To` number in this table
(app/services/conversation_service.py already reads it for that purpose).
This module is the dashboard-facing write side: staff register the Twilio
numbers they own here.

A phone number is globally unique, not scoped per organization (unlike a
`Contact`'s number, see tests/test_tenant_scoped_constraints.py): two
organizations can never claim the same Twilio number, since a webhook's
`To` number must resolve to exactly one organization.

None of these functions call `session.commit()`; they `flush()` so a
newly-created row gets its generated primary key and is visible to
subsequent queries within the same session, matching the convention in
knowledge_service.py and conversation_service.py. Committing is left to the
caller (app/api/phone_numbers.py).
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.phone_number import PhoneNumber

# E.164: a leading `+`, then a 1-15 digit number whose first digit is 1-9
# (no leading zero). See https://www.itu.int/rec/T-REC-E.164.
_E164_PATTERN = re.compile(r"^\+[1-9]\d{1,14}$")


class PhoneNumberServiceError(Exception):
    """Base class for phone_number_service errors."""


class InvalidE164NumberError(PhoneNumberServiceError):
    """Raised when a candidate number is not valid E.164."""


class DuplicatePhoneNumberError(PhoneNumberServiceError):
    """Raised when a number is already registered, to this organization or
    another one.
    """


def is_valid_e164(number: str) -> bool:
    """Whether `number` is a syntactically valid E.164 number."""
    return bool(_E164_PATTERN.match(number))


async def create(
    session: AsyncSession,
    organization_id: uuid.UUID,
    e164_number: str,
) -> PhoneNumber:
    """Register `e164_number` as belonging to `organization_id`.

    Raises `InvalidE164NumberError` for a malformed number, and
    `DuplicatePhoneNumberError` if the number is already registered to any
    organization (the database's unique constraint on
    `phone_numbers.e164_number` is the source of truth for this; the
    IntegrityError it raises on a racing duplicate insert is caught and
    translated here rather than relying solely on a pre-check, which would
    leave a TOCTOU gap).
    """
    if not is_valid_e164(e164_number):
        raise InvalidE164NumberError(
            f"{e164_number!r} is not a valid E.164 phone number"
        )

    phone_number = PhoneNumber(organization_id=organization_id, e164_number=e164_number)
    session.add(phone_number)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicatePhoneNumberError(
            f"{e164_number} is already registered to an organization"
        ) from exc
    return phone_number


async def list_for_organization(
    session: AsyncSession, organization_id: uuid.UUID
) -> list[PhoneNumber]:
    """List every phone number belonging to `organization_id`, newest first."""
    result = await session.execute(
        select(PhoneNumber)
        .where(PhoneNumber.organization_id == organization_id)
        .order_by(PhoneNumber.created_at.desc())
    )
    return list(result.scalars().all())


async def get(
    session: AsyncSession, organization_id: uuid.UUID, phone_number_id: uuid.UUID
) -> PhoneNumber | None:
    """Fetch a single phone number, scoped to `organization_id`.

    Returns `None` both when no phone number with `phone_number_id` exists
    at all and when one exists but belongs to a different organization, so
    callers can't distinguish "not found" from "not yours"
    (see app/api/phone_numbers.py).
    """
    result = await session.execute(
        select(PhoneNumber).where(
            PhoneNumber.id == phone_number_id,
            PhoneNumber.organization_id == organization_id,
        )
    )
    return result.scalar_one_or_none()


async def delete(
    session: AsyncSession, organization_id: uuid.UUID, phone_number_id: uuid.UUID
) -> bool:
    """Delete a phone number, scoped to `organization_id`.

    Returns `True` if a phone number was found (within this organization)
    and deleted, `False` if no such phone number exists for this
    organization.
    """
    phone_number = await get(session, organization_id, phone_number_id)
    if phone_number is None:
        return False

    await session.delete(phone_number)
    await session.flush()
    return True
