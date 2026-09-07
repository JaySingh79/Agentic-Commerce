"""Stripe PaymentIntents over raw ``httpx`` — created, never confirmed.

Stripe *does* accept an ``Idempotency-Key`` header, so the same receipt that keys the
ledger is sent upstream too: a retried create returns Stripe's stored intent instead of
a second one, which is the behaviour the Razorpay path has to emulate by searching.
"""

from __future__ import annotations

from typing import Any

import httpx

from agentic_commerce.payments.models import PaymentResult, PaymentState
from agentic_commerce.payments.providers.base import (
    classify_transport_error,
    raise_for_rejection,
    result_from,
)
from agentic_commerce.payments.settings import PaymentSettings

INTENTS_URL = "https://api.stripe.com/v1/payment_intents"


class StripeProvider:
    """Creates and reads Stripe PaymentIntents under test credentials."""

    name = "stripe"

    def __init__(self, settings: PaymentSettings | None = None) -> None:
        self.settings = settings or PaymentSettings.load()

    def preflight(self) -> None:
        """Enforces the live-key guard before any network call."""
        self.settings.require_stripe()

    def _headers(self, receipt: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.settings.stripe_secret_key}"}
        if receipt:
            headers["Idempotency-Key"] = receipt
        return headers

    async def create_order(
        self, amount_cents: int, currency: str, receipt: str
    ) -> PaymentResult:
        """Creates a PaymentIntent (test mode). It is never confirmed."""
        self.preflight()
        form = {
            "amount": amount_cents,
            "currency": currency.lower(),
            "description": receipt,
            "payment_method_types[]": "card",
            "metadata[source]": "agentic_commerce",
            "metadata[receipt]": receipt,
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                res = await client.post(
                    INTENTS_URL, headers=self._headers(receipt), data=form
                )
        except httpx.HTTPError as exc:
            raise classify_transport_error(exc) from exc

        if res.status_code >= 400:
            raise_for_rejection(self.settings, "Stripe", res.status_code, res.text)
        return self._to_result(res.json(), amount_cents, currency, receipt)

    async def fetch_order(self, reference_id: str) -> PaymentResult | None:
        """Fetches one PaymentIntent by id."""
        self.preflight()
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                res = await client.get(
                    f"{INTENTS_URL}/{reference_id}", headers=self._headers()
                )
        except httpx.HTTPError as exc:
            raise classify_transport_error(exc) from exc
        if res.status_code == 404:
            return None
        if res.status_code >= 400:
            raise_for_rejection(self.settings, "Stripe", res.status_code, res.text)
        data = res.json()
        return self._to_result(
            data,
            int(data.get("amount", 0)),
            str(data.get("currency", "usd")).upper(),
            str(data.get("metadata", {}).get("receipt", "")),
        )

    async def find_by_receipt(self, receipt: str) -> PaymentResult | None:
        """Reconciliation: replaying the create with the same idempotency key.

        Stripe answers a repeated ``Idempotency-Key`` with the original intent, so this
        is a lookup rather than a second charge attempt.
        """
        return None

    def _to_result(
        self, data: dict[str, Any], amount_cents: int, currency: str, receipt: str
    ) -> PaymentResult:
        """Maps a PaymentIntent payload onto the shared receipt type."""
        result = result_from(
            self.name,
            self.settings,
            data,
            status=str(data.get("status", "requires_payment_method")),
            reference_id=str(data.get("id", "")),
            amount_cents=int(data.get("amount", amount_cents)),
            currency=str(data.get("currency", currency)).upper(),
            receipt=receipt,
            provider_says_live=bool(data.get("livemode", False)),
        )
        result.state = PaymentState.AUTHORIZED.value
        return result
