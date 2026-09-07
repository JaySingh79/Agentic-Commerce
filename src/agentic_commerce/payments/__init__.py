"""Payments domain package: provider connections plus flow orchestration.

``backend/payments.py`` is a thin shim over this package so existing import paths keep
working while the logic lives here.
"""

from agentic_commerce.payments.flow import PaymentFlow, authorize_payment
from agentic_commerce.payments.gateway import (
    _charge_razorpay,
    _charge_stripe,
    _simulate,
    process_payment,
)
from agentic_commerce.payments.ledger import (
    MandateAlreadyUsedError,
    PaymentLedger,
    get_ledger,
    set_ledger,
)
from agentic_commerce.payments.mcp import (
    _bridge_enabled,
    mcp_call_tool,
    mcp_create_order,
    mcp_fetch_order,
    mcp_fetch_payment,
)
from agentic_commerce.payments.models import (
    IllegalTransitionError,
    PaymentConfigurationError,
    PaymentError,
    PaymentResult,
    PaymentSignatureError,
    PaymentState,
    ProviderRejectedError,
    ProviderTransientError,
    ProviderUnknownError,
)
from agentic_commerce.payments.providers.razorpay import (
    verify_payment_signature,
    verify_webhook_signature,
)
from agentic_commerce.payments.settings import PaymentSettings, select_provider

__all__ = [
    "IllegalTransitionError",
    "MandateAlreadyUsedError",
    "PaymentConfigurationError",
    "PaymentError",
    "PaymentFlow",
    "PaymentLedger",
    "PaymentResult",
    "PaymentSettings",
    "PaymentSignatureError",
    "PaymentState",
    "ProviderRejectedError",
    "ProviderTransientError",
    "ProviderUnknownError",
    "_bridge_enabled",
    "_charge_razorpay",
    "_charge_stripe",
    "_simulate",
    "authorize_payment",
    "get_ledger",
    "mcp_call_tool",
    "mcp_create_order",
    "mcp_fetch_order",
    "mcp_fetch_payment",
    "process_payment",
    "select_provider",
    "set_ledger",
    "verify_payment_signature",
    "verify_webhook_signature",
]
