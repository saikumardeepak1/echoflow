"""Request/response schemas for /v1/analytics routes."""

from datetime import date

from pydantic import BaseModel, ConfigDict

from app.services.analytics_service import AnalyticsOverview


class AnalyticsOverviewResponse(BaseModel):
    """Aggregate call/SMS volume, appointments booked, and average
    conversation length for one organization over a date range. Used by
    ``GET /v1/analytics/overview``.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-28",
                    "call_volume": 42,
                    "sms_volume": 108,
                    "appointments_booked": 17,
                    "average_conversation_length": 3.5,
                }
            ]
        }
    )

    start_date: date
    end_date: date
    call_volume: int
    sms_volume: int
    appointments_booked: int
    average_conversation_length: float

    @classmethod
    def from_overview(cls, overview: AnalyticsOverview) -> "AnalyticsOverviewResponse":
        return cls(
            start_date=overview.start_date,
            end_date=overview.end_date,
            call_volume=overview.call_volume,
            sms_volume=overview.sms_volume,
            appointments_booked=overview.appointments_booked,
            average_conversation_length=overview.average_conversation_length,
        )
