"""Tests for the JSON-RPC bridge to the local Razorpay MCP container.

All tests are hermetic: the ``docker exec`` subprocess is faked, so no
container or credential is needed. Live-container verification is manual
(``RAZORPAY_MCP_BRIDGE=1`` against ``razorpay-mcp``).
"""

import json
import subprocess

import pytest

from agentic_commerce.backend import payments as backend_payments
from agentic_commerce.backend.payments import PaymentConfigurationError
from agentic_commerce.core.runtime import run_async
from agentic_commerce.payments import gateway as payment_gateway
from agentic_commerce.payments import mcp as payments_mcp

MCP_ENV = ("RAZORPAY_MCP_BRIDGE", "RAZORPAY_MCP_CONTAINER", "RAZORPAY_MCP_TOOLSETS")


@pytest.fixture(autouse=True)
def clean_mcp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolates each test from bridge config and any real credentials."""
    for name in MCP_ENV + ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET"):
        monkeypatch.delenv(name, raising=False)


def _lines(*messages: dict) -> str:
    return "".join(json.dumps(message) + "\n" for message in messages)


def _ok_stdout(tool_result: dict) -> str:
    return _lines(
        {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
        {"jsonrpc": "2.0", "id": 2, "result": tool_result},
    )


def _fake_run(stdout: str):
    def _run(*_args, **_kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    return _run


def test_bridge_is_opt_in(monkeypatch: pytest.MonkeyPatch):
    assert payments_mcp._bridge_enabled() is False
    monkeypatch.setenv("RAZORPAY_MCP_BRIDGE", "1")
    assert payments_mcp._bridge_enabled() is True


def test_bridge_argv_expands_keys_inside_the_container(monkeypatch: pytest.MonkeyPatch):
    """The secret value must never be embedded in the argv Python builds."""
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "shh-test-value")
    argv = payments_mcp._bridge_argv()
    assert argv[:3] == ["docker", "exec", "-i"]
    assert '"$RAZORPAY_KEY_ID"' in argv[-1]
    assert "shh-test-value" not in " ".join(argv)


def test_exchange_returns_tool_result(monkeypatch: pytest.MonkeyPatch):
    stdout = _ok_stdout({"content": [{"type": "text", "text": '{"id": "order_1"}'}]})
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout))
    assert payments_mcp._exchange("fetch_order", {"id": "order_1"}) == {
        "content": [{"type": "text", "text": '{"id": "order_1"}'}]
    }


def test_exchange_raises_on_tool_error(monkeypatch: pytest.MonkeyPatch):
    stdout = _lines(
        {"jsonrpc": "2.0", "id": 1, "result": {}},
        {"jsonrpc": "2.0", "id": 2, "error": {"code": -32000, "message": "nope"}},
    )
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout))
    with pytest.raises(PaymentConfigurationError, match="tools/call"):
        payments_mcp._exchange("fetch_order", {"id": "order_1"})


def test_exchange_raises_when_server_goes_quiet(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(""))
    with pytest.raises(PaymentConfigurationError, match="no initialize"):
        payments_mcp._exchange("fetch_order", {"id": "order_1"})


def test_create_order_maps_onto_payment_result(monkeypatch: pytest.MonkeyPatch):
    order = {"id": "order_9A33", "amount": 100, "currency": "INR", "status": "created"}

    def _ok(tool: str, arguments: dict):
        assert tool == "create_order"
        assert arguments == {"amount": 100, "currency": "INR", "receipt": "r1"}
        return order

    monkeypatch.setattr(payments_mcp, "mcp_call_tool", _ok)
    result = payments_mcp.mcp_create_order(100, "INR", "r1")
    assert result.provider == "razorpay"
    assert result.reference_id == "order_9A33"
    assert result.live is False


def test_live_key_is_refused_before_spawning(monkeypatch: pytest.MonkeyPatch):
    """Guard: the bridge must not reach Docker on production credentials."""
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_dangerous")

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("subprocess must not spawn on a live key")

    monkeypatch.setattr(subprocess, "run", _must_not_run)
    with pytest.raises(PaymentConfigurationError, match="test key"):
        payments_mcp.mcp_call_tool("fetch_order", {"id": "order_1"})


def test_gateway_prefers_bridge_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAZORPAY_MCP_BRIDGE", "1")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")

    def _bridged(amount_cents, currency, receipt):
        return payment_gateway.PaymentResult(
            provider="razorpay",
            status="created",
            reference_id="order_mcp1",
            amount_cents=amount_cents,
            currency=currency,
            live=False,
        )

    async def _direct_must_not_run(*_args, **_kwargs):
        raise AssertionError("direct REST must not run when the bridge succeeds")

    monkeypatch.setattr(payment_gateway, "mcp_create_order", _bridged)
    monkeypatch.setattr(payment_gateway, "_charge_razorpay", _direct_must_not_run)
    result = run_async(payment_gateway.process_payment(100, "INR"))
    assert result.reference_id == "order_mcp1"


def test_gateway_falls_back_to_rest_when_bridge_fails(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAZORPAY_MCP_BRIDGE", "1")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")

    def _boom(*_args, **_kwargs):
        raise OSError("docker down")

    async def _direct(amount_cents, currency, receipt):
        return payment_gateway.PaymentResult(
            provider="razorpay",
            status="created",
            reference_id="order_rest1",
            amount_cents=amount_cents,
            currency=currency,
            live=False,
        )

    monkeypatch.setattr(payment_gateway, "mcp_create_order", _boom)
    monkeypatch.setattr(payment_gateway, "_charge_razorpay", _direct)
    result = run_async(payment_gateway.process_payment(100, "INR"))
    assert result.reference_id == "order_rest1"


def test_gateway_ignores_bridge_when_not_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_abc")
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", "secret")

    async def _bridged_must_not_run(*_args, **_kwargs):
        raise AssertionError("bridge must not run when not enabled")

    async def _direct(amount_cents, currency, receipt):
        return payment_gateway.PaymentResult(
            provider="razorpay",
            status="created",
            reference_id="order_rest2",
            amount_cents=amount_cents,
            currency=currency,
            live=False,
        )

    monkeypatch.setattr(payment_gateway, "mcp_create_order", _bridged_must_not_run)
    monkeypatch.setattr(payment_gateway, "_charge_razorpay", _direct)
    result = run_async(payment_gateway.process_payment(100, "INR"))
    assert result.reference_id == "order_rest2"


def test_backend_shim_exposes_bridge_names():
    """The shim keeps every bridge name reachable from the old import path."""
    assert backend_payments.mcp_create_order is payments_mcp.mcp_create_order
    assert backend_payments._bridge_enabled is payments_mcp._bridge_enabled
