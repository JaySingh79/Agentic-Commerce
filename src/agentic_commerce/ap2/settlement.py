"""Phase 4: Mock Settlement Gateway.

Simulates capture/settlement of a verified AP2 mandate against an in-memory
ledger. Used because live ``complete_checkout`` on third-party Shopify stores is
gated (returns AuthenticationFailed → referral handoff, see project_memory.md
section 4.2), so settlement is proven locally against the signed mandate.
"""

import time
import uuid
from typing import Any

from agentic_commerce.ap2.mandate import AP2Engine
from agentic_commerce.core.models import MandateStatus
from agentic_commerce.payments.ledger import MandateAlreadyUsedError, get_ledger


class SettlementError(RuntimeError):
    """Raised when a mandate fails verification before capture."""


class MockSettlementGateway:
    """Captures verified AP2 mandates into a mock ledger and marks them SETTLED."""

    def __init__(self, engine: AP2Engine | None = None):
        self.engine = engine or AP2Engine()
        self._ledger: list[dict[str, Any]] = []

    def capture(self, mandate: dict[str, Any]) -> dict[str, Any]:
        """Verifies a mandate then records a settlement receipt in the ledger.

        Returns the receipt (with ``status`` ``SETTLED``). Raises
        :class:`SettlementError` if the mandate signature/expiry is invalid, or if the
        mandate has already been settled — an authorization is spent once, and that has
        to be enforced in the database rather than by a list this process happens to
        hold, or two concurrent captures both "win".
        """
        if not self.engine.verify_mandate(mandate):
            raise SettlementError("Mandate verification failed; refusing to settle.")

        mandate_id = str(mandate.get("mandate_id") or "")
        if not get_ledger().is_mandate_claimed(mandate_id, purpose="authorization"):
            raise SettlementError(
                "Mandate was never authorized through the payment flow; "
                "refusing to settle a charge that was never attempted."
            )
        settlement_id = f"stl_{uuid.uuid4().hex[:16]}"
        try:
            get_ledger().claim_mandate(mandate_id, settlement_id, purpose="settlement")
        except MandateAlreadyUsedError as exc:
            raise SettlementError(str(exc)) from exc

        receipt = {
            "settlement_id": settlement_id,
            "mandate_id": mandate.get("mandate_id"),
            "cart_id": mandate.get("cart_id"),
            "amount_cents": mandate.get("amount_cents", 0),
            "currency": mandate.get("currency", "USD"),
            "merchant_domain": mandate.get("merchant_domain"),
            "status": MandateStatus.SETTLED.value,
            "settled_at": int(time.time()),
        }
        self._ledger.append(receipt)
        return receipt

    def get_ledger(self) -> list[dict[str, Any]]:
        """Returns a copy of all recorded settlement receipts."""
        return list(self._ledger)

    def format_receipt_display(self, receipt: dict[str, Any]) -> str:
        """Renders a settlement receipt as markdown for the chat UI."""
        amount_fmt = f"${(receipt['amount_cents'] / 100):.2f}"
        return (
            "### ✅ Settlement Complete\n\n"
            f"- **Settlement ID:** `{receipt['settlement_id']}`\n"
            f"- **Mandate:** `{receipt['mandate_id']}`\n"
            f"- **Captured:** **{amount_fmt} {receipt['currency']}**\n"
            f"- **Merchant:** `{receipt['merchant_domain']}`\n"
            f"- **Status:** `{receipt['status']}`\n"
        )
