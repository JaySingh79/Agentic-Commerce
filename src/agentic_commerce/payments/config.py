"""Connection config for payments: env reads, test-key guard, provider selection."""

from __future__ import annotations

import os

from agentic_commerce.payments.models import PaymentConfigurationError

#: Network timeout (seconds) for provider API calls.
TIMEOUT = 20.0


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
