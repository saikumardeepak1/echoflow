"""Integration tests for POST/GET/DELETE /v1/phone-numbers, run against a
real (test) Postgres via the ``client`` fixture (see conftest.py).
"""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PhoneNumber


async def _register_and_get_access_token(
    client: AsyncClient, name: str = "Riverside Dental"
) -> str:
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
    return response.json()["access_token"]


# --- POST /v1/phone-numbers ---------------------------------------------------


async def test_create_phone_number_returns_201(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"e164_number": "+15551234567"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["e164_number"] == "+15551234567"
    assert "id" in body
    assert "organization_id" in body
    assert "created_at" in body


async def test_created_phone_number_is_scoped_to_the_callers_organization(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"e164_number": "+15551234567"},
    )
    phone_number_id = uuid.UUID(response.json()["id"])

    result = await db_session.execute(
        select(PhoneNumber).where(PhoneNumber.id == phone_number_id)
    )
    stored = result.scalar_one()
    assert stored.organization_id is not None


async def test_create_phone_number_requires_session_auth(client: AsyncClient) -> None:
    response = await client.post("/v1/phone-numbers", json={"e164_number": "+15551234567"})
    assert response.status_code == 401


async def test_create_phone_number_rejects_missing_field(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token}"},
        json={},
    )
    assert response.status_code == 422


async def test_create_phone_number_rejects_invalid_e164_format(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"e164_number": "555-123-4567"},
    )
    assert response.status_code == 422


async def test_create_phone_number_rejects_duplicate_within_same_organization(
    client: AsyncClient,
) -> None:
    access_token = await _register_and_get_access_token(client)
    await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"e164_number": "+15551234567"},
    )

    response = await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"e164_number": "+15551234567"},
    )
    assert response.status_code == 409


async def test_create_phone_number_rejects_duplicate_across_organizations(
    client: AsyncClient,
) -> None:
    access_token_a = await _register_and_get_access_token(client, "Riverside Dental")
    access_token_b = await _register_and_get_access_token(client, "Lakeside Clinic")

    first_response = await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token_a}"},
        json={"e164_number": "+15551234567"},
    )
    assert first_response.status_code == 201

    second_response = await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token_b}"},
        json={"e164_number": "+15551234567"},
    )
    assert second_response.status_code == 409


# --- GET /v1/phone-numbers -----------------------------------------------------


async def test_list_phone_numbers_returns_only_the_callers_organization(
    client: AsyncClient,
) -> None:
    access_token_a = await _register_and_get_access_token(client, "Riverside Dental")
    access_token_b = await _register_and_get_access_token(client, "Lakeside Clinic")

    await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token_a}"},
        json={"e164_number": "+15551234567"},
    )
    await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token_b}"},
        json={"e164_number": "+15557654321"},
    )

    response = await client.get(
        "/v1/phone-numbers", headers={"Authorization": f"Bearer {access_token_a}"}
    )

    assert response.status_code == 200
    numbers = [entry["e164_number"] for entry in response.json()]
    assert numbers == ["+15551234567"]


async def test_list_phone_numbers_requires_session_auth(client: AsyncClient) -> None:
    response = await client.get("/v1/phone-numbers")
    assert response.status_code == 401


async def test_list_phone_numbers_empty_when_none_exist(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.get(
        "/v1/phone-numbers", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    assert response.json() == []


# --- DELETE /v1/phone-numbers/{id} ---------------------------------------------


async def test_delete_phone_number_returns_204(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)
    create_response = await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"e164_number": "+15551234567"},
    )
    phone_number_id = create_response.json()["id"]

    response = await client.delete(
        f"/v1/phone-numbers/{phone_number_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 204

    list_response = await client.get(
        "/v1/phone-numbers", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert list_response.json() == []


async def test_delete_phone_number_requires_session_auth(client: AsyncClient) -> None:
    response = await client.delete(f"/v1/phone-numbers/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_delete_unknown_phone_number_returns_404(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.delete(
        f"/v1/phone-numbers/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 404


async def test_cannot_delete_another_organizations_phone_number(client: AsyncClient) -> None:
    access_token_a = await _register_and_get_access_token(client, "Riverside Dental")
    access_token_b = await _register_and_get_access_token(client, "Lakeside Clinic")

    create_response = await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token_a}"},
        json={"e164_number": "+15551234567"},
    )
    phone_number_id = create_response.json()["id"]

    # Org B attempting to delete org A's phone number must not succeed, and
    # must 404 rather than 403 so org B cannot distinguish "not found" from
    # "not yours" and probe for other orgs' phone number ids.
    response = await client.delete(
        f"/v1/phone-numbers/{phone_number_id}",
        headers={"Authorization": f"Bearer {access_token_b}"},
    )
    assert response.status_code == 404

    # Org A's phone number is untouched.
    list_response = await client.get(
        "/v1/phone-numbers", headers={"Authorization": f"Bearer {access_token_a}"}
    )
    assert len(list_response.json()) == 1


async def test_cannot_view_another_organizations_phone_number_via_list(
    client: AsyncClient,
) -> None:
    access_token_a = await _register_and_get_access_token(client, "Riverside Dental")
    access_token_b = await _register_and_get_access_token(client, "Lakeside Clinic")

    await client.post(
        "/v1/phone-numbers",
        headers={"Authorization": f"Bearer {access_token_a}"},
        json={"e164_number": "+15551234567"},
    )

    response = await client.get(
        "/v1/phone-numbers", headers={"Authorization": f"Bearer {access_token_b}"}
    )
    assert response.status_code == 200
    assert response.json() == []
