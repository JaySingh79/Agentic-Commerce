"""Phase 4: AP2 Payment Mandate Engine.

Provides cryptographically verifiable payment authorization mandates using
HMAC-SHA256 signing over a canonical mandate string, with tamper detection and
expiry validation. Behavior is intentionally identical to the original
``backend/ap2.py`` (which now re-exports from here).
"""

import hashlib
import hmac
import os
import time
import uuid
from typing import Any

# Status literals kept as module constants; also mirrored by core.models.MandateStatus.
STATUS_PENDING = "AUTHORIZED_PENDING_SETTLEMENT"


class AP2Engine:
    """Generates, signs, and validates Agent Payment Protocol (AP2) payment mandates."""

    def __init__(self, secret_key: str | None = None):
        self.secret_key = secret_key or os.getenv("CLIENT_SECRET") or "ap2_default_secret_key"

    def create_payment_mandate(
        self,
        cart_id: str,
        amount_cents: int,
        currency: str = "USD",
        merchant_domain: str = "shopify.com",
        max_duration_seconds: int = 3600,
        buyer_id: str = "user_default",
    ) -> dict[str, Any]:
        """Creates a signed, verifiable AP2 Payment Authorization Mandate."""
        now = int(time.time())
        mandate_id = f"ap2_mandate_{uuid.uuid4().hex[:16]}"
        expires_at = now + max_duration_seconds

        mandate_payload = {
            "ap2_version": "2026-04-08",
            "mandate_id": mandate_id,
            "buyer_id": buyer_id,
            "cart_id": cart_id,
            "merchant_domain": merchant_domain,
            "amount_cents": amount_cents,
            "currency": currency.upper(),
            "created_at": now,
            "expires_at": expires_at,
            "status": STATUS_PENDING,
            "spending_limit_cents": amount_cents,
        }

        # Cryptographic signature
        canonical = f"{mandate_id}:{cart_id}:{amount_cents}:{currency}:{expires_at}"
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        mandate_payload["signature"] = signature
        return mandate_payload

    def verify_mandate(self, mandate: dict[str, Any]) -> bool:
        """Verifies the integrity and validity of an AP2 mandate."""
        now = int(time.time())
        if mandate.get("expires_at", 0) < now:
            return False

        mandate_id = mandate.get("mandate_id", "")
        cart_id = mandate.get("cart_id", "")
        amount_cents = mandate.get("amount_cents", 0)
        currency = mandate.get("currency", "USD")
        expires_at = mandate.get("expires_at", 0)
        provided_sig = mandate.get("signature", "")

        canonical = f"{mandate_id}:{cart_id}:{amount_cents}:{currency}:{expires_at}"
        expected_sig = hmac.new(
            self.secret_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        return hmac.compare_digest(provided_sig, expected_sig)

    def format_mandate_display(self, mandate: dict[str, Any]) -> str:
        """Renders an AP2 mandate as markdown for the user chat UI."""
        amount_fmt = f"${(mandate['amount_cents'] / 100):.2f}"
        return (
            "### 💳 Agent Payment Protocol (AP2) Mandate\n\n"
            f"- **Mandate ID:** `{mandate['mandate_id']}`\n"
            f"- **Authorized Amount:** **{amount_fmt} {mandate['currency']}**\n"
            f"- **Merchant:** `{mandate['merchant_domain']}`\n"
            f"- **Status:** `{mandate['status']}`\n"
            f"- **Cart Ref:** `{mandate['cart_id']}`\n"
            f"- **Cryptographic Signature:** `{mandate['signature'][:16]}...`\n\n"
            "```json\n"
            f"{{\n"
            f'  "mandate_id": "{mandate["mandate_id"]}",\n'
            f'  "amount_cents": {mandate["amount_cents"]},\n'
            f'  "currency": "{mandate["currency"]}",\n'
            f'  "status": "{mandate["status"]}"\n'
            f"}}\n"
            "```\n"
        )
