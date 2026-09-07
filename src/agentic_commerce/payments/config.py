"""Compatibility shim. Payment configuration now lives in ``payments.settings``.

Kept so existing imports (``from agentic_commerce.payments.config import TIMEOUT``)
and tests keep resolving; :class:`~agentic_commerce.payments.settings.PaymentSettings`
is the single source of truth.
"""

from __future__ import annotations

from agentic_commerce.payments.settings import (
    TIMEOUT,
    PaymentSettings,
    _require_test_key,
    select_provider,
)

__all__ = ["TIMEOUT", "PaymentSettings", "_require_test_key", "select_provider"]
