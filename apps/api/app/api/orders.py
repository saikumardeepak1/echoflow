"""Dashboard order management (``POST``/``GET /v1/orders``). Staff seed
order records here as a stand-in for a real order-system connector (see
docs/PRD.md non-goals); the agent's ``lookup_order`` tool reads them back
via ``order_service.lookup`` rather than this HTTP surface.
"""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_session
from app.models import User
from app.schemas.order import OrderCreateRequest, OrderResponse
from app.services import order_service

router = APIRouter(prefix="/v1/orders", tags=["orders"])


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    body: OrderCreateRequest,
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> OrderResponse:
    """Create an order for the caller's organization. The contact is
    resolved (or created) by ``contact_phone_number``, the same
    find-or-create-by-phone-number behavior inbound webhooks use.

    Requires a JWT session (``Authorization: Bearer <jwt>``).
    """
    order = await order_service.create(
        session,
        organization_id=current_user.organization_id,
        order_number=body.order_number,
        item_description=body.item_description,
        contact_phone_number=body.contact_phone_number,
        status=body.status,
    )
    await session.commit()
    return OrderResponse.from_order(order)


@router.get("", response_model=list[OrderResponse])
async def list_orders(
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> list[OrderResponse]:
    """All orders belonging to the caller's organization, most recently
    created first.

    Requires a JWT session (``Authorization: Bearer <jwt>``).
    """
    orders = await order_service.list_for_organization(
        session, organization_id=current_user.organization_id
    )
    return [OrderResponse.from_order(order) for order in orders]
