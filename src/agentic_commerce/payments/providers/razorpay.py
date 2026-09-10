"""Razorpay Orders API over raw ``httpx`` — no payment SDK is added.

Razorpay exposes no ``Idempotency-Key`` header, so ``receipt`` carries that job: it is
the key the ledger stores and the key :meth:`RazorpayProvider.find_by_receipt` uses to
answer "did my request land?" after a transmitted-but-unanswered call. Orders are
*created*, never paid — capture is not implemented while the live-key guard stands.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import httpx

from agentic_commerce.payments.models import PaymentResult, PaymentState
from agentic_commerce.payments.providers.base import (
    classify_transport_error,
    raise_for_rejection,
    result_from,
)
from agentic_commerce.payments.settings import PaymentSettings

ORDERS_URL = "https://api.razorpay.com/v1/orders"

#: Razorpay caps the order `receipt` field (40 chars). Ledger idempotency keys
#: are longer (`session:mandate:amount:currency:merchant:cart`), so long keys
#: are hashed — deterministically, so create, find-by-receipt, and the webhook
#: resolver all agree on the wire value while the ledger keeps the full key.
_RAZORPAY_RECEIPT_LIMIT = 40


def short_receipt(receipt: str) -> str:
    """Maps a ledger idempotency key onto a Razorpay-legal receipt value."""
    if len(receipt) <= _RAZORPAY_RECEIPT_LIMIT:
        return receipt
    return "ac_" + hashlib.sha256(receipt.encode()).hexdigest()[:32]


class RazorpayProvider:
    """Creates and reads Razorpay orders under test credentials."""

    name = "razorpay"

    def __init__(self, settings: PaymentSettings | None = None) -> None:
        self.settings = settings or PaymentSettings.load()

    def preflight(self) -> None:
        """Enforces the live-key guard before any network call."""
        self.settings.require_razorpay()

    def _auth(self) -> tuple[str, str]:
        return (self.settings.razorpay_key_id, self.settings.razorpay_key_secret)

    async def create_order(
        self, amount_cents: int, currency: str, receipt: str
    ) -> PaymentResult:
        """Creates a Razorpay order (test mode) via the Orders API."""
        self.preflight()
        payload = {
            "amount": amount_cents,
            "currency": currency,
            "receipt": short_receipt(receipt),
            "notes": {"source": "agentic_commerce", "flow": "ap2_mandate"},
        }
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                res = await client.post(ORDERS_URL, auth=self._auth(), json=payload)
        except httpx.HTTPError as exc:
            raise classify_transport_error(exc) from exc

        if res.status_code >= 400:
            raise_for_rejection(self.settings, "Razorpay", res.status_code, res.text)
        return self._to_result(res.json(), amount_cents, currency, receipt)

    async def fetch_order(self, reference_id: str) -> PaymentResult | None:
        """Fetches one order by id."""
        self.preflight()
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                res = await client.get(f"{ORDERS_URL}/{reference_id}", auth=self._auth())
        except httpx.HTTPError as exc:
            raise classify_transport_error(exc) from exc
        if res.status_code == 404:
            return None
        if res.status_code >= 400:
            raise_for_rejection(self.settings, "Razorpay", res.status_code, res.text)
        data = res.json()
        return self._to_result(
            data,
            int(data.get("amount", 0)),
            str(data.get("currency", "INR")),
            str(data.get("receipt", "")),
        )

    async def find_by_receipt(self, receipt: str) -> PaymentResult | None:
        """Reconciliation: looks for an order already created under this key."""
        self.preflight()
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout) as client:
                res = await client.get(
                    ORDERS_URL, auth=self._auth(), params={"receipt": short_receipt(receipt)}
                )
        except httpx.HTTPError as exc:
            raise classify_transport_error(exc) from exc
        if res.status_code >= 400:
            raise_for_rejection(self.settings, "Razorpay", res.status_code, res.text)

        items = res.json().get("items") or []
        if not items:
            return None
        order = items[0]
        return self._to_result(
            order,
            int(order.get("amount", 0)),
            str(order.get("currency", "INR")),
            receipt,
        )

    def _to_result(
        self, data: dict[str, Any], amount_cents: int, currency: str, receipt: str
    ) -> PaymentResult:
        """Maps an order payload onto the shared receipt type."""
        result = result_from(
            self.name,
            self.settings,
            data,
            status=str(data.get("status", "created")),
            reference_id=str(data.get("id", "")),
            amount_cents=int(data.get("amount", amount_cents)),
            currency=str(data.get("currency", currency)),
            receipt=receipt,
            # Razorpay orders carry no ``livemode`` field; mode comes from the key,
            # which the guard has already constrained to a test key.
            provider_says_live=None,
        )
        result.state = PaymentState.AUTHORIZED.value
        return result


def verify_payment_signature(
    order_id: str, payment_id: str, signature: str, secret: str | None = None
) -> bool:
    """Verifies a Razorpay checkout signature: ``HMAC-SHA256(order|payment)``."""
    key_secret = (
        secret if secret is not None else PaymentSettings.load().razorpay_key_secret
    )
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
