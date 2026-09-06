"""Provider orchestration: Razorpay first, Stripe fallback, else simulated."""

from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from contextlib import suppress

import httpx

from agentic_commerce.payments.config import TIMEOUT, _require_test_key, select_provider
from agentic_commerce.payments.mcp import _bridge_enabled, mcp_create_order
from agentic_commerce.payments.models import PaymentConfigurationError, PaymentResult
from agentic_commerce.payments.razorpay import _charge_razorpay

_STRIPE_INTENTS_URL = "https://api.stripe.com/v1/payment_intents"


async def _charge_stripe(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Creates a Stripe PaymentIntent (test mode)."""
    key = os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY") or ""
    _require_test_key(key, "sk_test_", "Stripe")

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        res = await client.post(
            _STRIPE_INTENTS_URL,
            headers={"Authorization": f"Bearer {key}"},
            data={
                "amount": amount_cents,
                "currency": currency.lower(),
                "description": receipt,
                "payment_method_types[]": "card",
                "metadata[source]": "agentic_commerce",
            },
        )
    if res.status_code >= 400:
        raise PaymentConfigurationError(f"Stripe error {res.status_code}: {res.text[:300]}")

    data = res.json()
    return PaymentResult(
        provider="stripe",
        status=str(data.get("status", "requires_payment_method")),
        reference_id=str(data.get("id", "")),
        amount_cents=int(data.get("amount", amount_cents)),
        currency=str(data.get("currency", currency)).upper(),
        live=bool(data.get("livemode", False)),
        raw=data,
    )


def _simulate(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Deterministic offline gateway used when no credentials are configured."""
    return PaymentResult(
        provider="simulated",
        status="authorized",
        reference_id=f"sim_{uuid.uuid4().hex[:16]}",
        amount_cents=amount_cents,
        currency=currency,
        live=False,
        raw={"receipt": receipt, "note": "no payment credentials configured"},
    )


async def process_payment(
    amount_cents: int,
    currency: str = "USD",
    receipt: str | None = None,
) -> PaymentResult:
    """Processes a test-mode payment through the best configured provider.

    When the MCP bridge is enabled, the local Razorpay MCP container creates
    the order first; otherwise (or when the bridge fails) direct REST is used.
    Falls back to the next provider when one is misconfigured, so a bad
    Razorpay key degrades to Stripe (and finally to the simulated gateway)
    instead of failing the whole checkout.
    """
    if amount_cents <= 0:
        raise ValueError(f"amount_cents must be positive, got {amount_cents}")

    reference = receipt or f"ac_{uuid.uuid4().hex[:12]}"
    provider = select_provider()

    if provider == "razorpay":
        if _bridge_enabled():
            with suppress(
                PaymentConfigurationError, subprocess.SubprocessError, OSError
            ):
                return await asyncio.to_thread(
                    mcp_create_order, amount_cents, currency, reference
                )
        try:
            return await _charge_razorpay(amount_cents, currency, reference)
        except (PaymentConfigurationError, httpx.HTTPError):
            provider = "stripe" if (
                os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY")
            ) else "simulated"

    if provider == "stripe":
        try:
            return await _charge_stripe(amount_cents, currency, reference)
        except (PaymentConfigurationError, httpx.HTTPError):
            provider = "simulated"

    return _simulate(amount_cents, currency, reference)
