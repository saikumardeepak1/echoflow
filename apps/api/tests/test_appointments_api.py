"""Integration tests for the /v1/appointments and /v1/business-hours routes,
run against the real ASGI app, a real (test) Postgres via the ``client``/
``db_session`` fixtures (see conftest.py), and a real Redis via the
``redis_queue`` fixture, to confirm a booking actually enqueues the
confirmation job end to end through the HTTP layer, not just at the service
level (see tests/test_appointment_service.py for that).
"""

import uuid
from collections.abc import Generator
from datetime import date

import pytest
from httpx import AsyncClient
from redis import Redis
from rq import Queue
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Contact

# A Tuesday, so day_of_week 1 matches both the business-hours setup and the
# scheduled_at values under test.
_TUESDAY = date(2026, 7, 28)
assert _TUESDAY.weekday() == 1


@pytest.fixture
def redis_queue() -> Generator[Queue, None, None]:
    connection = Redis.from_url(settings.redis_url)
    queue = Queue("default", connection=connection)
    queue.empty()  # type: ignore[no-untyped-call]
    yield queue
    queue.empty()  # type: ignore[no-untyped-call]


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


async def _make_contact(db_session: AsyncSession, organization_id: str) -> str:
    contact = Contact(organization_id=uuid.UUID(organization_id), e164_number="+15551234567")
    db_session.add(contact)
    await db_session.flush()
    return str(contact.id)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- POST /v1/business-hours --------------------------------------------------


async def test_set_business_hours_requires_session_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
    )
    assert response.status_code == 401


async def test_set_business_hours_creates_the_window(client: AsyncClient) -> None:
    access_token, _ = await _register(client)

    response = await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["day_of_week"] == 1
    assert body["opens_at"] == "09:00:00"
    assert body["closes_at"] == "17:00:00"


async def test_set_business_hours_rejects_invalid_window(client: AsyncClient) -> None:
    access_token, _ = await _register(client)

    response = await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "17:00:00", "closes_at": "09:00:00"},
        headers=_auth(access_token),
    )

    assert response.status_code == 422


# --- POST /v1/appointments ----------------------------------------------------


async def test_create_appointment_within_business_hours_succeeds(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    contact_id = await _make_contact(db_session, org_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )

    response = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
            "notes": "First cleaning",
        },
        headers=_auth(access_token),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["contact_id"] == contact_id
    assert body["duration_minutes"] == 30
    assert body["status"] == "scheduled"
    assert body["notes"] == "First cleaning"


async def test_create_appointment_enqueues_confirmation_job(
    client: AsyncClient, db_session: AsyncSession, redis_queue: Queue
) -> None:
    access_token, org_id = await _register(client)
    contact_id = await _make_contact(db_session, org_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )

    response = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token),
    )
    assert response.status_code == 201, response.text
    appointment_id = response.json()["id"]

    assert redis_queue.count == 1
    job = redis_queue.jobs[0]
    assert job.func_name == "app.workers.jobs.notify_appointment"
    assert job.args == (appointment_id,)
    assert job.kwargs == {"kind": "confirmation"}


async def test_create_appointment_outside_business_hours_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    contact_id = await _make_contact(db_session, org_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )

    response = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T20:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token),
    )

    assert response.status_code == 422


async def test_create_appointment_conflicting_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    contact_id = await _make_contact(db_session, org_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )
    first = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 60,
        },
        headers=_auth(access_token),
    )
    assert first.status_code == 201

    response = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T10:30:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token),
    )

    assert response.status_code == 409


async def test_create_appointment_requires_session_auth(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await client.post(
        "/v1/appointments",
        json={
            "contact_id": str(uuid.uuid4()),
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
        },
    )
    assert response.status_code == 401


# --- GET /v1/appointments ------------------------------------------------------


async def test_list_appointments_is_scoped_per_organization(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token_a, org_a_id = await _register(client, "Riverside Dental")
    access_token_b, org_b_id = await _register(client, "Sunset Auto")
    contact_a = await _make_contact(db_session, org_a_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token_a),
    )
    await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_a,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token_a),
    )

    response_a = await client.get("/v1/appointments", headers=_auth(access_token_a))
    response_b = await client.get("/v1/appointments", headers=_auth(access_token_b))

    assert response_a.status_code == 200
    assert len(response_a.json()) == 1
    assert response_b.status_code == 200
    assert response_b.json() == []


async def test_list_appointments_filters_by_date(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    contact_id = await _make_contact(db_session, org_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )
    await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token),
    )

    matching = await client.get(
        "/v1/appointments", params={"date": str(_TUESDAY)}, headers=_auth(access_token)
    )
    non_matching = await client.get(
        "/v1/appointments", params={"date": "2026-07-29"}, headers=_auth(access_token)
    )

    assert len(matching.json()) == 1
    assert non_matching.json() == []


# --- PATCH /v1/appointments/{id} -----------------------------------------------


async def test_update_appointment_changes_status(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    contact_id = await _make_contact(db_session, org_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )
    created = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token),
    )
    appointment_id = created.json()["id"]

    response = await client.patch(
        f"/v1/appointments/{appointment_id}",
        json={"status": "cancelled"},
        headers=_auth(access_token),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"


async def test_update_appointment_unknown_id_returns_404(client: AsyncClient) -> None:
    access_token, _ = await _register(client)

    response = await client.patch(
        f"/v1/appointments/{uuid.uuid4()}",
        json={"status": "cancelled"},
        headers=_auth(access_token),
    )

    assert response.status_code == 404


async def test_update_appointment_belonging_to_another_organization_returns_404(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token_a, org_a_id = await _register(client, "Riverside Dental")
    access_token_b, _ = await _register(client, "Sunset Auto")
    contact_a = await _make_contact(db_session, org_a_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token_a),
    )
    created = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_a,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token_a),
    )
    appointment_id = created.json()["id"]

    response = await client.patch(
        f"/v1/appointments/{appointment_id}",
        json={"status": "cancelled"},
        headers=_auth(access_token_b),
    )

    assert response.status_code == 404


async def test_update_appointment_reschedule_outside_business_hours_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    contact_id = await _make_contact(db_session, org_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )
    created = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token),
    )
    appointment_id = created.json()["id"]

    response = await client.patch(
        f"/v1/appointments/{appointment_id}",
        json={"scheduled_at": f"{_TUESDAY}T20:00:00Z"},
        headers=_auth(access_token),
    )

    assert response.status_code == 422


async def test_update_appointment_reschedule_conflicting_returns_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    contact_id = await _make_contact(db_session, org_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )
    await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token),
    )
    second = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T14:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token),
    )
    second_id = second.json()["id"]

    response = await client.patch(
        f"/v1/appointments/{second_id}",
        json={"scheduled_at": f"{_TUESDAY}T10:00:00Z"},
        headers=_auth(access_token),
    )

    assert response.status_code == 409


async def test_update_appointment_invalid_status_returns_422(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register(client)
    contact_id = await _make_contact(db_session, org_id)
    await client.post(
        "/v1/business-hours",
        json={"day_of_week": 1, "opens_at": "09:00:00", "closes_at": "17:00:00"},
        headers=_auth(access_token),
    )
    created = await client.post(
        "/v1/appointments",
        json={
            "contact_id": contact_id,
            "scheduled_at": f"{_TUESDAY}T10:00:00Z",
            "duration_minutes": 30,
        },
        headers=_auth(access_token),
    )
    appointment_id = created.json()["id"]

    response = await client.patch(
        f"/v1/appointments/{appointment_id}",
        json={"status": "not-a-real-status"},
        headers=_auth(access_token),
    )

    assert response.status_code == 422
