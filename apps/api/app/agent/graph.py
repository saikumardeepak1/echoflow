"""The agent's LangGraph tool-calling loop (see docs/TDD.md section 3.3).

An `agent` node calls Gemini (via app/agent/gemini_client.py) with the
running conversation plus the five tool declarations (app/agent/tools.py:
the four domain tools plus `end_call`, the signal the model uses to say a
voice conversation is resolved, see `AgentTurnResult`). If Gemini's response
contains one or more function calls, a `tools` node executes each of them
against the relevant service, scoped to the organization/contact the graph
state carries (never to ids Gemini's tool call arguments might supply, see
app/agent/tools.py), appends the results to the conversation, and loops back
to `agent`. Once Gemini responds with plain text and no function calls, the
graph ends. A response with more than one function call in it (Gemini can
request several tools in parallel) is executed as a batch by one pass
through `tools`; a conversation that needs several rounds of tool calls
(e.g. look up an order, then separately check availability) is handled by
the loop running through `agent` and `tools` more than once within a single
`run_agent_turn` call.

Conversation memory is loaded from the `messages` table by the caller
before this module runs at all (windowed to `HISTORY_WINDOW` turns), the
same approach Nexus uses, rather than relying on LangGraph's own
checkpointer: `run_agent_turn` is a plain async function called fresh for
each turn, not a graph that runs long-lived and persists its own state.
"""

import uuid
from dataclasses import dataclass
from typing import Any, TypedDict

from google.genai import types
from langgraph.graph import END, StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import gemini_client
from app.agent import tools as agent_tools

# How many of the most recent persisted turns to load into the graph's
# starting state. Enough for the model to have working context on an
# ordinary support conversation without an unbounded prompt as a
# conversation grows long (see docs/TDD.md section 3.3).
HISTORY_WINDOW = 20


class HistoryMessage(TypedDict):
    """One persisted turn, matching the shape of app.models.message.Message
    rows as loaded by the caller: `role` is `"user"` or `"assistant"`.
    """

    role: str
    content: str


class PendingToolCall(TypedDict):
    """One function call Gemini made in its last response, not yet executed."""

    name: str
    arguments: dict[str, Any]


class AgentState(TypedDict):
    """Graph state for one `run_agent_turn` call.

    `organization_id`/`contact_id` are read by the `tools` node and handed
    to `execute_tool` (never taken from a tool call's own arguments, see
    app/agent/tools.py); `conversation_id` is carried through for context
    even though no node currently branches on it. `contents` is the running
    conversation in Gemini's `Content` format, seeded from the windowed
    message history and appended to by both nodes. `pending_tool_calls` is
    whatever the most recent `agent` turn asked for and `tools` hasn't
    executed yet; `executed_tool_calls` accumulates every call that has
    been run, for callers that want to log or inspect what the agent did
    during the turn. `response_text` is `None` until Gemini returns a
    plain-text response, which is also the graph's end condition.
    `should_end_call` starts `False` and flips to `True` for good once the
    `tools` node executes an `end_call` call (see `agent_tools.end_call`);
    it survives every later `agent`/`tools` round of the same turn since
    neither node's returned partial state overwrites a key it doesn't
    mention, so a turn that calls `end_call` and then keeps using other
    tools before its final reply still reports the call as resolved.
    """

    organization_id: uuid.UUID
    contact_id: uuid.UUID
    conversation_id: uuid.UUID
    contents: list[types.Content]
    pending_tool_calls: list[PendingToolCall]
    executed_tool_calls: list[PendingToolCall]
    response_text: str | None
    should_end_call: bool


@dataclass(frozen=True)
class AgentTurnResult:
    """What `run_agent_turn` hands back to its caller: the reply to speak or
    text back, plus whether the agent considers the conversation resolved.

    `should_end_call` is voice-specific in practice (see app/api/webhooks.py:
    the SMS webhook always keeps texting regardless of this flag, since
    there is no "hang up" equivalent for a text thread), but it is computed
    the same way for both channels since nothing about the graph itself
    is channel-aware; only the caller decides what to do with it.
    """

    reply_text: str
    should_end_call: bool


def _history_to_contents(message_history: list[HistoryMessage]) -> list[types.Content]:
    """Convert windowed `Message` rows into Gemini's `Content` format.

    Gemini's chat roles are `"user"` and `"model"`, not the `"user"`/
    `"assistant"` this app's `Message.role` uses (see
    app/services/conversation_service.persist_message), so `"assistant"`
    maps to `"model"` here and everything else (in practice, only `"user"`)
    passes through unchanged.
    """
    windowed = message_history[-HISTORY_WINDOW:]
    contents = []
    for message in windowed:
        role = "model" if message["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=message["content"])]))
    return contents


def _extract_function_calls(response: types.GenerateContentResponse) -> list[types.FunctionCall]:
    if not response.candidates or response.candidates[0].content is None:
        return []
    parts = response.candidates[0].content.parts or []
    return [part.function_call for part in parts if part.function_call is not None]


def _extract_text(response: types.GenerateContentResponse) -> str:
    if not response.candidates or response.candidates[0].content is None:
        return ""
    parts = response.candidates[0].content.parts or []
    return "".join(part.text for part in parts if part.text)


async def _agent_node(state: AgentState) -> dict[str, Any]:
    """Call Gemini with the running conversation and tool declarations.

    Appends Gemini's response content to `contents` regardless of whether
    it contains function calls or plain text, so the model always sees its
    own prior turn on the next round trip, matching how the `google-genai`
    SDK's own automatic-function-calling loop builds history.
    """
    response = gemini_client.generate_content(
        contents=state["contents"], tools=agent_tools.GEMINI_TOOLS
    )

    model_content = (
        response.candidates[0].content
        if response.candidates and response.candidates[0].content is not None
        else types.Content(role="model", parts=[])
    )
    new_contents = [*state["contents"], model_content]

    function_calls = _extract_function_calls(response)
    if function_calls:
        pending: list[PendingToolCall] = [
            {"name": call.name or "", "arguments": dict(call.args or {})}
            for call in function_calls
        ]
        return {
            "contents": new_contents,
            "pending_tool_calls": pending,
            "response_text": None,
        }

    return {
        "contents": new_contents,
        "pending_tool_calls": [],
        "response_text": _extract_text(response),
    }


def _route_after_agent(state: AgentState) -> str:
    """End the graph once Gemini has returned plain text with no tool
    calls to run; otherwise continue to the `tools` node.
    """
    if state["response_text"] is not None:
        return END
    return "tools"


def _build_graph(session: AsyncSession) -> Any:
    """Compile the `agent` <-> `tools` graph for one turn.

    `session` is closed over by the `tools` node rather than carried in
    graph state: it is per-request infrastructure the tools need to talk to
    the database, not part of the conversational state described in the
    `AgentState` docstring, and (unlike `organization_id`/`contact_id`) the
    tools node never needs to hand it to a tool call by name.
    """
    graph: StateGraph[AgentState, None, AgentState, AgentState] = StateGraph(AgentState)

    async def tools_node(state: AgentState) -> dict[str, Any]:
        response_parts = []
        called_end_call = False
        for call in state["pending_tool_calls"]:
            result = await agent_tools.execute_tool(
                session,
                state["organization_id"],
                state["contact_id"],
                call["name"],
                call["arguments"],
            )
            response_parts.append(
                types.Part.from_function_response(name=call["name"], response=result)
            )
            if call["name"] == agent_tools.END_CALL_TOOL_NAME:
                called_end_call = True

        function_response_content = types.Content(role="user", parts=response_parts)
        return {
            "contents": [*state["contents"], function_response_content],
            "pending_tool_calls": [],
            "executed_tool_calls": [*state["executed_tool_calls"], *state["pending_tool_calls"]],
            "should_end_call": state["should_end_call"] or called_end_call,
        }

    graph.add_node("agent", _agent_node)
    graph.add_node("tools", tools_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", _route_after_agent, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


async def run_agent_turn(
    session: AsyncSession,
    organization_id: uuid.UUID,
    contact_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_history: list[HistoryMessage],
) -> AgentTurnResult:
    """Run one turn of the agent's tool-calling loop and return its final
    plain-text reply plus whether the agent considers the conversation
    resolved.

    `message_history` is the conversation loaded from the `messages` table
    by the caller (see the module docstring), already including the latest
    inbound turn; this function windows it, converts it to Gemini's
    `Content` format, and drives the `agent`/`tools` graph until Gemini
    returns a plain-text response, however many rounds of tool calls that
    takes. Webhook handlers (app/api/webhooks.py) are the intended caller.

    `AgentTurnResult.should_end_call` is `True` once the model has called
    the `end_call` tool (app/agent/tools.py) during the turn, however many
    rounds ago that was; see `AgentState.should_end_call`'s docstring for
    why that flag survives the rest of the turn once set.
    """
    graph = _build_graph(session)
    initial_state: AgentState = {
        "organization_id": organization_id,
        "contact_id": contact_id,
        "conversation_id": conversation_id,
        "contents": _history_to_contents(message_history),
        "pending_tool_calls": [],
        "executed_tool_calls": [],
        "response_text": None,
        "should_end_call": False,
    }
    final_state = await graph.ainvoke(initial_state)
    return AgentTurnResult(
        reply_text=final_state["response_text"] or "",
        should_end_call=final_state["should_end_call"],
    )
