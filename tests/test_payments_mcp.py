"""Tests for the Razorpay MCP provider — hermetic, no container required.

The MCP session is faked at the ``mcp`` SDK boundary (``stdio_client`` /
``ClientSession``), so these exercise the real handshake ordering, capability check,
argument resolution and error mapping without Docker. Live-container checks stay manual
and read-only: ``fetch_order`` before anything that writes.

The load-bearing test here is
``test_gateway_does_not_silently_fall_back_to_rest_when_the_bridge_fails``: the previous
bridge swallowed its own failures, so a broken bridge silently created a *second* order
through REST for the same receipt.
"""

import json
import types

import pytest

from agentic_commerce.core.runtime import run_async
from agentic_commerce.payments import gateway as payment_gateway
from agentic_commerce.payments.models import PaymentConfigurationError
from agentic_commerce.payments.providers import mcp as mcp_provider
from agentic_commerce.payments.providers.mcp import (
    RazorpayMcpProvider,
    _bridge_argv,
    _bridge_enabled,
)
from agentic_commerce.payments.providers.razorpay import RazorpayProvider

ORDER_JSON = {
    "id": "order_MCP123",
    "amount": 4500,
    "currency": "INR",
    "status": "created",
    "receipt": "cart_1",
}


class _FakeTool:
    def __init__(self, name: str, properties=None, required=None):
        self.name = name
        self.inputSchema = {
            "properties": {key: {"type": "string"} for key in (properties or [])},
            "required": list(required or []),
        }


class _FakeContent:
    def __init__(self, text: str):
        self.text = text


class _FakeResponse:
    def __init__(self, text: str, is_error: bool = False):
        self.content = [_FakeContent(text)]
        self.isError = is_error


class _FakeSession:
    """Records the call order so the handshake can be asserted, not assumed."""

    def __init__(self, calls, tools, response):
        self.calls = calls
        self._tools = tools
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def initialize(self):
        self.calls.append(("initialize", None))

    async def list_tools(self):
        self.calls.append(("list_tools", None))
        return types.SimpleNamespace(tools=self._tools)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


@pytest.fixture
def fake_bridge(monkeypatch: pytest.MonkeyPatch):
    """Installs a fake MCP stdio session and returns the recorded call log."""

    def _install(tools=None, response=None):
        calls: list[tuple] = []
        tools = tools if tools is not None else [
            _FakeTool("create_order", ["amount", "currency", "receipt"]),
            _FakeTool("fetch_order", ["order_id"], ["order_id"]),
        ]
        response = response if response is not None else _FakeResponse(json.dumps(ORDER_JSON))

        class _Stdio:
            def __init__(self, server):
                calls.append(("spawn", server.command, tuple(server.args)))

            async def __aenter__(self):
                return ("read", "write")

            async def __aexit__(self, *_exc):
                return False

        def _session(read, write, read_timeout_seconds=None):
            calls.append(("session", read_timeout_seconds))
            return _FakeSession(calls, tools, response)

        import mcp
        import mcp.client.stdio

        monkeypatch.setattr(mcp.client.stdio, "stdio_client", _Stdio)
        monkeypatch.setattr(mcp, "ClientSession", _session)
        return calls

    return _install


@pytest.fixture(autouse=True)
def test_keys(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "supersecret")


def test_bridge_is_opt_in(monkeypatch: pytest.MonkeyPatch):
    assert _bridge_enabled() is False
    monkeypatch.setenv("RAZORPAY_MCP_BRIDGE", "1")
    assert _bridge_enabled() is True


def test_bridge_argv_expands_keys_inside_the_container():
    """The secret must never appear in this process's argv."""
    argv = _bridge_argv()
    assert argv[:4] == ["docker", "exec", "-i", "razorpay-mcp"]
    assert '"$RAZORPAY_KEY_ID"' in argv[-1]
    assert "supersecret" not in " ".join(argv)


def test_handshake_completes_before_the_tool_is_called(fake_bridge):
    """The old bridge fired all three frames before reading anything."""
    calls = fake_bridge()
    run_async(RazorpayMcpProvider().create_order(4500, "INR", "cart_1"))

    names = [call[0] for call in calls]
    assert names.index("initialize") < names.index("list_tools") < names.index("create_order")


def test_create_order_maps_onto_payment_result(fake_bridge):
    fake_bridge()
    result = run_async(RazorpayMcpProvider().create_order(4500, "INR", "cart_1"))

    assert result.provider == "razorpay"
    assert result.reference_id == "order_MCP123"
    assert result.amount_cents == 4500
    assert result.currency == "INR"
    assert result.live is False


def test_missing_server_tools_are_refused_before_calling(fake_bridge):
    """Capability negotiation: an image without the orders toolset must not be used."""
    fake_bridge(tools=[_FakeTool("fetch_payment", ["payment_id"])])
    with pytest.raises(PaymentConfigurationError, match="required tools"):
        run_async(RazorpayMcpProvider().create_order(4500, "INR", "cart_1"))


def test_id_argument_is_resolved_from_the_server_schema(fake_bridge):
    """Different image versions name the id parameter differently."""
    calls = fake_bridge(
        tools=[
            _FakeTool("create_order", ["amount"]),
            _FakeTool("fetch_order", ["order_id"], ["order_id"]),
        ]
    )
    run_async(RazorpayMcpProvider().fetch_order("order_MCP123"))
    fetch_call = next(call for call in calls if call[0] == "fetch_order")
    assert fetch_call[1] == {"order_id": "order_MCP123"}


def test_tool_error_is_reported_not_swallowed(fake_bridge):
    fake_bridge(response=_FakeResponse("order creation failed", is_error=True))
    with pytest.raises(PaymentConfigurationError, match="reported an error"):
        run_async(RazorpayMcpProvider().create_order(4500, "INR", "cart_1"))


def test_unparseable_content_is_reported(fake_bridge):
    fake_bridge(response=_FakeResponse("<html>gateway timeout</html>"))
    with pytest.raises(PaymentConfigurationError, match="unparseable"):
        run_async(RazorpayMcpProvider().create_order(4500, "INR", "cart_1"))


def test_live_key_is_refused_before_the_container_is_contacted(
    monkeypatch: pytest.MonkeyPatch, fake_bridge
):
    calls = fake_bridge()
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_dangerous")
    with pytest.raises(PaymentConfigurationError, match="test key"):
        run_async(RazorpayMcpProvider().create_order(1000, "INR", "cart_1"))
    assert calls == []


def test_gateway_prefers_the_bridge_when_enabled(
    monkeypatch: pytest.MonkeyPatch, fake_bridge
):
    fake_bridge()
    monkeypatch.setenv("RAZORPAY_MCP_BRIDGE", "1")

    async def _rest_must_not_run(self, amount_cents, currency, receipt):
        raise AssertionError("REST was used while the bridge was enabled")

    monkeypatch.setattr(RazorpayProvider, "create_order", _rest_must_not_run)

    result = run_async(payment_gateway.process_payment(4500, "INR", "cart_1"))
    assert result.reference_id == "order_MCP123"


def test_gateway_does_not_silently_fall_back_to_rest_when_the_bridge_fails(
    monkeypatch: pytest.MonkeyPatch,
):
    """A failed bridge must surface, not quietly create the order twice."""
    monkeypatch.setenv("RAZORPAY_MCP_BRIDGE", "1")

    async def _broken(self, tool, arguments):
        raise PaymentConfigurationError("MCP bridge session failed: container is gone")

    async def _rest_must_not_run(self, amount_cents, currency, receipt):
        raise AssertionError("REST silently created a second order")

    monkeypatch.setattr(mcp_provider.RazorpayMcpProvider, "call_tool", _broken)
    monkeypatch.setattr(RazorpayProvider, "create_order", _rest_must_not_run)

    with pytest.raises(PaymentConfigurationError, match="container is gone"):
        run_async(payment_gateway.process_payment(4500, "INR", "cart_broken"))


def test_gateway_ignores_the_bridge_when_it_is_not_enabled(
    monkeypatch: pytest.MonkeyPatch, fake_bridge
):
    fake_bridge()

    async def _rest(self, amount_cents, currency, receipt):
        return payment_gateway.PaymentResult(
            provider="razorpay",
            status="created",
            reference_id="order_REST",
            amount_cents=amount_cents,
            currency=currency,
            live=False,
        )

    monkeypatch.setattr(RazorpayProvider, "create_order", _rest)
    result = run_async(payment_gateway.process_payment(4500, "INR", "cart_rest"))
    assert result.reference_id == "order_REST"


def test_backend_shim_still_exports_the_bridge_helpers():
    from agentic_commerce.backend import payments as backend_payments

    assert backend_payments.mcp_create_order is not None
    assert backend_payments._bridge_enabled is _bridge_enabled
