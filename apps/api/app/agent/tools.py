"""The agent's four tools: Gemini function declarations plus the async
functions that actually execute them against app/services/ (see
docs/TDD.md section 3.3).

Every tool function takes `organization_id` and `contact_id` as explicit
parameters supplied by the caller (the graph's `tools` node, from graph
state, see app/agent/graph.py) rather than reading them out of Gemini's
tool-call arguments. Gemini only ever supplies the "business" arguments
(`query`, `order_number`, `date`, ...); it is never trusted to supply which
organization or contact it's acting on, since nothing stops a model from
hallucinating or being prompt-injected into supplying the wrong one, and a
wrong one would mean one tenant's data leaking into another tenant's
conversation.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import date as date_type
from datetime import datetime
from typing import Any

from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import appointment_service, knowledge_service, order_service
from app.services.appointment_service import AppointmentServiceError

# --- Gemini function declarations ------------------------------------------

_KNOWLEDGE_SEARCH_DECLARATION = types.FunctionDeclaration(
    name="knowledge_search",
    description=(
        "Search the business's knowledge base (FAQs, policies, service "
        "descriptions) for information relevant to the customer's question."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="The customer's question or search phrase.",
            ),
        },
        required=["query"],
    ),
)

_LOOKUP_ORDER_DECLARATION = types.FunctionDeclaration(
    name="lookup_order",
    description=(
        "Look up a customer's order, by order number if the customer gave "
        "one, or by their phone number otherwise (their most recent order "
        "on that number is returned)."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "order_number": types.Schema(
                type=types.Type.STRING,
                description="The order number, if the customer provided one.",
            ),
            "phone_number": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The customer's phone number in E.164 format, used to "
                    "find their order when no order number was given."
                ),
            ),
        },
    ),
)

_CHECK_AVAILABILITY_DECLARATION = types.FunctionDeclaration(
    name="check_availability",
    description="Check open appointment slots for a given calendar date.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "date": types.Schema(
                type=types.Type.STRING,
                description="The date to check, as an ISO 8601 date, e.g. 2026-08-03.",
            ),
        },
        required=["date"],
    ),
)

_BOOK_APPOINTMENT_DECLARATION = types.FunctionDeclaration(
    name="book_appointment",
    description=(
        "Book an appointment at a specific date and time, after confirming "
        "the slot is open with check_availability."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "scheduled_at": types.Schema(
                type=types.Type.STRING,
                description=(
                    "The appointment start time, as an ISO 8601 datetime, "
                    "e.g. 2026-08-03T14:00:00."
                ),
            ),
            "duration_minutes": types.Schema(
                type=types.Type.INTEGER,
                description="How long the appointment should last, in minutes.",
            ),
            "notes": types.Schema(
                type=types.Type.STRING,
                description="Any notes about the appointment the customer mentioned.",
            ),
        },
        required=["scheduled_at", "duration_minutes"],
    ),
)

TOOL_DECLARATIONS = [
    _KNOWLEDGE_SEARCH_DECLARATION,
    _LOOKUP_ORDER_DECLARATION,
    _CHECK_AVAILABILITY_DECLARATION,
    _BOOK_APPOINTMENT_DECLARATION,
]

# What app/agent/graph.py's `agent` node hands to `gemini_client.generate_content`
# as the `tools` argument: one `Tool` bundling all four function declarations.
GEMINI_TOOLS = [types.Tool(function_declarations=TOOL_DECLARATIONS)]


# --- Tool implementations ---------------------------------------------------


async def knowledge_search(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    query: str,
) -> dict[str, Any]:
    """Wraps `knowledge_service.search`. `contact_id` is accepted (like
    every tool here) for a uniform dispatch signature, even though a
    knowledge-base search doesn't scope by contact.
    """
    documents = await knowledge_service.search(session, organization_id, query)
    return {
        "results": [
            {"title": document.title, "content": document.content} for document in documents
        ]
    }


async def lookup_order(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    order_number: str | None = None,
    phone_number: str | None = None,
) -> dict[str, Any]:
    """Wraps `order_service.lookup`."""
    result = await order_service.lookup(
        session, organization_id, order_number=order_number, phone_number=phone_number
    )
    if not result.found or result.order is None:
        return {"found": False}

    order = result.order
    return {
        "found": True,
        "order_number": order.order_number,
        "status": order.status,
        "item_description": order.item_description,
    }


async def check_availability(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    date: str,
) -> dict[str, Any]:
    """Wraps `appointment_service.check_availability`. `date` is an ISO 8601
    date string (Gemini's function-calling arguments are always JSON
    scalars, never native `date` objects), parsed before being handed to
    the service.
    """
    parsed_date = date_type.fromisoformat(date)
    slots = await appointment_service.check_availability(session, organization_id, parsed_date)
    return {
        "slots": [
            {"start": slot.start.isoformat(), "end": slot.end.isoformat()} for slot in slots
        ]
    }


async def book_appointment(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    scheduled_at: str,
    duration_minutes: int,
    notes: str | None = None,
) -> dict[str, Any]:
    """Wraps `appointment_service.book`. `contact_id` (from graph state, see
    the module docstring) is who the appointment gets booked for.

    `OutsideBusinessHoursError` and `AppointmentConflictError` (both
    `AppointmentServiceError`) are caught and turned into a `booked: False`
    result with an `error` message rather than propagating, so the model
    gets a chance to tell the customer why the booking didn't go through
    and offer an alternative, instead of the whole turn failing.
    """
    parsed_scheduled_at = datetime.fromisoformat(scheduled_at)
    try:
        appointment = await appointment_service.book(
            session,
            organization_id,
            contact_id,
            parsed_scheduled_at,
            duration_minutes,
            notes=notes,
        )
    except AppointmentServiceError as error:
        return {"booked": False, "error": str(error)}

    return {
        "booked": True,
        "appointment_id": str(appointment.id),
        "scheduled_at": appointment.scheduled_at.isoformat(),
        "duration_minutes": appointment.duration_minutes,
    }


TOOL_FUNCTIONS: dict[str, Callable[..., Awaitable[dict[str, Any]]]] = {
    "knowledge_search": knowledge_search,
    "lookup_order": lookup_order,
    "check_availability": check_availability,
    "book_appointment": book_appointment,
}


async def execute_tool(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Look up `name` in `TOOL_FUNCTIONS` and run it with `organization_id`/
    `contact_id` injected and `arguments` (Gemini's tool-call arguments)
    passed through as keyword arguments.

    Returns `{"error": ...}` for a tool name the model hallucinated (not in
    `TOOL_FUNCTIONS`) rather than raising, so a single bad tool call doesn't
    crash the whole graph run; the error is a well-formed function response
    Gemini can read and recover from.
    """
    function = TOOL_FUNCTIONS.get(name)
    if function is None:
        return {"error": f"Unknown tool: {name}"}
    return await function(session, organization_id, contact_id, **arguments)
