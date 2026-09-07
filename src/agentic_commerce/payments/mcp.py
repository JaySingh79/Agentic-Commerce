"""Compatibility shim over the MCP provider in ``payments.providers.mcp``.

The synchronous ``mcp_*`` helpers stay because ``backend/payments.py`` and the demo
scripts call them from sync code; each one drives the async client through
:func:`~agentic_commerce.core.runtime.run_async`.
"""

from __future__ import annotations

from typing import Any

from agentic_commerce.core.runtime import run_async
from agentic_commerce.payments.models import PaymentResult
from agentic_commerce.payments.providers.mcp import (
    BRIDGE_ENV,
    CONTAINER_ENV,
    ID_SENTINEL,
    REQUIRED_TOOLS,
    TOOLSETS_ENV,
    RazorpayMcpProvider,
    _bridge_argv,
    _bridge_enabled,
)
from agentic_commerce.payments.settings import BRIDGE_TIMEOUT as TIMEOUT


def mcp_call_tool(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Calls one MCP tool after enforcing the test-key guard. Returns a dict."""
    return run_async(RazorpayMcpProvider().call_tool(tool, arguments))


def mcp_create_order(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Creates a Razorpay order (test mode) through the local MCP container."""
    return run_async(RazorpayMcpProvider().create_order(amount_cents, currency, receipt))


def mcp_fetch_order(order_id: str) -> PaymentResult | None:
    """Fetches an order by id through the local MCP container."""
    return run_async(RazorpayMcpProvider().fetch_order(order_id))


def mcp_fetch_payment(payment_id: str) -> PaymentResult | None:
    """Fetches a payment by id through the local MCP container."""
    provider = RazorpayMcpProvider()
    data = run_async(provider.call_tool("fetch_payment", {ID_SENTINEL: payment_id}))
    return provider._to_result(data, int(data.get("amount", 0)), "", "")


__all__ = [
    "BRIDGE_ENV",
    "CONTAINER_ENV",
    "ID_SENTINEL",
    "REQUIRED_TOOLS",
    "TIMEOUT",
    "TOOLSETS_ENV",
    "RazorpayMcpProvider",
    "_bridge_argv",
    "_bridge_enabled",
    "mcp_call_tool",
    "mcp_create_order",
    "mcp_fetch_order",
    "mcp_fetch_payment",
]
