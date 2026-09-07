"""The single place payment credentials are read, validated, and judged live-or-not.

Everything downstream asks :class:`PaymentSettings` rather than reading ``os.environ``,
so the live-key guard exists exactly once and ``PaymentResult.live`` is *derived* from
the credential mode instead of being written as a literal by each provider.

The guard is deliberately unconditional: a production key is refused whatever the
configuration says. Enabling real charges is a code change plus a credential change,
never a stray environment variable.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum

from agentic_commerce.payments.models import PaymentConfigurationError

#: Network timeout (seconds) for provider API calls.
TIMEOUT = 20.0

#: Hard cap on a whole MCP bridge exchange (spawn + handshake + tool call).
BRIDGE_TIMEOUT = 60.0

_RAZORPAY_TEST_PREFIX = "rzp_test_"
_STRIPE_TEST_PREFIX = "sk_test_"

#: Patterns redacted from any provider text before it reaches a log or an exception.
_SECRET_PATTERNS = (
    re.compile(r"\b(rzp_(?:test|live)_[A-Za-z0-9]+)"),
    re.compile(r"\b((?:sk|pk|whsec)_(?:test_|live_)?[A-Za-z0-9]+)"),
)


class CredentialMode(StrEnum):
    """How real the configured credentials are."""

    TEST = "test"
    SIMULATED = "simulated"


def redact(text: str, *extra_secrets: str) -> str:
    """Removes API keys and any explicitly named secret from provider text."""
    cleaned = text
    for secret in extra_secrets:
        if secret:
            cleaned = cleaned.replace(secret, "***")
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("***", cleaned)
    return cleaned


def _require_test_key(key: str, prefix: str, provider: str) -> None:
    """Guards against a demo agent transacting on production credentials."""
    if not key.startswith(prefix):
        raise PaymentConfigurationError(
            f"{provider} key does not look like a test key (expected prefix "
            f"{prefix!r}). Refusing to process a live payment from the agent."
        )


@dataclass(frozen=True)
class PaymentSettings:
    """An immutable snapshot of payment configuration for one operation.

    Loaded per call rather than cached at import: tests and the running app both
    change the environment, and a payment is rare enough that reading a handful of
    variables costs nothing next to a network round trip.
    """

    provider: str
    razorpay_key_id: str
    razorpay_key_secret: str
    stripe_secret_key: str
    razorpay_webhook_secret: str
    mcp_bridge: bool
    mcp_container: str
    mcp_toolsets: str
    strict: bool
    timeout: float = TIMEOUT
    bridge_timeout: float = BRIDGE_TIMEOUT

    @classmethod
    def load(cls) -> PaymentSettings:
        """Reads the current environment. Never raises; validation is explicit."""
        return cls(
            provider=_select_provider(),
            razorpay_key_id=os.getenv("RAZORPAY_KEY_ID", ""),
            razorpay_key_secret=os.getenv("RAZORPAY_KEY_SECRET", ""),
            stripe_secret_key=(
                os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY") or ""
            ),
            razorpay_webhook_secret=os.getenv("RAZORPAY_WEBHOOK_SECRET", ""),
            mcp_bridge=os.getenv("RAZORPAY_MCP_BRIDGE", "") == "1",
            mcp_container=os.getenv("RAZORPAY_MCP_CONTAINER", "") or "razorpay-mcp",
            mcp_toolsets=os.getenv("RAZORPAY_MCP_TOOLSETS", "") or "orders,payments",
            strict=os.getenv("AC_PAYMENTS_STRICT", "") == "1",
        )

    def require_razorpay(self) -> None:
        """Enforces the live-key guard for Razorpay. Raises pre-flight, or not at all."""
        _require_test_key(self.razorpay_key_id, _RAZORPAY_TEST_PREFIX, "Razorpay")
        if not self.razorpay_key_secret:
            raise PaymentConfigurationError("Razorpay key secret is not configured.")

    def require_stripe(self) -> None:
        """Enforces the live-key guard for Stripe. Raises pre-flight, or not at all."""
        _require_test_key(self.stripe_secret_key, _STRIPE_TEST_PREFIX, "Stripe")

    @property
    def mode(self) -> CredentialMode:
        """Test credentials or none at all — never ``live`` while the guard stands."""
        if self.provider == "razorpay" and self.razorpay_key_id.startswith(
            _RAZORPAY_TEST_PREFIX
        ):
            return CredentialMode.TEST
        if self.provider == "stripe" and self.stripe_secret_key.startswith(
            _STRIPE_TEST_PREFIX
        ):
            return CredentialMode.TEST
        return CredentialMode.SIMULATED

    def derive_live(self, provider_says_live: bool | None = None) -> bool:
        """Computes ``PaymentResult.live`` from the credential mode and the response.

        A provider claiming ``livemode`` while our credentials are test-mode is a
        contradiction we resolve loudly rather than by trusting either side.
        """
        credentials_are_live = self.mode not in (
            CredentialMode.TEST,
            CredentialMode.SIMULATED,
        )
        if provider_says_live and not credentials_are_live:
            raise PaymentConfigurationError(
                "Provider reported a live transaction under test credentials; "
                "refusing to report this receipt as test-mode."
            )
        return bool(credentials_are_live)

    def redact(self, text: str) -> str:
        """Redacts this configuration's secrets out of arbitrary provider text."""
        return redact(text, self.razorpay_key_secret, self.stripe_secret_key)


def _select_provider() -> str:
    """Returns the provider the current configuration selects."""
    if os.getenv("RAZORPAY_KEY_ID") and os.getenv("RAZORPAY_KEY_SECRET"):
        return "razorpay"
    if os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY"):
        return "stripe"
    return "simulated"


def select_provider() -> str:
    """Returns the provider that current configuration selects."""
    return _select_provider()
