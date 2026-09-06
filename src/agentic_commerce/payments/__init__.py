"""Payments domain package: Razorpay connection plus provider orchestration.

``backend/payments.py`` is a thin shim over this package so existing import
paths keep working while the logic lives here.
"""

from agentic_commerce.payments.config import select_provider
from agentic_commerce.payments.gateway import _charge_stripe, _simulate, process_payment
from agentic_commerce.payments.mcp import (
    _bridge_enabled,
    mcp_call_tool,
    mcp_create_order,
    mcp_fetch_order,
    mcp_fetch_payment,
)
from agentic_commerce.payments.models import (
    PaymentConfigurationError,
    PaymentResult,
    PaymentSignatureError,
)
from agentic_commerce.payments.razorpay import (
    _charge_razorpay,
    verify_payment_signature,
    verify_webhook_signature,
)

__all__ = [
    "PaymentConfigurationError",
    "PaymentResult",
    "PaymentSignatureError",
    "_charge_razorpay",
    "_charge_stripe",
    "_simulate",
    "_bridge_enabled",
    "mcp_call_tool",
    "mcp_create_order",
    "mcp_fetch_order",
    "mcp_fetch_payment",
    "process_payment",
    "select_provider",
    "verify_payment_signature",
    "verify_webhook_signature",
]
