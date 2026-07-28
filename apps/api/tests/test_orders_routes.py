"""Integration tests for POST/GET /v1/orders, run against a real (test)
Postgres via the ``client`` and ``db_session`` fixtures (see conftest.py).
"""

import uuid
from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Order


async def _register_and_get_access_token(
    client: AsyncClient, organization_name: str = "Riverside Dental"
) -> tuple[str, str]:
    email = f"user-{uuid.uuid4().hex[:12]}@example.com"
    response = await client.post(
        "/v1/auth/register",
        json={
            "organization_name": organization_name,
            "email": email,
            "password": "correct horse battery staple",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body["access_token"], body["user"]["organization_id"]


def _order_payload(**overrides: str) -> dict[str, str]:
    payload = {
        "order_number": f"ORD-{uuid.uuid4().hex[:8]}",
        "item_description": "2x ceramic espresso cups",
        "contact_phone_number": "+15551234567",
        "status": "pending",
    }
    payload.update(overrides)
    return payload


# --- POST /v1/orders ---------------------------------------------------


async def test_create_order_returns_the_created_order(client: AsyncClient) -> None:
    access_token, org_id = await _register_and_get_access_token(client)
    payload = _order_payload()

    response = await client.post(
        "/v1/orders", json=payload, headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["order_number"] == payload["order_number"]
    assert body["item_description"] == payload["item_description"]
    assert body["contact_phone_number"] == payload["contact_phone_number"]
    assert body["status"] == "pending"
    assert body["organization_id"] == org_id
    assert "id" in body
    assert "contact_id" in body


async def test_create_order_defaults_status_to_pending_when_omitted(
    client: AsyncClient,
) -> None:
    access_token, _ = await _register_and_get_access_token(client)
    payload = _order_payload()
    del payload["status"]

    response = await client.post(
        "/v1/orders", json=payload, headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 201, response.text
    assert response.json()["status"] == "pending"


async def test_create_order_finds_or_creates_contact_by_phone_number(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token, org_id = await _register_and_get_access_token(client)
    phone_number = "+15559990000"

    first = await client.post(
        "/v1/orders",
        json=_order_payload(contact_phone_number=phone_number),
        headers={"Authorization": f"Bearer {access_token}"},
    )
    second = await client.post(
        "/v1/orders",
        json=_order_payload(contact_phone_number=phone_number),
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["contact_id"] == second.json()["contact_id"]

    result = await db_session.execute(
        select(Contact).where(
            Contact.organization_id == uuid.UUID(org_id),
            Contact.e164_number == phone_number,
        )
    )
    contacts = result.scalars().all()
    assert len(contacts) == 1


async def test_create_order_requires_session_auth(client: AsyncClient) -> None:
    response = await client.post("/v1/orders", json=_order_payload())
    assert response.status_code == 401


async def test_create_order_rejects_missing_order_number(client: AsyncClient) -> None:
    access_token, _ = await _register_and_get_access_token(client)
    payload = _order_payload()
    del payload["order_number"]

    response = await client.post(
        "/v1/orders", json=payload, headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 422


# --- GET /v1/orders ------------------------------------------------------


async def test_list_orders_returns_only_the_callers_organization_orders(
    client: AsyncClient,
) -> None:
    access_token_a, _ = await _register_and_get_access_token(client, "Org A")
    access_token_b, _ = await _register_and_get_access_token(client, "Org B")

    await client.post(
        "/v1/orders",
        json=_order_payload(order_number="ORD-A-ONLY"),
        headers={"Authorization": f"Bearer {access_token_a}"},
    )
    await client.post(
        "/v1/orders",
        json=_order_payload(order_number="ORD-B-ONLY"),
        headers={"Authorization": f"Bearer {access_token_b}"},
    )

    response_a = await client.get(
        "/v1/orders", headers={"Authorization": f"Bearer {access_token_a}"}
    )
    assert response_a.status_code == 200
    order_numbers_a = [order["order_number"] for order in response_a.json()]
    assert "ORD-A-ONLY" in order_numbers_a
    assert "ORD-B-ONLY" not in order_numbers_a

    response_b = await client.get(
        "/v1/orders", headers={"Authorization": f"Bearer {access_token_b}"}
    )
    assert response_b.status_code == 200
    order_numbers_b = [order["order_number"] for order in response_b.json()]
    assert "ORD-B-ONLY" in order_numbers_b
    assert "ORD-A-ONLY" not in order_numbers_b


async def test_list_orders_returns_empty_list_for_organization_with_no_orders(
    client: AsyncClient,
) -> None:
    access_token, _ = await _register_and_get_access_token(client)

    response = await client.get(
        "/v1/orders", headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 200
    assert response.json() == []


async def test_list_orders_requires_session_auth(client: AsyncClient) -> None:
    response = await client.get("/v1/orders")
    assert response.status_code == 401


async def test_list_orders_orders_most_recently_created_first(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    # Both orders are created in the same test transaction (via the shared
    # db_session/client fixtures), and Postgres's `now()` (the server
    # default backing `created_at`) returns the enclosing transaction's
    # start time rather than the statement time, so they would otherwise
    # get an identical `created_at` here even though ordering is
    # well-defined across separate, real requests in production. Setting
    # `created_at` explicitly after creation keeps this test about the
    # endpoint's ordering rather than about Postgres transaction semantics.
    access_token, _ = await _register_and_get_access_token(client)

    first = await client.post(
        "/v1/orders",
        json=_order_payload(order_number="ORD-FIRST"),
        headers={"Authorization": f"Bearer {access_token}"},
    )
    second = await client.post(
        "/v1/orders",
        json=_order_payload(order_number="ORD-SECOND"),
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert first.status_code == 201
    assert second.status_code == 201

    result = await db_session.execute(
        select(Order).where(Order.order_number.in_(["ORD-FIRST", "ORD-SECOND"]))
    )
    orders_by_number = {order.order_number: order for order in result.scalars().all()}
    orders_by_number["ORD-FIRST"].created_at = datetime(2026, 1, 1, tzinfo=UTC)
    orders_by_number["ORD-SECOND"].created_at = datetime(2026, 1, 2, tzinfo=UTC)
    await db_session.flush()

    response = await client.get(
        "/v1/orders", headers={"Authorization": f"Bearer {access_token}"}
    )

    order_numbers = [order["order_number"] for order in response.json()]
    assert order_numbers.index("ORD-SECOND") < order_numbers.index("ORD-FIRST")
