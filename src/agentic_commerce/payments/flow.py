"""The deterministic payment flow: cart → mandate → authorize → capture → settle.

This module exists because the model used to decide when money moved. The agent could
call ``process_test_payment`` with any amount at any point in a conversation, and the
AP2 mandate it had just minted was never consulted — mandate and charge were unrelated
code paths. Prose is not an authorization.

Here the order is a table, not a suggestion. Every guard below is a separate refusal
with its own name, because "invalid" tells a shopper nothing: expired, tampered,
over-limit, wrong cart, wrong merchant and already-spent are six different problems.
An LLM tool can *start* this flow; it cannot skip a step in it.

Capture is deliberately implemented and deliberately switched off: the transition exists
and is tested, but :class:`CaptureNotEnabledError` fires while the live-key guard stands,
because nothing in this repository may move real money.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agentic_commerce.ap2.mandate import AP2Engine
from agentic_commerce.payments.gateway import process_payment
from agentic_commerce.payments.ledger import PaymentLedger, get_ledger
from agentic_commerce.payments.models import (
    PaymentError,
    PaymentResult,
    PaymentState,
    validate_amount,
    validate_currency,
)

logger = logging.getLogger(__name__)


class MandateExpiredError(PaymentError):
    """The mandate's authorization window has closed."""


class MandateTamperedError(PaymentError):
    """The mandate's signature does not match its contents."""


class MandateScopeError(PaymentError):
    """The requested charge falls outside what the mandate authorizes."""


class CaptureNotEnabledError(PaymentError):
    """Capture is structurally implemented but disabled: no real money moves here."""


class PaymentFlow:
    """Runs an authorization through every guard, in order, exactly once."""

    def __init__(
        self,
        engine: AP2Engine | None = None,
        ledger: PaymentLedger | None = None,
    ) -> None:
        self.engine = engine or AP2Engine()
        self._ledger = ledger

    @property
    def ledger(self) -> PaymentLedger:
        """The ledger this flow records into (process-wide unless injected)."""
        return self._ledger or get_ledger()

    def idempotency_key(self, mandate: dict[str, Any], session_id: str = "") -> str:
        """The key that makes a retried authorization a no-op rather than a second one."""
        return f"{session_id}:{mandate.get('mandate_id', '')}:{mandate.get('amount_cents', 0)}"

    def check_mandate(
        self,
        mandate: dict[str, Any],
        amount_cents: int | None = None,
        currency: str | None = None,
        expected_cart_id: str | None = None,
        expected_merchant: str | None = None,
    ) -> tuple[int, str]:
        """Runs every pre-charge guard. Returns the amount and currency to charge.

        Raises the specific refusal rather than a generic one, so the surface above can
        tell the shopper which thing went wrong.
        """
        if not self.engine.verify_mandate(mandate):
            if int(mandate.get("expires_at", 0) or 0) < int(time.time()):
                raise MandateExpiredError("This authorization has expired.")
            raise MandateTamperedError(
                "The signature does not match this mandate's contents."
            )

        limit = int(
            mandate.get("spending_limit_cents") or mandate.get("amount_cents") or 0
        )
        amount = validate_amount(
            amount_cents if amount_cents is not None else int(mandate.get("amount_cents", 0))
        )
        if amount > limit:
            raise MandateScopeError(
                f"charge of {amount} exceeds the mandate's spending limit of {limit}"
            )

        mandate_currency = validate_currency(str(mandate.get("currency") or "USD"))
        charge_currency = validate_currency(currency or mandate_currency)
        if charge_currency != mandate_currency:
            raise MandateScopeError(
                f"mandate authorizes {mandate_currency}, not {charge_currency}"
            )

        if expected_cart_id and str(mandate.get("cart_id") or "") != expected_cart_id:
            raise MandateScopeError("mandate does not authorize this cart")
        if (
            expected_merchant
            and str(mandate.get("merchant_domain") or "") != expected_merchant
        ):
            raise MandateScopeError("mandate does not authorize this merchant")

        return amount, charge_currency

    async def authorize(
        self,
        mandate: dict[str, Any],
        session_id: str = "",
        amount_cents: int | None = None,
        currency: str | None = None,
        expected_cart_id: str | None = None,
        expected_merchant: str | None = None,
    ) -> PaymentResult:
        """Authorizes a charge against a verified, unspent, in-scope mandate."""
        amount, charge_currency = self.check_mandate(
            mandate,
            amount_cents=amount_cents,
            currency=currency,
            expected_cart_id=expected_cart_id,
            expected_merchant=expected_merchant,
        )
        mandate_id = str(mandate.get("mandate_id") or "")
        key = self.idempotency_key(mandate, session_id)

        # Claiming before charging means a duplicated request is refused by the database,
        # not by whichever coroutine happens to check first.
        self.ledger.claim_mandate(mandate_id, key)

        result = await process_payment(
            amount,
            charge_currency,
            key,
            session_id=session_id,
            mandate_id=mandate_id,
            ledger=self.ledger,
        )
        logger.info(
            "authorized %s for mandate %s (%s)", result.reference_id, mandate_id, result.state
        )
        return result

    async def capture(self, idempotency_key: str) -> PaymentResult:
        """Would move an authorization to captured. Refuses while the guard stands."""
        attempt = self.ledger.get(idempotency_key)
        if attempt is None:
            raise PaymentError(f"unknown payment attempt {idempotency_key!r}")
        if attempt["state"] != PaymentState.AUTHORIZED.value:
            raise PaymentError(
                f"cannot capture an attempt in state {attempt['state']!r}"
            )
        raise CaptureNotEnabledError(
            "Capture is disabled: this deployment authorizes test-mode payments only "
            "and never moves money. Enabling it requires live credentials, which the "
            "payment settings refuse by design."
        )


async def authorize_payment(
    mandate: dict[str, Any],
    session_id: str = "",
    amount_cents: int | None = None,
    currency: str | None = None,
    expected_cart_id: str | None = None,
    expected_merchant: str | None = None,
    engine: AP2Engine | None = None,
) -> PaymentResult:
    """Module-level entry point for a mandate-gated authorization."""
    return await PaymentFlow(engine=engine).authorize(
        mandate,
        session_id=session_id,
        amount_cents=amount_cents,
        currency=currency,
        expected_cart_id=expected_cart_id,
        expected_merchant=expected_merchant,
    )
