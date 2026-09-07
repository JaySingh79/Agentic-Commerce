"""Compatibility shim. The Razorpay connection now lives in ``payments.providers``.

``_charge_razorpay`` and the signature helpers keep their import paths so
``backend/payments.py``, ``api/server.py``, and the tests resolve unchanged.
"""

from __future__ import annotations

from agentic_commerce.payments.models import PaymentResult
from agentic_commerce.payments.providers.razorpay import (
    ORDERS_URL as _RAZORPAY_ORDERS_URL,
)
from agentic_commerce.payments.providers.razorpay import (
    RazorpayProvider,
    verify_payment_signature,
    verify_webhook_signature,
)


async def _charge_razorpay(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Creates a Razorpay order (test mode) via the Orders API."""
    return await RazorpayProvider().create_order(amount_cents, currency, receipt)


__all__ = [
    "RazorpayProvider",
    "_RAZORPAY_ORDERS_URL",
    "_charge_razorpay",
    "verify_payment_signature",
    "verify_webhook_signature",
]
