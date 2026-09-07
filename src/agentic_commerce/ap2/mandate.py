"""AP2 Payment Mandate engine: sign, verify, and refuse.

A mandate is the only thing standing between an autonomous agent and a charge, so the
signature has to cover the *whole* authorization. The previous canonical string signed
five fields — ``mandate_id:cart_id:amount_cents:currency:expires_at`` — which left
``merchant_domain``, ``buyer_id`` and ``spending_limit_cents`` unsigned: an attacker (or
a confused agent) could redirect a valid mandate to a different merchant, or raise its
spending limit, without breaking verification.

Signing is now HMAC-SHA256 over canonical JSON of every field except ``signature``:
sorted keys, no whitespace, so the bytes are reproducible and no field is left out by
construction. Mandates live for an hour, so no migration path is needed for the old
scheme — ``ap2_version`` is bumped and the old format simply stops verifying.
"""

import hashlib
import hmac
import json
import os
import time
import uuid
from typing import Any

# Status literals kept as module constants; also mirrored by core.models.MandateStatus.
STATUS_PENDING = "AUTHORIZED_PENDING_SETTLEMENT"

#: Bumped when the signing scheme changed from a 5-field string to canonical JSON.
AP2_VERSION = "2026-09-01"

#: The placeholder key the engine used when nothing was configured. Signing real
#: authorizations with a constant published in this repository is not a signature.
_INSECURE_DEFAULT_SECRET = "ap2_default_secret_key"


class MandateConfigurationError(RuntimeError):
    """Raised when the engine is asked to sign with an unusable key."""


class AP2Engine:
    """Generates, signs, and validates Agent Payment Protocol (AP2) payment mandates."""

    def __init__(self, secret_key: str | None = None):
        self.secret_key = secret_key or os.getenv("CLIENT_SECRET") or _INSECURE_DEFAULT_SECRET
        if self.secret_key == _INSECURE_DEFAULT_SECRET and _strict():
            raise MandateConfigurationError(
                "AC_PAYMENTS_STRICT is set but no CLIENT_SECRET is configured; "
                "refusing to sign mandates with the built-in placeholder key."
            )

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
            "ap2_version": AP2_VERSION,
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
        mandate_payload["signature"] = self.sign(mandate_payload)
        return mandate_payload

    def sign(self, mandate: dict[str, Any]) -> str:
        """Returns the HMAC-SHA256 signature over every field but ``signature``."""
        return hmac.new(
            self.secret_key.encode("utf-8"),
            canonical_bytes(mandate),
            hashlib.sha256,
        ).hexdigest()

    def verify_mandate(self, mandate: dict[str, Any]) -> bool:
        """Verifies a mandate's signature and expiry. Any tampered field fails."""
        if int(mandate.get("expires_at", 0) or 0) < int(time.time()):
            return False
        provided = str(mandate.get("signature", ""))
        if not provided:
            return False
        return hmac.compare_digest(self.sign(mandate), provided)

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


def canonical_bytes(mandate: dict[str, Any]) -> bytes:
    """Serializes a mandate to reproducible bytes, excluding its own signature."""
    payload = {k: v for k, v in mandate.items() if k != "signature"}
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _strict() -> bool:
    """Whether placeholder credentials are a hard error rather than a dev convenience."""
    return os.getenv("AC_PAYMENTS_STRICT", "") == "1"
