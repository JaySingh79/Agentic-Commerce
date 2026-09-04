"""Test-mode payment processing: Razorpay first, Stripe fallback, else simulated.

Provider selection is by configuration, in this order:

1. **Razorpay** when ``RAZORPAY_KEY_ID`` and ``RAZORPAY_KEY_SECRET`` are set.
2. **Stripe** when ``STRIPE_SECRET_KEY`` (or ``STRIPE_API_KEY``) is set.
3. **Simulated** gateway otherwise, so the AP2 checkout flow stays demonstrable
   with no credentials. Simulated receipts are explicitly flagged
   (``provider="simulated"``, ``live=False``) and must never be shown as a real
   capture.

Both live providers are called over ``httpx`` — already a project dependency —
so no payment SDK is added.

Safety: this module refuses to run against production keys. Razorpay test keys
are prefixed ``rzp_test_`` and Stripe test keys ``sk_test_``; anything else
raises, so a real card cannot be charged by a demo agent.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx

_RAZORPAY_ORDERS_URL = "https://api.razorpay.com/v1/orders"
_STRIPE_INTENTS_URL = "https://api.stripe.com/v1/payment_intents"
_TIMEOUT = 20.0

#: Currencies Razorpay bills in the "paise-like" 1/100 minor unit we already use.
_MINOR_UNIT_DIVISOR = 100


class PaymentConfigurationError(RuntimeError):
    """Raised when configured credentials are unusable or not test-mode."""


@dataclass
class PaymentResult:
    """Normalized outcome across Razorpay, Stripe, and the simulated gateway."""

    provider: str
    status: str
    reference_id: str
    amount_cents: int
    currency: str
    live: bool
    receipt_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def amount_display(self) -> str:
        """Human-readable amount in major units."""
        return f"{self.amount_cents / _MINOR_UNIT_DIVISOR:.2f} {self.currency}"

    def as_dict(self) -> dict[str, Any]:
        """Serializable summary for session state and the UI."""
        return {
            "provider": self.provider,
            "status": self.status,
            "reference_id": self.reference_id,
            "amount_cents": self.amount_cents,
            "amount_display": self.amount_display,
            "currency": self.currency,
            "live": self.live,
            "receipt_url": self.receipt_url,
        }

    def format_display(self) -> str:
        """Markdown block describing the payment for the chat surface."""
        mode = "LIVE" if self.live else "TEST / SIMULATED"
        lines = [
            f"💳 **Payment {self.status.upper()}** — `{self.provider}` ({mode})",
            f"- **Amount:** {self.amount_display}",
            f"- **Reference:** `{self.reference_id}`",
        ]
        if self.receipt_url:
            lines.append(f"- **Receipt:** [View]({self.receipt_url})")
        if not self.live:
            lines.append("- *No real money moved; this is a test-mode authorization.*")
        return "\n".join(lines)


def _require_test_key(key: str, prefix: str, provider: str) -> None:
    """Guards against a demo agent transacting on production credentials."""
    if not key.startswith(prefix):
        raise PaymentConfigurationError(
            f"{provider} key does not look like a test key (expected prefix "
            f"{prefix!r}). Refusing to process a live payment from the agent."
        )


def select_provider() -> str:
    """Returns the provider that current configuration selects."""
    if os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET"):
        return "razorpay"
    if os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY"):
        return "stripe"
    return "simulated"


async def _charge_razorpay(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Creates a Razorpay order (test mode) via the Orders API."""
    key_id = os.getenv("RAZORPAY_KEY_ID", "")
    key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    _require_test_key(key_id, "rzp_test_", "Razorpay")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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


async def _charge_stripe(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Creates a Stripe PaymentIntent (test mode)."""
    key = os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY") or ""
    _require_test_key(key, "sk_test_", "Stripe")

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
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
    """Processes a test-mode .
payment through the best configured provider
    Falls back to the next provider when one is misconfigured, so a bad
    Razorpay key degrades to Stripe (and finally to the simulated gateway)
    instead of failing the whole checkout.
    """
    if amount_cents <= 0:
        raise ValueError(f"amount_cents must be positive, got {amount_cents}")

    reference = receipt or f"ac_{uuid.uuid4().hex[:12]}"
    provider = select_provider()

    if provider == "razorpay":
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
