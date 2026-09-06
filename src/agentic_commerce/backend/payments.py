"""Thin shim over :mod:`agentic_commerce.payments` (kept for import stability).

All logic moved verbatim into ``agentic_commerce.payments``; this module only
re-exports the same names so ``backend.crew``, ``backend.tools``,
``api.server``, and the tests keep working unchanged.
"""

from agentic_commerce.payments import (
    PaymentConfigurationError,
    PaymentResult,
    PaymentSignatureError,
    _bridge_enabled,
    _charge_razorpay,
    _charge_stripe,
    _simulate,
    mcp_call_tool,
    mcp_create_order,
    mcp_fetch_order,
    mcp_fetch_payment,
    process_payment,
    select_provider,
    verify_payment_signature,
    verify_webhook_signature,
)

__all__ = [
    "PaymentConfigurationError",
    "PaymentResult",
    "PaymentSignatureError",
    "_bridge_enabled",
    "_charge_razorpay",
    "_charge_stripe",
    "_simulate",
    "mcp_call_tool",
    "mcp_create_order",
    "mcp_fetch_order",
    "mcp_fetch_payment",
    "process_payment",
    "select_provider",
    "verify_payment_signature",
    "verify_webhook_signature",
]
