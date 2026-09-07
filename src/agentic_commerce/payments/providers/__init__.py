"""Payment providers behind one protocol, plus the resolver that picks one.

Resolution happens **once** per operation and is never revisited after a request has
been transmitted — see ``payments/gateway.py`` for why that ordering is the whole
safety argument.
"""

from __future__ import annotations

from agentic_commerce.payments.providers.base import (
    PaymentProvider,
    classify_transport_error,
    raise_for_rejection,
    result_from,
)
from agentic_commerce.payments.providers.mcp import RazorpayMcpProvider
from agentic_commerce.payments.providers.razorpay import (
    RazorpayProvider,
    verify_payment_signature,
    verify_webhook_signature,
)
from agentic_commerce.payments.providers.simulated import SimulatedProvider
from agentic_commerce.payments.providers.stripe import StripeProvider
from agentic_commerce.payments.settings import PaymentSettings

#: One simulated provider per process, so a replayed receipt is recognised as a replay
#: rather than answered with a fresh id.
_SIMULATED = SimulatedProvider()


def build_provider(name: str, settings: PaymentSettings) -> PaymentProvider:
    """Returns the provider implementation for ``name``.

    When the MCP bridge is opted into, it *replaces* the Razorpay REST client rather
    than sitting in front of it. Two Razorpay clients in one attempt is exactly how the
    old code could create two orders for one receipt.
    """
    if name == "razorpay":
        if settings.mcp_bridge:
            return RazorpayMcpProvider(settings)
        return RazorpayProvider(settings)
    if name == "stripe":
        return StripeProvider(settings)
    _SIMULATED.settings = settings
    return _SIMULATED


__all__ = [
    "PaymentProvider",
    "RazorpayMcpProvider",
    "RazorpayProvider",
    "SimulatedProvider",
    "StripeProvider",
    "build_provider",
    "classify_transport_error",
    "raise_for_rejection",
    "result_from",
    "verify_payment_signature",
    "verify_webhook_signature",
]
