"""Integration tests for POST/GET/DELETE /v1/knowledge-documents, run
against a real (test) Postgres via the ``client`` fixture (see conftest.py).
"""

import uuid

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import KnowledgeDocument


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


# --- POST /v1/knowledge-documents --------------------------------------------


async def test_create_knowledge_document_returns_201(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.post(
        "/v1/knowledge-documents",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"title": "Parking", "content": "Free parking is available in the lot."},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Parking"
    assert body["content"] == "Free parking is available in the lot."
    assert "id" in body
    assert "created_at" in body


async def test_created_knowledge_document_is_scoped_to_the_callers_organization(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.post(
        "/v1/knowledge-documents",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"title": "Parking", "content": "Free parking is available in the lot."},
    )
    document_id = uuid.UUID(response.json()["id"])

    result = await db_session.execute(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
    )
    stored = result.scalar_one()
    assert stored.organization_id is not None


async def test_create_knowledge_document_requires_session_auth(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/knowledge-documents",
        json={"title": "Parking", "content": "Free parking is available in the lot."},
    )
    assert response.status_code == 401


async def test_create_knowledge_document_rejects_missing_fields(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.post(
        "/v1/knowledge-documents",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"title": "Parking"},
    )
    assert response.status_code == 422


# --- GET /v1/knowledge-documents ----------------------------------------------


async def test_list_knowledge_documents_returns_only_the_callers_organization(
    client: AsyncClient,
) -> None:
    access_token_a = await _register_and_get_access_token(client, "Riverside Dental")
    access_token_b = await _register_and_get_access_token(client, "Lakeside Clinic")

    await client.post(
        "/v1/knowledge-documents",
        headers={"Authorization": f"Bearer {access_token_a}"},
        json={"title": "Org A doc", "content": "Content belonging to org A."},
    )
    await client.post(
        "/v1/knowledge-documents",
        headers={"Authorization": f"Bearer {access_token_b}"},
        json={"title": "Org B doc", "content": "Content belonging to org B."},
    )

    response = await client.get(
        "/v1/knowledge-documents", headers={"Authorization": f"Bearer {access_token_a}"}
    )

    assert response.status_code == 200
    titles = [doc["title"] for doc in response.json()]
    assert titles == ["Org A doc"]


async def test_list_knowledge_documents_requires_session_auth(client: AsyncClient) -> None:
    response = await client.get("/v1/knowledge-documents")
    assert response.status_code == 401


async def test_list_knowledge_documents_empty_when_none_exist(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.get(
        "/v1/knowledge-documents", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert response.status_code == 200
    assert response.json() == []


# --- DELETE /v1/knowledge-documents/{id} --------------------------------------


async def test_delete_knowledge_document_returns_204(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)
    create_response = await client.post(
        "/v1/knowledge-documents",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"title": "Parking", "content": "Free parking is available in the lot."},
    )
    document_id = create_response.json()["id"]

    response = await client.delete(
        f"/v1/knowledge-documents/{document_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 204

    list_response = await client.get(
        "/v1/knowledge-documents", headers={"Authorization": f"Bearer {access_token}"}
    )
    assert list_response.json() == []


async def test_delete_knowledge_document_requires_session_auth(client: AsyncClient) -> None:
    response = await client.delete(f"/v1/knowledge-documents/{uuid.uuid4()}")
    assert response.status_code == 401


async def test_delete_unknown_knowledge_document_returns_404(client: AsyncClient) -> None:
    access_token = await _register_and_get_access_token(client)

    response = await client.delete(
        f"/v1/knowledge-documents/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 404


async def test_cannot_delete_another_organizations_knowledge_document(
    client: AsyncClient,
) -> None:
    access_token_a = await _register_and_get_access_token(client, "Riverside Dental")
    access_token_b = await _register_and_get_access_token(client, "Lakeside Clinic")

    create_response = await client.post(
        "/v1/knowledge-documents",
        headers={"Authorization": f"Bearer {access_token_a}"},
        json={"title": "Org A doc", "content": "Content belonging to org A."},
    )
    document_id = create_response.json()["id"]

    # Org B attempting to delete org A's document must not succeed, and must
    # 404 rather than 403 so org B cannot distinguish "not found" from "not
    # yours" and probe for other orgs' document ids.
    response = await client.delete(
        f"/v1/knowledge-documents/{document_id}",
        headers={"Authorization": f"Bearer {access_token_b}"},
    )
    assert response.status_code == 404

    # Org A's document is untouched.
    list_response = await client.get(
        "/v1/knowledge-documents", headers={"Authorization": f"Bearer {access_token_a}"}
    )
    assert len(list_response.json()) == 1


async def test_cannot_view_another_organizations_knowledge_document_via_list(
    client: AsyncClient,
) -> None:
    access_token_a = await _register_and_get_access_token(client, "Riverside Dental")
    access_token_b = await _register_and_get_access_token(client, "Lakeside Clinic")

    await client.post(
        "/v1/knowledge-documents",
        headers={"Authorization": f"Bearer {access_token_a}"},
        json={"title": "Org A secret", "content": "Content belonging to org A only."},
    )

    response = await client.get(
        "/v1/knowledge-documents", headers={"Authorization": f"Bearer {access_token_b}"}
    )
    assert response.status_code == 200
    assert response.json() == []
