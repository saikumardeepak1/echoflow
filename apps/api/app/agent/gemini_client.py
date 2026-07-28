"""Thin seam around the real Gemini client.

The agent graph's `agent` node (see app/agent/graph.py) calls
`generate_content` in this module rather than constructing a
`genai.Client(...)` and calling `.models.generate_content(...)` itself.
That indirection is the whole point of this module: tests monkeypatch
`generate_content` directly (see tests/test_agent_graph.py) to return a
canned `types.GenerateContentResponse`, so the graph's routing logic is
fully exercised without a live GEMINI_API_KEY or any network access.

The `genai.Client` itself is constructed lazily and cached at module level
(`_client`) rather than at import time, so importing this module never
requires `settings.gemini_api_key` to be a real key (the config default is
a placeholder, see app/core/config.py) and every test that never calls
`generate_content` for real never touches the client at all.
"""

from google import genai
from google.genai import types

from app.core.config import settings

# gemini-2.0-flash supports function calling and is the fast/cheap tier
# appropriate for a synchronous, latency-sensitive voice/SMS reply loop
# (see docs/TDD.md section 3.1).
MODEL_NAME = "gemini-2.0-flash"

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def generate_content(
    contents: list[types.Content], tools: list[types.Tool]
) -> types.GenerateContentResponse:
    """Call Gemini with the running conversation `contents` and the agent's
    `tools` function declarations, and return its response.

    The sole seam between the graph and the network: swap this function out
    (monkeypatch it in tests, or replace it with a different implementation
    entirely) to change what "calling Gemini" means without touching
    app/agent/graph.py at all.
    """
    client = _get_client()
    return client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        # `GenerateContentConfig.tools` is typed as `list[Tool | Callable |
        # ...]`; `list` is invariant, so our more specific `list[Tool]`
        # doesn't satisfy it even though every element is a valid member of
        # that union.
        config=types.GenerateContentConfig(tools=tools),  # type: ignore[arg-type]
    )
