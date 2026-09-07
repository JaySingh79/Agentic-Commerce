"""Razorpay over MCP, using a real MCP client session. **Development path only.**

The ``razorpay/mcp`` image speaks MCP over stdio and exposes no port, so the transport
is ``docker exec -i`` into the running container. That is a development arrangement, not
a production one: it needs the host Docker socket and a root-ish app container. Direct
REST stays the production path; this exists so the MCP tool surface (orders, payments,
and eventually refunds/links) can be exercised locally.

What changed from the previous bridge: it now *is* an MCP client. ``mcp.ClientSession``
performs a real ``initialize`` handshake, waits for the response before sending anything
else, negotiates capabilities, and enforces a per-request timeout. The old code wrote
``initialize``, ``notifications/initialized`` and ``tools/call`` into one stdin blob
before reading a single byte, dropped any stdout line that failed to parse, and had its
every failure swallowed by the caller — so a broken bridge looked exactly like a working
one that had chosen REST, and could create a second order for the same receipt.

Keys are still expanded *inside* the container (``"$RAZORPAY_KEY_ID"``), so the secret
never enters this process's argv.
"""

from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Any

from agentic_commerce.payments.models import (
    PaymentConfigurationError,
    PaymentResult,
    PaymentState,
    ProviderUnknownError,
)
from agentic_commerce.payments.settings import PaymentSettings

#: Opt-in flag: only bridge to the container when explicitly enabled.
BRIDGE_ENV = "RAZORPAY_MCP_BRIDGE"
#: Container name override (default ``razorpay-mcp``).
CONTAINER_ENV = "RAZORPAY_MCP_CONTAINER"
#: Toolsets enabled server-side (default ``orders,payments``).
TOOLSETS_ENV = "RAZORPAY_MCP_TOOLSETS"

#: Tools this provider refuses to run without. Checked against ``tools/list``.
REQUIRED_TOOLS = frozenset({"create_order", "fetch_order"})

#: Placeholder for "the id argument, whatever this server calls it".
#:
#: The container's fetch tools have named their id parameter ``id``, ``order_id`` and
#: ``payment_id`` across image versions. Rather than guess, the session resolves the
#: name from the tool's own ``inputSchema`` — which is available for free now that the
#: handshake actually completes before the call.
ID_SENTINEL = "__id__"


def _bridge_enabled() -> bool:
    """Returns True only when the MCP bridge is explicitly opted into."""
    return os.getenv(BRIDGE_ENV, "") == "1"


def _bridge_argv(settings: PaymentSettings | None = None) -> list[str]:
    """Builds the ``docker exec`` command; keys expand inside the container."""
    cfg = settings or PaymentSettings.load()
    return [
        "docker",
        "exec",
        "-i",
        cfg.mcp_container,
        "sh",
        "-c",
        "exec ./razorpay-mcp-server stdio"
        ' --key "$RAZORPAY_KEY_ID" --secret "$RAZORPAY_KEY_SECRET"'
        f" --toolsets {cfg.mcp_toolsets}",
    ]


class RazorpayMcpProvider:
    """Talks to the local ``razorpay-mcp`` container as an MCP client."""

    name = "razorpay"

    def __init__(self, settings: PaymentSettings | None = None) -> None:
        self.settings = settings or PaymentSettings.load()

    def preflight(self) -> None:
        """Enforces the live-key guard before any container is contacted."""
        self.settings.require_razorpay()

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Runs one MCP tool call inside a fully negotiated session."""
        self.preflight()
        argv = _bridge_argv(self.settings)
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:  # pragma: no cover - dependency is in the lockfile
            raise PaymentConfigurationError(
                "MCP bridge needs the `mcp` package."
            ) from exc

        server = StdioServerParameters(command=argv[0], args=argv[1:])
        timeout = timedelta(seconds=self.settings.bridge_timeout)
        try:
            async with (
                stdio_client(server) as (read, write),
                ClientSession(read, write, read_timeout_seconds=timeout) as session,
            ):
                await session.initialize()
                listing = await session.list_tools()
                self._assert_tools(listing, tool)
                resolved = _resolve_id_argument(listing, tool, arguments)
                response = await session.call_tool(tool, resolved)
        except FileNotFoundError as exc:
            raise PaymentConfigurationError(
                "MCP bridge needs the `docker` CLI on PATH."
            ) from exc
        except (PaymentConfigurationError, ProviderUnknownError):
            raise
        except Exception as exc:  # noqa: BLE001 - any transport fault is a bridge fault
            raise PaymentConfigurationError(
                f"MCP bridge session failed: {self.settings.redact(str(exc))[:300]}"
            ) from exc

        return self._unwrap(tool, response)

    def _assert_tools(self, listing: Any, tool: str) -> None:
        """Refuses to proceed unless the server actually advertises what we need."""
        available = {item.name for item in getattr(listing, "tools", [])}
        missing = ({tool} | REQUIRED_TOOLS) - available
        if missing:
            raise PaymentConfigurationError(
                "MCP server does not expose required tools: "
                f"{', '.join(sorted(missing))} (has: {', '.join(sorted(available))})"
            )

    def _unwrap(self, tool: str, response: Any) -> dict[str, Any]:
        """Turns an MCP tool response into a dict, or explains why it cannot."""
        if getattr(response, "isError", False):
            raise PaymentConfigurationError(
                f"MCP tool {tool} reported an error: "
                f"{self.settings.redact(_first_text(response))[:300]}"
            )
        text = _first_text(response)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PaymentConfigurationError(
                f"MCP tool {tool} returned unparseable content: {text[:300]}"
            ) from exc
        if not isinstance(data, dict):
            raise PaymentConfigurationError(
                f"MCP tool {tool} returned a non-object result: {data!r:.300}"
            )
        return data

    async def create_order(
        self, amount_cents: int, currency: str, receipt: str
    ) -> PaymentResult:
        """Creates a Razorpay order through the container."""
        data = await self.call_tool(
            "create_order",
            {"amount": amount_cents, "currency": currency, "receipt": receipt},
        )
        return self._to_result(data, amount_cents, currency, receipt)

    async def fetch_order(self, reference_id: str) -> PaymentResult | None:
        """Fetches an order by id through the container."""
        data = await self.call_tool("fetch_order", {ID_SENTINEL: reference_id})
        return self._to_result(data, int(data.get("amount", 0)), "", "")

    async def find_by_receipt(self, receipt: str) -> PaymentResult | None:
        """The MCP toolset exposes no receipt search, so reconciliation is deferred.

        Returning ``None`` keeps the attempt in ``UNKNOWN`` where a webhook or a manual
        ``fetch_order`` can resolve it — which is honest, and strictly better than
        creating a second order to find out.
        """
        return None

    def _to_result(
        self, data: dict[str, Any], amount_cents: int, currency: str, receipt: str
    ) -> PaymentResult:
        """Maps an order/payment payload onto the shared receipt type."""
        return PaymentResult(
            provider="razorpay",
            status=str(data.get("status", "created")),
            reference_id=str(data.get("id", "")),
            amount_cents=int(data.get("amount", amount_cents)),
            currency=str(data.get("currency", currency or "INR")),
            live=self.settings.derive_live(),
            raw=data,
            state=PaymentState.AUTHORIZED.value,
            idempotency_key=receipt or None,
        )


def _resolve_id_argument(
    listing: Any, tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Renames :data:`ID_SENTINEL` to whatever this server's tool schema calls it."""
    if ID_SENTINEL not in arguments:
        return arguments
    value = arguments[ID_SENTINEL]
    schema: dict[str, Any] = {}
    for item in getattr(listing, "tools", []):
        if item.name == tool:
            schema = getattr(item, "inputSchema", None) or {}
            break
    properties = list((schema.get("properties") or {}).keys())
    required = [name for name in (schema.get("required") or []) if name in properties]
    candidates = required or properties
    name = next(
        (n for n in candidates if n in ("id", "order_id", "payment_id", "orderId")),
        candidates[0] if candidates else "id",
    )
    resolved = {k: v for k, v in arguments.items() if k != ID_SENTINEL}
    resolved[name] = value
    return resolved


def _first_text(response: Any) -> str:
    """Extracts ``content[0].text`` from an MCP response, tolerating shapes."""
    content = getattr(response, "content", None) or []
    for item in content:
        text = getattr(item, "text", None)
        if text:
            return str(text)
    return ""
