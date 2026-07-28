"""Tests for app.agent.graph: the LangGraph tool-calling loop.

Gemini is never called for real here; `app.agent.gemini_client.generate_content`
is monkeypatched to return canned `types.GenerateContentResponse` objects
(see `_text_response`/`_function_call_response` below), so these tests need
no GEMINI_API_KEY and no network access. `app.agent.tools.execute_tool` is
also monkeypatched, so these tests assert the graph's routing (which tool
gets called, with what arguments, how many rounds it takes) without
touching a database; app.agent.tools itself, and therefore the real
services it wraps, is covered separately (with a real Postgres) in
tests/test_agent_tools.py.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import gemini_client, graph
from app.agent import tools as agent_tools


class _StubGenerateContent:
    """A callable that returns successive canned `GenerateContentResponse`
    values on each call, matching `gemini_client.generate_content`'s
    synchronous signature (the real Gemini client call is synchronous; the
    graph's `agent` node awaits nothing around it).
    """

    def __init__(self, *responses: types.GenerateContentResponse) -> None:
        self._responses = iter(responses)

    def __call__(self, **kwargs: Any) -> types.GenerateContentResponse:
        return next(self._responses)


def _text_response(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))
        ]
    )


def _function_call_response(*calls: tuple[str, dict[str, Any]]) -> types.GenerateContentResponse:
    parts = [
        types.Part(function_call=types.FunctionCall(name=name, args=args)) for name, args in calls
    ]
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=parts))]
    )


@pytest.fixture
def organization_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def contact_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def conversation_id() -> uuid.UUID:
    return uuid.uuid4()


def _history(user_text: str = "Hi, I have a question.") -> list[graph.HistoryMessage]:
    return [{"role": "user", "content": user_text}]


# --- no tool needed -----------------------------------------------------------


async def test_plain_response_never_calls_a_tool(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    conversation_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_response = _text_response("Hi! How can I help?")
    monkeypatch.setattr(gemini_client, "generate_content", _StubGenerateContent(stub_response))
    execute_tool_mock = AsyncMock()
    monkeypatch.setattr(agent_tools, "execute_tool", execute_tool_mock)

    result = await graph.run_agent_turn(
        db_session, organization_id, contact_id, conversation_id, _history()
    )

    assert result == "Hi! How can I help?"
    execute_tool_mock.assert_not_called()


# --- one tool call per tool ------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "tool_args"),
    [
        ("knowledge_search", {"query": "what are your hours"}),
        ("lookup_order", {"order_number": "ORD-1"}),
        ("check_availability", {"date": "2026-08-03"}),
        (
            "book_appointment",
            {"scheduled_at": "2026-08-03T14:00:00", "duration_minutes": 30, "notes": "first visit"},
        ),
    ],
)
async def test_graph_calls_the_right_tool_with_the_right_arguments(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    conversation_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
    tool_args: dict[str, Any],
) -> None:
    responses = [
        _function_call_response((tool_name, tool_args)),
        _text_response("All set."),
    ]
    monkeypatch.setattr(gemini_client, "generate_content", _StubGenerateContent(*responses))
    execute_tool_mock = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(agent_tools, "execute_tool", execute_tool_mock)

    result = await graph.run_agent_turn(
        db_session, organization_id, contact_id, conversation_id, _history()
    )

    assert result == "All set."
    execute_tool_mock.assert_awaited_once_with(
        db_session, organization_id, contact_id, tool_name, tool_args
    )


# --- multi-tool-call turn -----------------------------------------------------


async def test_multi_tool_call_turn_handles_lookup_order_then_check_availability(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    conversation_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        _function_call_response(("lookup_order", {"order_number": "ORD-1"})),
        _function_call_response(("check_availability", {"date": "2026-08-03"})),
        _text_response("Your order ORD-1 is on its way, and I found an open slot."),
    ]
    monkeypatch.setattr(gemini_client, "generate_content", _StubGenerateContent(*responses))
    execute_tool_mock = AsyncMock(
        side_effect=[
            {"found": True, "order_number": "ORD-1", "status": "shipped"},
            {"slots": [{"start": "2026-08-03T09:00:00+00:00", "end": "2026-08-03T17:00:00+00:00"}]},
        ]
    )
    monkeypatch.setattr(agent_tools, "execute_tool", execute_tool_mock)

    result = await graph.run_agent_turn(
        db_session, organization_id, contact_id, conversation_id, _history()
    )

    assert result == "Your order ORD-1 is on its way, and I found an open slot."
    assert execute_tool_mock.await_count == 2
    execute_tool_mock.assert_any_await(
        db_session, organization_id, contact_id, "lookup_order", {"order_number": "ORD-1"}
    )
    execute_tool_mock.assert_any_await(
        db_session, organization_id, contact_id, "check_availability", {"date": "2026-08-03"}
    )


async def test_multiple_function_calls_in_a_single_response_both_execute(
    db_session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    conversation_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini can also request more than one tool call in a single response
    (parallel function calling); the `tools` node should execute all of
    them in that one pass rather than only the first.
    """
    responses = [
        _function_call_response(
            ("lookup_order", {"order_number": "ORD-1"}),
            ("check_availability", {"date": "2026-08-03"}),
        ),
        _text_response("Done."),
    ]
    monkeypatch.setattr(gemini_client, "generate_content", _StubGenerateContent(*responses))
    execute_tool_mock = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(agent_tools, "execute_tool", execute_tool_mock)

    result = await graph.run_agent_turn(
        db_session, organization_id, contact_id, conversation_id, _history()
    )

    assert result == "Done."
    assert execute_tool_mock.await_count == 2
