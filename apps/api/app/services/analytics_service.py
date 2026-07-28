"""Aggregate analytics for the dashboard's overview page (see docs/TDD.md
section 3.2 and issue #19).

Every metric here is produced by a single aggregate SQL query (``GROUP BY``,
``COUNT``, a join), never by loading conversation/message/appointment rows
into Python and counting or averaging them there. That's what lets
``get_overview`` run the same small, constant number of queries whether an
organization has ten conversations or ten thousand over the requested range.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.appointment import Appointment
from app.models.conversation import Conversation
from app.models.message import Message


@dataclass(frozen=True)
class AnalyticsOverview:
    """Aggregate counts for one organization over ``[start_date, end_date]``
    (both calendar days, inclusive, interpreted as UTC).
    """

    start_date: date
    end_date: date
    call_volume: int
    sms_volume: int
    appointments_booked: int
    average_conversation_length: float


def _date_range_bounds(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """``[start_date, end_date]`` (calendar days, inclusive) as a half-open
    UTC datetime range: ``[start_date 00:00 UTC, end_date + 1 day 00:00 UTC)``.
    Same convention as ``app.services.appointment_service.list_appointments``'s
    ``on_date`` filter.
    """
    range_start = datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
    range_end = datetime.combine(end_date, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    return range_start, range_end


async def get_overview(
    session: AsyncSession,
    organization_id: uuid.UUID,
    start_date: date,
    end_date: date,
) -> AnalyticsOverview:
    """Call volume, SMS volume, appointments booked, and average conversation
    length for ``organization_id`` over ``[start_date, end_date]``.

    "Call volume" and "SMS volume" are the count of ``Conversation`` rows
    created in the range on the ``voice``/``sms`` channel respectively (one
    conversation per call or text thread, not one per message/turn).

    "Appointments booked" is the count of ``Appointment`` rows *created*
    (not scheduled) in the range: booking activity that happened during the
    window, independent of when the booked slot itself falls. That matches
    how a dashboard user reads "booked in the last 30 days".

    "Average conversation length" is message count per conversation (turns
    per call/thread) rather than wall-clock duration. ``Message`` has no
    explicit end-of-turn or end-of-call timestamp to derive a duration from
    (a voice call's `Message` rows are persisted as the agent produces each
    turn, not stamped with the call's start/end time), so message count is
    used instead: a direct, well-defined proxy for how much back-and-forth a
    conversation involved, derivable cleanly from the existing schema.

    It's computed as ``(total messages belonging to in-range conversations)
    / (in-range conversation count)`` rather than an ``AVG(...)`` over a
    per-conversation subquery. The two are mathematically identical: the
    average of a per-conversation message count, taken over every
    in-range conversation (including ones with zero messages), is exactly
    that conversation's share of the grand total. This form needs only a
    ``COUNT`` on each side and no subquery.

    Every metric above comes from one aggregate SQL query: one grouped
    ``COUNT`` for call/SMS volume (and, via their sum, total conversation
    count), one ``COUNT`` for appointments booked, and one joined ``COUNT``
    for the message total. That's 3 queries total, regardless of how many
    conversations, messages, or appointments exist in the range.
    """
    range_start, range_end = _date_range_bounds(start_date, end_date)

    # Query 1: conversation counts grouped by channel. Covers call_volume
    # and sms_volume in one pass; their sum is also the in-range
    # conversation count, used below for the average.
    channel_counts_result = await session.execute(
        select(Conversation.channel, func.count(Conversation.id))
        .where(
            Conversation.organization_id == organization_id,
            Conversation.created_at >= range_start,
            Conversation.created_at < range_end,
        )
        .group_by(Conversation.channel)
    )
    channel_counts: dict[str, int] = {
        channel: count for channel, count in channel_counts_result.all()
    }
    call_volume = channel_counts.get("voice", 0)
    sms_volume = channel_counts.get("sms", 0)
    conversation_count = call_volume + sms_volume

    # Query 2: appointments booked (created, not scheduled) in the range.
    appointments_booked_result = await session.execute(
        select(func.count(Appointment.id)).where(
            Appointment.organization_id == organization_id,
            Appointment.created_at >= range_start,
            Appointment.created_at < range_end,
        )
    )
    appointments_booked = appointments_booked_result.scalar_one()

    # Query 3: total messages belonging to those same in-range conversations.
    message_count_result = await session.execute(
        select(func.count(Message.id))
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Conversation.organization_id == organization_id,
            Conversation.created_at >= range_start,
            Conversation.created_at < range_end,
        )
    )
    message_count = message_count_result.scalar_one()

    average_conversation_length = (
        message_count / conversation_count if conversation_count > 0 else 0.0
    )

    return AnalyticsOverview(
        start_date=start_date,
        end_date=end_date,
        call_volume=call_volume,
        sms_volume=sms_volume,
        appointments_booked=appointments_booked,
        average_conversation_length=average_conversation_length,
    )
