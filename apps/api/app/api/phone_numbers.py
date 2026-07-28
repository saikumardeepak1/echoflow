"""Phone number provisioning (POST/GET/DELETE /v1/phone-numbers).
Session-authenticated: dashboard staff register which Twilio number(s)
belong to their organization. This is the table inbound voice/SMS webhooks
read to resolve `Organization` from the Twilio `To` number (see
app/services/conversation_service.py and docs/TDD.md section 3.6).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_session
from app.models import User
from app.schemas.phone_number import PhoneNumberCreateRequest, PhoneNumberResponse
from app.services import phone_number_service

router = APIRouter(prefix="/v1/phone-numbers", tags=["phone-numbers"])


@router.post("", response_model=PhoneNumberResponse, status_code=status.HTTP_201_CREATED)
async def create_phone_number(
    body: PhoneNumberCreateRequest,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> PhoneNumberResponse:
    """Register a Twilio number for the caller's organization.

    Requires a JWT session (``Authorization: Bearer <jwt>``). Returns 422 for
    a number that is not valid E.164, and 409 if the number is already
    registered, to this organization or any other (a number resolves to
    exactly one organization for webhook lookups, so it can never be shared).
    """
    try:
        phone_number = await phone_number_service.create(
            session,
            organization_id=current_user.organization_id,
            e164_number=body.e164_number,
        )
    except phone_number_service.InvalidE164NumberError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except phone_number_service.DuplicatePhoneNumberError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await session.commit()
    await session.refresh(phone_number)
    return PhoneNumberResponse.model_validate(phone_number)


@router.get("", response_model=list[PhoneNumberResponse])
async def list_phone_numbers(
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> list[PhoneNumberResponse]:
    """List every phone number belonging to the caller's organization,
    newest first.

    Requires a JWT session (``Authorization: Bearer <jwt>``).
    """
    phone_numbers = await phone_number_service.list_for_organization(
        session, organization_id=current_user.organization_id
    )
    return [PhoneNumberResponse.model_validate(phone_number) for phone_number in phone_numbers]


@router.delete("/{phone_number_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_phone_number(
    phone_number_id: uuid.UUID,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a phone number belonging to the caller's organization.

    Requires a JWT session (``Authorization: Bearer <jwt>``). 404s (rather
    than 403s) for a phone number that exists but belongs to another
    organization, so a caller cannot distinguish "not found" from "not
    yours" and probe for other orgs' phone number ids.
    """
    deleted = await phone_number_service.delete(
        session, organization_id=current_user.organization_id, phone_number_id=phone_number_id
    )
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Phone number not found"
        )
    await session.commit()
