"""Integration tests for GET /v1/analytics/overview, run against a real
(test) Postgres via the ``client``/``db_session`` fixtures (see conftest.py).

The aggregation math itself (call/SMS volume, appointments booked, average
conversation length) is covered by tests/test_analytics_service.py against
the service function directly; these tests are about the HTTP layer:
session auth, org scoping, and query-param handling.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Appointment, Contact, Conversation, Message

_RANGE_START = date(2026, 7, 1)
_RANGE_END = date(2026, 7, 31)
_IN_RANGE = datetime(2026, 7, 15, tzinfo=UTC)
_BEFORE_RANGE = datetime(2026, 6, 1, tzinfo=UTC)


async def _register(client: AsyncClient, name: str = "Riverside Dental") -> tuple[str, str]:
    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
    response = await client.post(
        "/v1/auth/register",
        json={
            "organization_name": name,
            "email": email,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["user"]["organization_id"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _find_or_create_contact(
    db_session: AsyncSession, org_uuid: uuid.UUID, e164_number: str
) -> Contact:
    """Reuses a contact if one already exists for (org, number), same
    uniqueness the real find_or_create_contact enforces (see
    app/services/conversation_service.py), so seeding two conversations for
    the same organization in one test doesn't collide on
    uq_contacts_org_e164_number.
    """
    result = await db_session.execute(
        select(Contact).where(
            Contact.organization_id == org_uuid, Contact.e164_number == e164_number
        )
    )
    contact = result.scalar_one_or_none()
    if contact is not None:
        return contact
    contact = Contact(organization_id=org_uuid, e164_number=e164_number)
    db_session.add(contact)
    await db_session.flush()
    return contact


async def _seed_conversation(
    db_session: AsyncSession,
    organization_id: str,
    channel: str,
    created_at: datetime,
    message_count: int = 0,
) -> Conversation:
    org_uuid = uuid.UUID(organization_id)
    contact = await _find_or_create_contact(db_session, org_uuid, "+15551234567")
    conversation = Conversation(
        organization_id=org_uuid, contact_id=contact.id, channel=channel, status="closed"
    )
    db_session.add(conversation)
    await db_session.flush()
    conversation.created_at = created_at
    for i in range(message_count):
        db_session.add(
            Message(conversation_id=conversation.id, role="user", content=f"message {i}")
        )
    await db_session.flush()
    return conversation


async def _seed_appointment(
    db_session: AsyncSession, organization_id: str, created_at: datetime
) -> Appointment:
    org_uuid = uuid.UUID(organization_id)
    contact = await _find_or_create_contact(db_session, org_uuid, "+15557654321")
    appointment = Appointment(
        organization_id=org_uuid,
        contact_id=contact.id,
        scheduled_at=created_at + timedelta(days=1),
        duration_minutes=30,
        status="scheduled",
    )
    db_session.add(appointment)
    await db_session.flush()
    appointment.created_at = created_at
    await db_session.flush()
    return appointment


async def test_get_overview_requires_session_auth(client: AsyncClient) -> None:
    response = await client.get(
        "/v1/analytics/overview",
        params={"start_date": str(_RANGE_START), "end_date": str(_RANGE_END)},
    )

    assert response.status_code == 401


async def test_get_overview_returns_aggregate_metrics(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    await _seed_conversation(db_session, org_id, "voice", _IN_RANGE, message_count=2)
    await _seed_conversation(db_session, org_id, "sms", _IN_RANGE, message_count=4)
    await _seed_appointment(db_session, org_id, _IN_RANGE)

    response = await client.get(
        "/v1/analytics/overview",
        params={"start_date": str(_RANGE_START), "end_date": str(_RANGE_END)},
        headers=_auth(access_token),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["start_date"] == str(_RANGE_START)
    assert body["end_date"] == str(_RANGE_END)
    assert body["call_volume"] == 1
    assert body["sms_volume"] == 1
    assert body["appointments_booked"] == 1
    assert body["average_conversation_length"] == 3.0


async def test_get_overview_is_scoped_per_organization(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token_a, org_id_a = await _register(client, "Org A")
    access_token_b, org_id_b = await _register(client, "Org B")
    await _seed_conversation(db_session, org_id_a, "voice", _IN_RANGE)
    await _seed_conversation(db_session, org_id_b, "voice", _IN_RANGE)
    await _seed_conversation(db_session, org_id_b, "voice", _IN_RANGE)

    response_a = await client.get(
        "/v1/analytics/overview",
        params={"start_date": str(_RANGE_START), "end_date": str(_RANGE_END)},
        headers=_auth(access_token_a),
    )
    response_b = await client.get(
        "/v1/analytics/overview",
        params={"start_date": str(_RANGE_START), "end_date": str(_RANGE_END)},
        headers=_auth(access_token_b),
    )

    assert response_a.status_code == 200
    assert response_a.json()["call_volume"] == 1
    assert response_b.status_code == 200
    assert response_b.json()["call_volume"] == 2


async def test_get_overview_excludes_data_outside_the_range(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    await _seed_conversation(db_session, org_id, "voice", _IN_RANGE)
    await _seed_conversation(db_session, org_id, "voice", _BEFORE_RANGE)

    response = await client.get(
        "/v1/analytics/overview",
        params={"start_date": str(_RANGE_START), "end_date": str(_RANGE_END)},
        headers=_auth(access_token),
    )

    assert response.status_code == 200
    assert response.json()["call_volume"] == 1


async def test_get_overview_missing_query_params_returns_422(client: AsyncClient) -> None:
    access_token, _ = await _register(client)

    response = await client.get("/v1/analytics/overview", headers=_auth(access_token))

    assert response.status_code == 422


async def test_get_overview_end_date_before_start_date_returns_422(client: AsyncClient) -> None:
    access_token, _ = await _register(client)

    response = await client.get(
        "/v1/analytics/overview",
        params={"start_date": "2026-07-31", "end_date": "2026-07-01"},
        headers=_auth(access_token),
    )

    assert response.status_code == 422
