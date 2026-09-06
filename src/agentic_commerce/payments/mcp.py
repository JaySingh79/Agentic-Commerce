"""JSON-RPC bridge to the local Razorpay MCP server running in Docker.

The ``razorpay-mcp`` container speaks MCP (JSON-RPC 2.0) over stdio only — it
exposes no HTTP port — so this module carries the same JSON-RPC frames over a
``docker exec -i`` pipe instead of TCP. Key expansion happens *inside* the
container (``"$RAZORPAY_KEY_ID"``), so the Python process never holds the
secret; the local ``RAZORPAY_KEY_ID`` is read only to enforce the test-key
guard before bridging.

Enable with ``RAZORPAY_MCP_BRIDGE=1`` (explicit opt-in; direct REST stays the
default). ``RAZORPAY_MCP_CONTAINER`` overrides the container name and
``RAZORPAY_MCP_TOOLSETS`` the enabled toolsets (default ``orders,payments``).

One stateless subprocess per call: initialize, notify, single tools/call,
close stdin, parse stdout. Payments are rare (Cashier), so the ~1s spawn cost
is acceptable and avoids all lifecycle bookkeeping.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
from typing import Any

from agentic_commerce.payments.config import _require_test_key
from agentic_commerce.payments.models import PaymentConfigurationError, PaymentResult

#: Opt-in flag: only bridge to the container when explicitly enabled.
BRIDGE_ENV = "RAZORPAY_MCP_BRIDGE"
#: Container name override (default ``razorpay-mcp``).
CONTAINER_ENV = "RAZORPAY_MCP_CONTAINER"
#: Toolsets enabled server-side (default ``orders,payments``).
TOOLSETS_ENV = "RAZORPAY_MCP_TOOLSETS"

_DEFAULT_CONTAINER = "razorpay-mcp"
_DEFAULT_TOOLSETS = "orders,payments"

#: Hard cap on the whole bridge exchange (spawn + handshake + tool call).
TIMEOUT = 60.0


def _bridge_enabled() -> bool:
    """Returns True only when the MCP bridge is explicitly opted into."""
    return os.getenv(BRIDGE_ENV, "") == "1"


def _bridge_argv() -> list[str]:
    """Builds the ``docker exec`` command; keys expand inside the container."""
    container = os.getenv(CONTAINER_ENV, "") or _DEFAULT_CONTAINER
    toolsets = os.getenv(TOOLSETS_ENV, "") or _DEFAULT_TOOLSETS
    return [
        "docker",
        "exec",
        "-i",
        container,
        "sh",
        "-c",
        "exec ./razorpay-mcp-server stdio"
        ' --key "$RAZORPAY_KEY_ID" --secret "$RAZORPAY_KEY_SECRET"'
        f" --toolsets {toolsets}",
    ]


def _exchange(tool: str, arguments: dict[str, Any]) -> Any:
    """Runs one initialize + tools/call exchange, returns the result payload."""
    counter = itertools.count(1)
    init_id, call_id = next(counter), next(counter)
    lines = [
        {
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "agentic-commerce", "version": "0.1.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        },
    ]
    payload = "".join(json.dumps(line) + "\n" for line in lines)

    try:
        proc = subprocess.run(
            _bridge_argv(),
            input=payload,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except FileNotFoundError as exc:
        raise PaymentConfigurationError(
            "MCP bridge needs the `docker` CLI on PATH."
        ) from exc

    responses: dict[int, dict[str, Any]] = {}
    for raw in proc.stdout.splitlines():
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(message.get("id"), int):
            responses[message["id"]] = message

    for expect, label in ((init_id, "initialize"), (call_id, f"tools/call {tool}")):
        if expect not in responses:
            raise PaymentConfigurationError(
                f"MCP bridge got no {label} response: {proc.stderr[-300:]}"
            )
        if "error" in responses[expect]:
            raise PaymentConfigurationError(
                f"MCP bridge {label} failed: {responses[expect]['error']}"
            )
    return responses[call_id].get("result")


def _content_json(result: Any) -> dict[str, Any]:
    """Unwraps MCP ``content[0].text`` (a JSON string) into a dict."""
    try:
        text = result["content"][0]["text"]
        data = json.loads(text)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise PaymentConfigurationError(
            f"MCP bridge returned an unparseable tool result: {result!r:.300}"
        ) from exc
    if not isinstance(data, dict):
        raise PaymentConfigurationError(
            f"MCP bridge returned a non-object tool result: {data!r:.300}"
        )
    return data


def mcp_call_tool(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Calls one MCP tool after enforcing the test-key guard. Returns a dict."""
    _require_test_key(os.getenv("RAZORPAY_KEY_ID", ""), "rzp_test_", "Razorpay")
    return _content_json(_exchange(tool, arguments))


def _to_payment_result(data: dict[str, Any]) -> PaymentResult:
    """Maps an order/payment payload onto the shared receipt type."""
    return PaymentResult(
        provider="razorpay",
        status=str(data.get("status", "created")),
        reference_id=str(data.get("id", "")),
        amount_cents=int(data.get("amount", 0)),
        currency=str(data.get("currency", "INR")),
        live=False,  # guarded to test keys above
        raw=data,
    )


def mcp_create_order(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Creates a Razorpay order (test mode) through the local MCP container."""
    return _to_payment_result(
        mcp_call_tool(
            "create_order",
            {"amount": amount_cents, "currency": currency, "receipt": receipt},
        )
    )


def mcp_fetch_order(order_id: str) -> PaymentResult:
    """Fetches an order by id through the local MCP container."""
    return _to_payment_result(mcp_call_tool("fetch_order", {"id": order_id}))


def mcp_fetch_payment(payment_id: str) -> PaymentResult:
    """Fetches a payment by id through the local MCP container."""
    return _to_payment_result(mcp_call_tool("fetch_payment", {"id": payment_id}))
