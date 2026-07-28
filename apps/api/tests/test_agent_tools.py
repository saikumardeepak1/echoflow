"""Tests for app.agent.tools: each tool function against a real (migrated)
Postgres database (see conftest.py), same convention as the service tests
these tools wrap. `book_appointment` also needs a real Redis, since
`appointment_service.book` enqueues a confirmation job (see
tests/test_appointment_service.py's redis_queue fixture, reused here).

The point of these tests is that each tool function correctly delegates to
its underlying service with organization/contact scoping intact and shapes
the result into a plain dict suitable for a Gemini function response, not
to re-verify the services' own business logic (already covered by
test_knowledge_service.py, test_order_service.py, and
test_appointment_service.py).
"""

import uuid
from collections.abc import Generator
from datetime import UTC, date, datetime, time

import pytest
from redis import Redis
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import tools
from app.core.config import settings
from app.models import Contact, KnowledgeDocument, Order, Organization
from app.services import appointment_service


@pytest.fixture
def redis_queue() -> Generator[Queue, None, None]:
    connection = Redis.from_url(settings.redis_url)
    queue = Queue("default", connection=connection)
    queue.empty()  # type: ignore[no-untyped-call]
    yield queue
    queue.empty()  # type: ignore[no-untyped-call]


async def _make_org(db_session: AsyncSession, name: str = "Riverside Dental") -> Organization:
    organization = Organization(name=name)
    db_session.add(organization)
    await db_session.flush()
    return organization


async def _make_contact(
    db_session: AsyncSession, organization_id: uuid.UUID, e164_number: str = "+15551234567"
) -> Contact:
    contact = Contact(organization_id=organization_id, e164_number=e164_number)
    db_session.add(contact)
    await db_session.flush()
    return contact


# --- knowledge_search --------------------------------------------------------


async def test_knowledge_search_returns_matching_documents(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    db_session.add(
        KnowledgeDocument(
            organization_id=organization.id,
            title="Parking",
            content="Free parking is available in the lot behind the building.",
        )
    )
    await db_session.flush()

    result = await tools.knowledge_search(
        db_session, organization.id, contact.id, query="parking"
    )

    assert result["results"] == [
        {
            "title": "Parking",
            "content": "Free parking is available in the lot behind the building.",
        }
    ]


async def test_knowledge_search_no_match_returns_empty_results(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    result = await tools.knowledge_search(
        db_session, organization.id, contact.id, query="nonexistent gibberish topic"
    )

    assert result == {"results": []}


async def test_knowledge_search_is_scoped_per_organization(db_session: AsyncSession) -> None:
    organization_a = await _make_org(db_session, "Org A")
    organization_b = await _make_org(db_session, "Org B")
    contact_a = await _make_contact(db_session, organization_a.id)
    db_session.add(
        KnowledgeDocument(
            organization_id=organization_b.id,
            title="Hours",
            content="We are open every day of the week including holidays.",
        )
    )
    await db_session.flush()

    result = await tools.knowledge_search(
        db_session, organization_a.id, contact_a.id, query="holidays"
    )

    assert result == {"results": []}


# --- lookup_order -------------------------------------------------------------


async def test_lookup_order_found_by_order_number(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    db_session.add(
        Order(
            organization_id=organization.id,
            contact_id=contact.id,
            order_number="ORD-1",
            item_description="2x ceramic mug",
            status="shipped",
        )
    )
    await db_session.flush()

    result = await tools.lookup_order(
        db_session, organization.id, contact.id, order_number="ORD-1"
    )

    assert result == {
        "found": True,
        "order_number": "ORD-1",
        "status": "shipped",
        "item_description": "2x ceramic mug",
    }


async def test_lookup_order_found_by_phone_number(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id, "+15559876543")
    db_session.add(
        Order(
            organization_id=organization.id,
            contact_id=contact.id,
            order_number="ORD-2",
            item_description="1x travel mug",
            status="pending",
        )
    )
    await db_session.flush()

    result = await tools.lookup_order(
        db_session, organization.id, contact.id, phone_number="+15559876543"
    )

    assert result["found"] is True
    assert result["order_number"] == "ORD-2"


async def test_lookup_order_not_found_returns_found_false(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    result = await tools.lookup_order(
        db_session, organization.id, contact.id, order_number="NO-SUCH-ORDER"
    )

    assert result == {"found": False}


# --- check_availability --------------------------------------------------------

# A Tuesday, matching tests/test_appointment_service.py's convention.
_TUESDAY = date(2026, 7, 28)
assert _TUESDAY.weekday() == 1


async def test_check_availability_returns_the_open_window(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )

    result = await tools.check_availability(
        db_session, organization.id, contact.id, date=_TUESDAY.isoformat()
    )

    assert result == {
        "slots": [
            {
                "start": datetime.combine(_TUESDAY, time(9, 0), tzinfo=UTC).isoformat(),
                "end": datetime.combine(_TUESDAY, time(17, 0), tzinfo=UTC).isoformat(),
            }
        ]
    }


async def test_check_availability_no_business_hours_returns_no_slots(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    result = await tools.check_availability(
        db_session, organization.id, contact.id, date=_TUESDAY.isoformat()
    )

    assert result == {"slots": []}


# --- book_appointment -----------------------------------------------------------


async def test_book_appointment_success(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    scheduled_at = datetime.combine(_TUESDAY, time(10, 0), tzinfo=UTC)

    result = await tools.book_appointment(
        db_session,
        organization.id,
        contact.id,
        scheduled_at=scheduled_at.isoformat(),
        duration_minutes=30,
        notes="First-time customer",
    )

    assert result["booked"] is True
    assert result["duration_minutes"] == 30
    assert uuid.UUID(str(result["appointment_id"]))
    assert redis_queue.count == 1


async def test_book_appointment_outside_business_hours_returns_error(
    db_session: AsyncSession, redis_queue: Queue
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    await appointment_service.set_business_hours(
        db_session, organization.id, day_of_week=1, opens_at=time(9, 0), closes_at=time(17, 0)
    )
    scheduled_at = datetime.combine(_TUESDAY, time(20, 0), tzinfo=UTC)

    result = await tools.book_appointment(
        db_session,
        organization.id,
        contact.id,
        scheduled_at=scheduled_at.isoformat(),
        duration_minutes=30,
    )

    assert result["booked"] is False
    assert "error" in result
    assert redis_queue.count == 0


# --- execute_tool dispatch -------------------------------------------------------


async def test_execute_tool_dispatches_to_the_named_tool(db_session: AsyncSession) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)
    db_session.add(
        Order(
            organization_id=organization.id,
            contact_id=contact.id,
            order_number="ORD-3",
            item_description="1x tumbler",
            status="pending",
        )
    )
    await db_session.flush()

    result = await tools.execute_tool(
        db_session, organization.id, contact.id, "lookup_order", {"order_number": "ORD-3"}
    )

    assert result["found"] is True
    assert result["order_number"] == "ORD-3"


async def test_execute_tool_unknown_name_returns_error_without_raising(
    db_session: AsyncSession,
) -> None:
    organization = await _make_org(db_session)
    contact = await _make_contact(db_session, organization.id)

    result = await tools.execute_tool(
        db_session, organization.id, contact.id, "delete_everything", {}
    )

    assert result == {"error": "Unknown tool: delete_everything"}
