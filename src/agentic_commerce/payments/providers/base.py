"""The provider contract and the transport-error classification every provider shares.

Classification is the safety-critical part. ``httpx`` reports "could not connect" and
"connected, sent, then heard nothing" through sibling exception types, but the two mean
opposite things for money: the first cannot have created an order, the second may have.
:func:`classify_transport_error` is where that distinction is made once, so no provider
can accidentally invite a duplicate charge by re-raising the wrong class.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import httpx

from agentic_commerce.payments.models import (
    PaymentResult,
    ProviderRejectedError,
    ProviderTransientError,
    ProviderUnknownError,
)
from agentic_commerce.payments.settings import PaymentSettings

#: Errors raised before any byte reached the provider — safe to try elsewhere.
_NEVER_SENT = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ProxyError,
    httpx.UnsupportedProtocol,
    httpx.InvalidURL,
)


def classify_transport_error(exc: Exception) -> Exception:
    """Maps an ``httpx`` failure onto the payment error taxonomy."""
    if isinstance(exc, _NEVER_SENT):
        return ProviderTransientError(f"provider unreachable: {type(exc).__name__}")
    if isinstance(exc, httpx.HTTPError):
        return ProviderUnknownError(
            f"request was transmitted but the outcome is unknown: {type(exc).__name__}"
        )
    return exc


def raise_for_rejection(
    settings: PaymentSettings, provider: str, status_code: int, body: str
) -> None:
    """Raises for an error response, with secrets redacted.

    A 4xx is a refusal: the provider understood the request and declined it, so no
    order exists and another provider may be tried. A 5xx is not — the request was
    accepted for processing and may have created an order, so it becomes an
    :class:`ProviderUnknownError` that only reconciliation can resolve.
    """
    detail = f"{provider} error {status_code}: {settings.redact(body)[:300]}"
    if status_code >= 500:
        raise ProviderUnknownError(detail)
    raise ProviderRejectedError(detail)


@runtime_checkable
class PaymentProvider(Protocol):
    """What the gateway needs from any payment backend.

    ``create_order`` authorizes an amount; it never captures. ``find_by_receipt`` is
    what makes an :class:`~agentic_commerce.payments.models.ProviderUnknownError`
    recoverable — it answers "did my request actually land?" without sending another.
    """

    name: str

    async def create_order(
        self, amount_cents: int, currency: str, receipt: str
    ) -> PaymentResult:
        """Creates an authorization for ``amount_cents``, keyed by ``receipt``."""
        ...

    async def fetch_order(self, reference_id: str) -> PaymentResult | None:
        """Returns the provider's current view of an order, or ``None``."""
        ...

    async def find_by_receipt(self, receipt: str) -> PaymentResult | None:
        """Finds an existing order by idempotency key, for reconciliation."""
        ...


def result_from(
    provider: str,
    settings: PaymentSettings,
    data: dict[str, Any],
    *,
    status: str,
    reference_id: str,
    amount_cents: int,
    currency: str,
    receipt: str,
    provider_says_live: bool | None = None,
) -> PaymentResult:
    """Builds a :class:`PaymentResult` with ``live`` derived, never asserted."""
    return PaymentResult(
        provider=provider,
        status=status,
        reference_id=reference_id,
        amount_cents=amount_cents,
        currency=currency,
        live=settings.derive_live(provider_says_live),
        raw=data,
        idempotency_key=receipt,
    )
