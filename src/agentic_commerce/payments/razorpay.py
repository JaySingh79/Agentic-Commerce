"""Razorpay API connection over ``httpx`` — no payment SDK is added.

Covers the Orders API used for test-mode order creation plus the HMAC-SHA256
helpers that verify a checkout payment signature and an incoming webhook
signature. ``receipt`` doubles as the idempotency key: Razorpay exposes no
``Idempotency-Key`` header, so callers must pass a unique receipt per order.
"""

from __future__ import annotations

import hashlib
import hmac
import os

import httpx

from agentic_commerce.payments.config import TIMEOUT, _require_test_key
from agentic_commerce.payments.models import PaymentConfigurationError, PaymentResult

_RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"


async def _charge_razorpay(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Creates a Razorpay order (test mode) via the Orders API."""
    key_id = os.getenv("RAZORPAY_KEY_ID", "")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    _require_test_key(key_id, "rzp_test_", "Razorpay")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.post(
            _RAZORPAY_ORDERS_URL,
            auth=(key_id, key_secret),
            json={
                "amount": amount_cents,
                "currency": currency,
                "receipt": receipt,
                "notes": {"source": "agentic_commerce", "flow": "ap2_mandate"},
            },
        )
    if res.status_code >= 400:
        raise PaymentConfigurationError(f"Razorpay error {res.status_code}: {res.text[:300]}")

    data = res.json()
    return PaymentResult(
        provider="razorpay",
        status=str(data.get("status", "created")),
        reference_id=str(data.get("id", "")),
        amount_cents=int(data.get("amount", amount_cents)),
        currency=str(data.get("currency", currency)),
        live=False,  # guarded to test keys above
        raw=data,
    )


def verify_payment_signature(
    order_id: str, payment_id: str, signature: str, secret: str | None = None
) -> bool:
    """Verifies a Razorpay checkout signature: ``HMAC-SHA256(order|payment)``."""
    key_secret = secret if secret is not None else os.getenv("RAZORPAY_KEY_SECRET", "")
    expected = hmac.new(
        key_secret.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_webhook_signature(raw_body: bytes, signature: str, secret: str) -> bool:
    """Verifies an ``X-Razorpay-Signature`` header over the raw webhook body."""
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
