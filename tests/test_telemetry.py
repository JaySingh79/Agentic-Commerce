"""Unit tests for OpenTelemetry instrumentation and token tracking."""

from agentic_commerce.core.telemetry import (
    estimate_tokens,
    get_latest_session_stats,
    init_telemetry,
    record_llm_usage,
    trace_llm_call,
    trace_tool_execution,
    trace_turn,
)


def test_init_telemetry():
    tracer, meter = init_telemetry()
    assert tracer is not None
    assert meter is not None


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert estimate_tokens("hi") == 1
    assert estimate_tokens("a" * 100) == 25


def test_trace_turn_and_record_llm_usage():
    session_id = "test_otel_session"
    with (
        trace_turn(session_id=session_id, query="Find winter jackets"),
        trace_llm_call("gemini-2.5-flash", 0.4, session_id) as span,
    ):
        record_llm_usage(
            span,
            prompt_tokens=150,
            completion_tokens=85,
            model_name="gemini-2.5-flash",
            session_id=session_id,
        )

    stats = get_latest_session_stats(session_id)
    assert stats["session_id"] == session_id
    assert stats["query"] == "Find winter jackets"
    assert stats["prompt_tokens"] == 150
    assert stats["completion_tokens"] == 85
    assert stats["total_tokens"] == 235
    assert stats["latency_seconds"] >= 0.0


def _captured_spans():
    """Attaches an in-memory exporter to the live provider and returns it."""
    from opentelemetry import trace as _trace
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    init_telemetry()
    exporter = InMemorySpanExporter()
    _trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(exporter))
    return exporter


def test_llm_and_tool_spans_are_children_of_the_turn_span():
    """Spans must form one trace tree, otherwise Tempo shows disconnected roots."""
    exporter = _captured_spans()
    session_id = "test_span_tree_session"

    with trace_turn(session_id=session_id, query="find shoes"):
        with trace_tool_execution("search_products", {"q": "shoes"}, session_id=session_id):
            pass
        with trace_llm_call("gemini-2.5-flash", 0.4, session_id):
            pass

    spans = {s.name: s for s in exporter.get_finished_spans()}
    turn = spans["commerce.turn"]
    tool = spans["commerce.tool.search_products"]
    llm = spans["gen_ai.chat"]

    assert tool.parent is not None, "tool span is an orphan root"
    assert llm.parent is not None, "llm span is an orphan root"
    assert tool.parent.span_id == turn.context.span_id
    assert llm.parent.span_id == turn.context.span_id
    assert tool.context.trace_id == turn.context.trace_id
    assert llm.context.trace_id == turn.context.trace_id


def test_trace_tool_execution():
    session_id = "test_tool_otel_session"
    with (
        trace_turn(session_id=session_id, query="Add shoes to cart"),
        trace_tool_execution(
            tool_name="search_products",
            args={"query": "shoes"},
            session_id=session_id,
        ),
    ):
        pass

    stats = get_latest_session_stats(session_id)
    assert len(stats["tools_called"]) == 1
    tool_rec = stats["tools_called"][0]
    assert tool_rec["tool"] == "search_products"
    assert tool_rec["status"] == "success"
    assert tool_rec["duration_ms"] >= 0
