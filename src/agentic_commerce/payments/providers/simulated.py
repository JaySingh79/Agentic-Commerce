"""The offline provider used when no credentials are configured — or refused.

It is deterministic per receipt: replaying the same idempotency key returns the same
reference id, so the simulated path exercises exactly the dedupe behaviour the real
providers must show, instead of hiding a bug behind fresh random ids in tests.
"""

from __future__ import annotations

import hashlib

from agentic_commerce.payments.models import PaymentResult, PaymentState
from agentic_commerce.payments.settings import PaymentSettings


class SimulatedProvider:
    """Records an authorization locally. Nothing leaves the process."""

    name = "simulated"

    def __init__(self, settings: PaymentSettings | None = None) -> None:
        self.settings = settings or PaymentSettings.load()
        self._orders: dict[str, PaymentResult] = {}

    def preflight(self) -> None:
        """No credentials to check."""

    async def create_order(
        self, amount_cents: int, currency: str, receipt: str
    ) -> PaymentResult:
        """Returns a clearly-labelled non-live authorization."""
        existing = self._orders.get(receipt)
        if existing is not None:
            return existing
        result = PaymentResult(
            provider=self.name,
            status="authorized",
            reference_id=f"sim_{_digest(receipt)}",
            amount_cents=amount_cents,
            currency=currency,
            live=False,
            raw={"receipt": receipt, "note": "test-mode authorization recorded locally"},
            state=PaymentState.AUTHORIZED.value,
            idempotency_key=receipt,
        )
        self._orders[receipt] = result
        return result

    async def fetch_order(self, reference_id: str) -> PaymentResult | None:
        """Returns a previously simulated order by reference id."""
        for result in self._orders.values():
            if result.reference_id == reference_id:
                return result
        return None

    async def find_by_receipt(self, receipt: str) -> PaymentResult | None:
        """Returns a previously simulated order by idempotency key."""
        return self._orders.get(receipt)


def _digest(receipt: str) -> str:
    """Stable 16-char id for a receipt, so replays are recognisable as replays."""
    return hashlib.sha256(receipt.encode()).hexdigest()[:16]
