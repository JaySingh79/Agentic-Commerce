"""Regression tests for the LLM streaming path in ``CommerceAgent.execute_stream``.

The existing streaming tests all drive the heuristic fallback (``agent.llm = None``),
which never enters the ``self.llm.stream(...)`` loop. That blind spot let a
``return`` ship where a ``yield`` belonged: the generator ended on the first text
token, so every conversational answer was dropped and tool execution was skipped
entirely. These tests exercise the real loop with a stubbed model.
"""

from typing import Any

import pytest

from agentic_commerce.backend.agent import CommerceAgent


class FakeChunk:
    """A minimal stand-in for a LangChain streaming chunk.

    Supports ``+`` because the agent aggregates chunks into a single message.
    """

    def __init__(
        self,
        content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        usage_metadata: dict[str, int] | None = None,
    ):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage_metadata = usage_metadata or {}

    def __add__(self, other: "FakeChunk") -> "FakeChunk":
        return FakeChunk(
            content=self.content + other.content,
            tool_calls=self.tool_calls + other.tool_calls,
            usage_metadata=other.usage_metadata or self.usage_metadata,
        )


class FakeLLM:
    """Streams a scripted sequence of chunks, then records follow-up calls."""

    def __init__(self, *turns: list[FakeChunk]):
        self._turns = list(turns)
        self.calls = 0

    def stream(self, messages: Any, **kwargs: Any):
        self.calls += 1
        turn = self._turns[min(self.calls - 1, len(self._turns) - 1)]
        yield from turn


@pytest.fixture
def agent() -> CommerceAgent:
    """An agent whose model is stubbed, so no network call is made."""
    return CommerceAgent(session_id="stream_test_session")


def test_text_deltas_reach_the_consumer(agent: CommerceAgent):
    """The bug: a `return` here ended the generator on the first token."""
    agent.llm = FakeLLM([FakeChunk("Here "), FakeChunk("are "), FakeChunk("five shirts.")])

    events = list(agent.execute_stream("find me shirts"))
    text = "".join(e["text"] for e in events if e["type"] == "content")

    assert text == "Here are five shirts."


def test_a_prose_turn_does_not_end_after_the_first_token(agent: CommerceAgent):
    agent.llm = FakeLLM([FakeChunk("A"), FakeChunk("B"), FakeChunk("C")])

    content_events = [e for e in agent.execute_stream("hi") if e["type"] == "content"]

    assert len(content_events) == 3, "generator terminated early"


def test_tools_still_run_after_leading_text(agent: CommerceAgent):
    """Step 2 was unreachable whenever the model spoke before calling a tool.

    A stubbed tool keeps this off the network while still proving the agent
    reaches tool execution and streams the follow-up synthesis.
    """
    tool_call = {"name": "search_products", "args": {"query": "shirts"}, "id": "call_1"}
    agent.llm = FakeLLM(
        [FakeChunk("Let me look. "), FakeChunk("", tool_calls=[tool_call])],
        [FakeChunk("Found some.")],
    )

    class StubTool:
        name = "search_products"

        def invoke(self, args: dict[str, Any]) -> str:
            return "Found 2 products for 'shirts'."

    agent.tool_map = dict(agent.tool_map, search_products=StubTool())

    events = list(agent.execute_stream("find shirts"))
    kinds = [e["type"] for e in events]

    assert "tool_call" in kinds, "never reached tool execution"
    assert "tool_result" in kinds
    assert "Let me look. " in "".join(e["text"] for e in events if e["type"] == "content")


def test_a_model_failure_surfaces_instead_of_vanishing(agent: CommerceAgent):
    class BoomLLM:
        def stream(self, messages: Any, **kwargs: Any):
            raise RuntimeError("model unavailable")
            yield  # pragma: no cover - generator marker

    agent.llm = BoomLLM()

    events = list(agent.execute_stream("hello"))

    assert events, "a failed turn produced no events at all"
    assert any("unavailable" in str(e.get("text", "")) for e in events)


def test_stream_calls_carry_a_timeout(agent: CommerceAgent):
    """A stalled Gemini response must not hang execute_stream forever.

    Without a call-time timeout, `self.llm.stream(messages)` can block
    indefinitely with nothing above it to interrupt it (see RCA in
    docs/architectural-workflow.md). This asserts every stream call actually
    carries the agent's timeout, and that a timeout raised mid-stream is
    caught by the existing fallback path instead of propagating as a hang.
    """
    seen_timeouts: list[Any] = []

    class TimeoutLLM:
        def stream(self, messages: Any, **kwargs: Any):
            seen_timeouts.append(kwargs.get("timeout"))
            raise TimeoutError("deadline exceeded")
            yield  # pragma: no cover - generator marker

    agent.llm = TimeoutLLM()

    events = list(agent.execute_stream("checkout"))

    assert seen_timeouts, "stream() was never called"
    assert all(t == CommerceAgent.LLM_TIMEOUT_SECONDS for t in seen_timeouts)
    assert events, "a timed-out turn produced no events at all (would hang the UI)"
