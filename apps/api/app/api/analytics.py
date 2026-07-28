"""Dashboard analytics overview (``GET /v1/analytics/overview``), see
docs/TDD.md section 3.2 and issue #19. Session-authenticated and org-scoped,
same pattern as app/api/conversations.py and app/api/appointments.py: a
dashboard user only ever sees aggregate metrics for their own organization.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.security import require_session
from app.models import User
from app.schemas.analytics import AnalyticsOverviewResponse
from app.services import analytics_service

router = APIRouter(prefix="/v1/analytics", tags=["analytics"])


@router.get("/overview", response_model=AnalyticsOverviewResponse)
async def get_overview(
    start_date: date = Query(..., description="Start of the reporting range (UTC, inclusive)"),
    end_date: date = Query(..., description="End of the reporting range (UTC, inclusive)"),
    current_user: User = Depends(require_session),
    session: AsyncSession = Depends(get_session),
) -> AnalyticsOverviewResponse:
    """Call volume, SMS volume, appointments booked, and average conversation
    length for the caller's organization over ``[start_date, end_date]``.

    Requires a JWT session (``Authorization: Bearer <jwt>``).
    """
    if end_date < start_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="end_date must not be before start_date",
        )

    overview = await analytics_service.get_overview(
        session, current_user.organization_id, start_date, end_date
    )
    return AnalyticsOverviewResponse.from_overview(overview)
