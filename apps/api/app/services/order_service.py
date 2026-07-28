"""Order CRUD and lookup, backing the dashboard's /v1/orders routes and the
agent's ``lookup_order`` tool (see docs/TDD.md section 3.2).

Orders in EchoFlow are seeded by staff via the dashboard API rather than
synced from a real order system (see docs/PRD.md non-goals and
app/models/order.py), so ``create`` also resolves the owning `Contact` by
phone number, finding-or-creating it the same way
``conversation_service.find_or_create_contact`` does for inbound webhooks.

None of these functions call ``session.commit()``; they ``flush()`` so a
newly-created row gets its generated primary key and is visible to
subsequent queries within the same session, but committing is left to the
caller (the route handler), same convention as conversation_service.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.contact import Contact
from app.models.order import Order


@dataclass(frozen=True)
class OrderLookupResult:
    """The outcome of `lookup`: exactly one of `order` or `not_found` is
    meaningful, so callers (including the agent's `lookup_order` tool) can
    branch on `found` and never need to catch an exception for the
    ordinary, expected "no such order" case.
    """

    found: bool
    order: Order | None = None


async def create(
    session: AsyncSession,
    organization_id: uuid.UUID,
    order_number: str,
    item_description: str,
    contact_phone_number: str,
    status: str = "pending",
) -> Order:
    """Create an `Order` for `organization_id`, resolving (or creating) the
    `Contact` for `contact_phone_number` within that organization first, the
    same find-or-create-by-phone-number pattern
    `conversation_service.find_or_create_contact` uses for inbound webhooks.
    """
    contact = await _find_or_create_contact(session, organization_id, contact_phone_number)

    order = Order(
        organization_id=organization_id,
        contact_id=contact.id,
        order_number=order_number,
        item_description=item_description,
        status=status,
    )
    session.add(order)
    await session.flush()
    await session.refresh(order, attribute_names=["contact"])
    return order


async def list_for_organization(session: AsyncSession, organization_id: uuid.UUID) -> list[Order]:
    """All orders belonging to `organization_id`, most recently created first.

    Eager-loads `contact` (`selectinload`, a second query rather than a
    JOIN) since route handlers read `order.contact.e164_number` to build
    the response, and implicit lazy loading is not safe to trigger from
    ordinary attribute access under SQLAlchemy's async engine.
    """
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.contact))
        .where(Order.organization_id == organization_id)
        .order_by(Order.created_at.desc())
    )
    return list(result.scalars().all())


async def get(
    session: AsyncSession, organization_id: uuid.UUID, order_id: uuid.UUID
) -> Order | None:
    """A single order, scoped to `organization_id` so one org can never read
    (or even distinguish "not found" from "not yours" for) another org's
    order by guessing its id. Eager-loads `contact`, see
    `list_for_organization` for why.
    """
    result = await session.execute(
        select(Order)
        .options(selectinload(Order.contact))
        .where(Order.id == order_id, Order.organization_id == organization_id)
    )
    return result.scalar_one_or_none()


async def lookup(
    session: AsyncSession,
    organization_id: uuid.UUID,
    order_number: str | None = None,
    phone_number: str | None = None,
) -> OrderLookupResult:
    """Resolve an order by `order_number` or by the contact's
    `phone_number`, scoped to `organization_id`, for the agent's
    `lookup_order` tool (see docs/TDD.md section 3.3).

    Returns an `OrderLookupResult` rather than raising on no match, per the
    issue's acceptance criteria: "no match returns a clear not-found result
    (not an exception) so the calling tool can report 'no order found' back
    to the caller." Requires at least one of `order_number` or
    `phone_number`; when both are given, `order_number` is tried first and
    `phone_number` is only used as a fallback if it does not match, since an
    order number identifies a single order while a phone number may have
    placed several.
    """
    if not order_number and not phone_number:
        return OrderLookupResult(found=False)

    if order_number:
        result = await session.execute(
            select(Order)
            .options(selectinload(Order.contact))
            .where(
                Order.organization_id == organization_id,
                Order.order_number == order_number,
            )
        )
        order = result.scalar_one_or_none()
        if order is not None:
            return OrderLookupResult(found=True, order=order)

    if phone_number:
        result = await session.execute(
            select(Order)
            .options(selectinload(Order.contact))
            .join(Contact, Contact.id == Order.contact_id)
            .where(
                Order.organization_id == organization_id,
                Contact.e164_number == phone_number,
            )
            .order_by(Order.created_at.desc())
        )
        order = result.scalars().first()
        if order is not None:
            return OrderLookupResult(found=True, order=order)

    return OrderLookupResult(found=False)


async def _find_or_create_contact(
    session: AsyncSession, organization_id: uuid.UUID, phone_number: str
) -> Contact:
    result = await session.execute(
        select(Contact).where(
            Contact.organization_id == organization_id,
            Contact.e164_number == phone_number,
        )
    )
    contact = result.scalar_one_or_none()
    if contact is not None:
        return contact

    contact = Contact(organization_id=organization_id, e164_number=phone_number)
    session.add(contact)
    await session.flush()
    return contact
