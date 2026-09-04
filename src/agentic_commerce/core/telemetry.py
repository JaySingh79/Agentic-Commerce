"""OpenTelemetry Tracing, Metrics, and Token Observability for Agentic Commerce.

Exports traces and metrics to Grafana via OTLP (Tempo / Prometheus / OpenTelemetry Collector).
Tracks every tool invocation, LLM call, latency, and token consumption.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Status, StatusCode

# Silence noisy OTLP export retry messages when Grafana/Tempo is not yet running
logging.getLogger("opentelemetry.exporter").setLevel(logging.CRITICAL)
logging.getLogger("opentelemetry.sdk").setLevel(logging.CRITICAL)

logger = logging.getLogger("agentic_commerce.telemetry")

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "agentic-commerce")
_RESOURCE = Resource.create({"service.name": _SERVICE_NAME, "service.version": "0.1.0"})

# In-memory telemetry cache for real-time UI display
_LATEST_SESSION_STATS: dict[str, dict[str, Any]] = {}

# Active turn span per session, used to parent LLM/tool spans explicitly.
# OTel's implicit context is a ContextVar, which does not survive Gradio's
# per-step threadpool, so parenting is keyed on the explicit session id instead.
_ACTIVE_TURN_SPANS: dict[str, trace.Span] = {}

_INITIALIZED = False
_TRACER: trace.Tracer | None = None
_METER: metrics.Meter | None = None

_TOKEN_COUNTER: metrics.Counter | None = None
_LLM_DURATION: metrics.Histogram | None = None
_TOOL_COUNTER: metrics.Counter | None = None
_TOOL_DURATION: metrics.Histogram | None = None


def _exporters_enabled() -> bool:
    """Whether to attach OTLP exporters.

    Exporters are attached unconditionally (unless explicitly disabled) rather
    than gated on a startup reachability probe: the probe latched a single
    result for the process lifetime, so an app started before the telemetry
    stack stayed permanently un-instrumented. ``BatchSpanProcessor`` already
    buffers and drops gracefully while the collector is down.
    """
    return os.getenv("OTEL_SDK_DISABLED", "").lower() not in {"true", "1", "yes"}


def _parent_context(session_id: str):
    """Returns an OTel context parented to the session's active turn span, if any."""
    parent = _ACTIVE_TURN_SPANS.get(session_id)
    return trace.set_span_in_context(parent) if parent is not None else None


def init_telemetry() -> tuple[trace.Tracer, metrics.Meter]:
    """Initializes OpenTelemetry Tracer and Meter with OTLP HTTP exporters."""
    global _INITIALIZED, _TRACER, _METER, _TOKEN_COUNTER
    global _LLM_DURATION, _TOOL_COUNTER, _TOOL_DURATION

    if _INITIALIZED and _TRACER is not None and _METER is not None:
        return _TRACER, _METER

    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    traces_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", f"{otlp_endpoint.rstrip('/')}/v1/traces"
    )
    metrics_endpoint = os.getenv(
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", f"{otlp_endpoint.rstrip('/')}/v1/metrics"
    )

    is_online = _exporters_enabled()

    # 1. Tracing Setup
    tracer_provider = TracerProvider(resource=_RESOURCE)
    if is_online:
        try:
            otlp_span_exporter = OTLPSpanExporter(endpoint=traces_endpoint, timeout=2)
            tracer_provider.add_span_processor(BatchSpanProcessor(otlp_span_exporter))
        except Exception as exc:  # noqa: BLE001
            logger.debug("OTLP trace exporter init warning: %s", exc)

    trace.set_tracer_provider(tracer_provider)
    _TRACER = trace.get_tracer("agentic-commerce.tracer", "0.1.0")

    # 2. Metrics Setup
    if is_online:
        try:
            otlp_metric_exporter = OTLPMetricExporter(endpoint=metrics_endpoint, timeout=2)
            metric_reader = PeriodicExportingMetricReader(
                otlp_metric_exporter, export_interval_millis=5000
            )
            meter_provider = MeterProvider(resource=_RESOURCE, metric_readers=[metric_reader])
        except Exception as exc:  # noqa: BLE001
            logger.debug("OTLP metric exporter init warning: %s", exc)
            meter_provider = MeterProvider(resource=_RESOURCE)
    else:
        meter_provider = MeterProvider(resource=_RESOURCE)

    metrics.set_meter_provider(meter_provider)
    _METER = metrics.get_meter("agentic-commerce.meter", "0.1.0")

    _TOKEN_COUNTER = _METER.create_counter(
        name="gen_ai.usage.tokens",
        description="Total tokens consumed by LLM interactions",
        unit="{token}",
    )
    _LLM_DURATION = _METER.create_histogram(
        name="gen_ai.client.operation.duration",
        description="Duration of LLM calls in seconds",
        unit="s",
    )
    _TOOL_COUNTER = _METER.create_counter(
        name="commerce.tool.invocations",
        description="Total invocations of commerce tools",
        unit="{call}",
    )
    _TOOL_DURATION = _METER.create_histogram(
        name="commerce.tool.duration",
        description="Duration of commerce tool executions in seconds",
        unit="s",
    )

    _INITIALIZED = True
    return _TRACER, _METER


def get_tracer() -> trace.Tracer:
    """Returns the initialized OpenTelemetry Tracer."""
    if _TRACER is None:
        tracer, _ = init_telemetry()
        return tracer
    return _TRACER


def estimate_tokens(text: str) -> int:
    """Rough heuristic token estimator (~4 characters per token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


@contextmanager
def trace_turn(session_id: str, query: str) -> Generator[trace.Span, None, None]:
    """Context manager tracing a complete user interaction turn without ContextVar detach issues."""
    tracer = get_tracer()
    span = tracer.start_span(
        "commerce.turn",
        attributes={
            "session.id": session_id,
            "user.query": query,
        },
    )
    _LATEST_SESSION_STATS[session_id] = {
        "session_id": session_id,
        "query": query,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "tools_called": [],
        "latency_seconds": 0.0,
        "timestamp": time.time(),
    }
    start_t = time.time()
    _ACTIVE_TURN_SPANS[session_id] = span
    try:
        yield span
        span.set_status(Status(StatusCode.OK))
    except Exception as err:
        span.set_status(Status(StatusCode.ERROR, str(err)))
        span.record_exception(err)
        raise
    finally:
        elapsed = round(time.time() - start_t, 3)
        if session_id in _LATEST_SESSION_STATS:
            _LATEST_SESSION_STATS[session_id]["latency_seconds"] = elapsed
        _ACTIVE_TURN_SPANS.pop(session_id, None)
        span.end()


@contextmanager
def trace_llm_call(
    model_name: str,
    temperature: float = 0.4,
    session_id: str = "default_user_session",
) -> Generator[trace.Span, None, None]:
    """Context manager tracing an LLM invocation safely across generator yields."""
    tracer = get_tracer()
    start_t = time.time()
    span = tracer.start_span(
        "gen_ai.chat",
        context=_parent_context(session_id),
        attributes={
            "gen_ai.system": "google_genai",
            "gen_ai.request.model": model_name,
            "gen_ai.request.temperature": temperature,
            "session.id": session_id,
        },
    )
    try:
        yield span
        span.set_status(Status(StatusCode.OK))
    except Exception as err:
        span.set_status(Status(StatusCode.ERROR, str(err)))
        span.record_exception(err)
        raise
    finally:
        dur = time.time() - start_t
        if _LLM_DURATION is not None:
            _LLM_DURATION.record(dur, {"gen_ai.request.model": model_name})
        span.end()


def record_llm_usage(
    span: trace.Span,
    prompt_tokens: int,
    completion_tokens: int,
    model_name: str = "gemini-2.5-flash",
    session_id: str = "default_user_session",
) -> None:
    """Records token usage attributes on span, metric counters, and session cache."""
    total_tokens = prompt_tokens + completion_tokens
    span.set_attribute("gen_ai.usage.input_tokens", prompt_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", completion_tokens)
    span.set_attribute("gen_ai.usage.total_tokens", total_tokens)

    if _TOKEN_COUNTER is not None:
        _TOKEN_COUNTER.add(
            prompt_tokens, {"gen_ai.request.model": model_name, "token_type": "input"}
        )
        _TOKEN_COUNTER.add(
            completion_tokens, {"gen_ai.request.model": model_name, "token_type": "output"}
        )

    if session_id in _LATEST_SESSION_STATS:
        stat = _LATEST_SESSION_STATS[session_id]
        stat["prompt_tokens"] += prompt_tokens
        stat["completion_tokens"] += completion_tokens
        stat["total_tokens"] += total_tokens


@contextmanager
def trace_tool_execution(
    tool_name: str,
    args: dict[str, Any] | None = None,
    session_id: str = "default_user_session",
) -> Generator[trace.Span, None, None]:
    """Context manager tracing a commerce tool invocation safely across thread boundaries."""
    tracer = get_tracer()
    start_t = time.time()
    args_json = json.dumps(args, default=str) if args else "{}"
    span = tracer.start_span(
        f"commerce.tool.{tool_name}",
        context=_parent_context(session_id),
        attributes={
            "gen_ai.tool.name": tool_name,
            "gen_ai.tool.parameters": args_json,
            "session.id": session_id,
        },
    )
    status_label = "success"
    try:
        yield span
        span.set_status(Status(StatusCode.OK))
    except Exception as err:
        status_label = "error"
        span.set_status(Status(StatusCode.ERROR, str(err)))
        span.record_exception(err)
        raise
    finally:
        dur = time.time() - start_t
        if _TOOL_DURATION is not None:
            _TOOL_DURATION.record(dur, {"tool_name": tool_name, "status": status_label})
        if _TOOL_COUNTER is not None:
            _TOOL_COUNTER.add(1, {"tool_name": tool_name, "status": status_label})
        if session_id in _LATEST_SESSION_STATS:
            _LATEST_SESSION_STATS[session_id]["tools_called"].append({
                "tool": tool_name,
                "duration_ms": round(dur * 1000, 1),
                "status": status_label,
            })
        span.end()


def get_latest_session_stats(session_id: str = "default_user_session") -> dict[str, Any]:
    """Returns the latest captured telemetry statistics for the given session."""
    return _LATEST_SESSION_STATS.get(
        session_id,
        {
            "session_id": session_id,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "tools_called": [],
            "latency_seconds": 0.0,
        },
    )
