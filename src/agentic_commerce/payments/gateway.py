"""Provider resolution, error classification, and reconciliation.

The rule that shapes this module: **a provider is only swapped out for a failure that
happened before any byte was transmitted.** Configuration errors and unreachable hosts
qualify; a request that went out and was not answered does not. That second case is
resolved by asking the same provider whether the order exists
(:meth:`find_by_receipt`), never by asking a different provider to create another one.
The previous implementation retried across providers on any ``httpx.HTTPError`` and
additionally swallowed every MCP bridge failure, which meant one shopper action could
create two orders.

Idempotency is enforced by the ledger rather than trusted to callers: the attempt row
is written before the provider call, and a repeated key short-circuits to the stored
result.
"""

from __future__ import annotations

import logging
import uuid

from agentic_commerce.payments.ledger import PaymentLedger, get_ledger
from agentic_commerce.payments.models import (
    PaymentConfigurationError,
    PaymentError,
    PaymentResult,
    PaymentState,
    ProviderRejectedError,
    ProviderTransientError,
    ProviderUnknownError,
    validate_amount,
    validate_currency,
)
from agentic_commerce.payments.providers import build_provider
from agentic_commerce.payments.providers.base import classify_transport_error
from agentic_commerce.payments.providers.razorpay import RazorpayProvider
from agentic_commerce.payments.providers.stripe import StripeProvider
from agentic_commerce.payments.settings import PaymentSettings

logger = logging.getLogger(__name__)

#: Order providers are attempted in. Simulated is always last and always succeeds.
_FALLBACK_ORDER = ("razorpay", "stripe", "simulated")


def _short_reason(settings: PaymentSettings, exc: Exception) -> str:
    """One-line redacted provider failure for receipts and logs."""
    return settings.redact(str(exc))[:200]


def _candidates(settings: PaymentSettings) -> list[str]:
    """Providers worth trying, most preferred first, given the configuration."""
    start = _FALLBACK_ORDER.index(settings.provider)
    chain = [name for name in _FALLBACK_ORDER[start:]]
    if "stripe" in chain and not settings.stripe_secret_key:
        chain.remove("stripe")
    return chain


async def process_payment(
    amount_cents: int,
    currency: str = "USD",
    receipt: str | None = None,
    *,
    session_id: str = "",
    mandate_id: str = "",
    ledger: PaymentLedger | None = None,
) -> PaymentResult:
    """Authorizes ``amount_cents`` through the best usable provider.

    ``receipt`` doubles as the idempotency key. Repeating a call with the same receipt
    returns the recorded result without contacting any provider.

    Raises ``ValueError`` for an invalid amount or currency. Never raises for a
    provider failure: the chain degrades to the clearly-labelled simulated gateway, or
    returns a result in :attr:`PaymentState.UNKNOWN` when an outcome could not be
    established.
    """
    amount_cents = validate_amount(amount_cents)
    currency = validate_currency(currency)
    key = receipt or f"ac_{uuid.uuid4().hex[:12]}"
    book = ledger or get_ledger()
    settings = PaymentSettings.load()

    stored = book.stored_result(key)
    if stored is not None:
        if stored.state == PaymentState.FAILED.value and book.reopen_untransmitted(key):
            logger.info("payment %s: prior attempt never transmitted; retrying fresh", key)
        else:
            logger.info("payment %s replayed from ledger (%s)", key, stored.state)
            return stored

    attempt, created = book.open_attempt(
        key,
        amount_cents,
        currency,
        provider=settings.provider,
        session_id=session_id,
        mandate_id=mandate_id,
    )
    if not created and attempt["state"] == PaymentState.UNKNOWN.value:
        return await _reconcile(settings, key, amount_cents, currency, book)

    skipped: list[dict[str, str]] = []
    for name in _candidates(settings):
        provider = build_provider(name, settings)
        try:
            try:
                result = await provider.create_order(amount_cents, currency, key)
            except PaymentError:
                raise
            except Exception as exc:
                # A provider that lets a raw transport error escape still has to be
                # classified before it can influence a fallback decision.
                raise classify_transport_error(exc) from exc
        except (PaymentConfigurationError, ProviderTransientError) as exc:
            if name == "razorpay" and settings.mcp_bridge:
                # The bridge was explicitly opted into. Degrading past it silently is
                # what hid the broken bridge in the first place: fail where it broke.
                logger.error("payment %s: MCP bridge failed (%s)", key, exc)
                book.advance(key, PaymentState.FAILED)
                raise
            # Nothing was transmitted: this provider is unusable, the next may not be.
            logger.warning("payment %s: %s unusable (%s)", key, name, exc)
            skipped.append(
                {"provider": name, "outcome": f"unusable: {_short_reason(settings, exc)}"}
            )
            continue
        except ProviderRejectedError as exc:
            # The provider answered "no", so no order exists on its side.
            logger.warning("payment %s: %s refused (%s)", key, name, exc)
            skipped.append(
                {"provider": name, "outcome": f"refused: {_short_reason(settings, exc)}"}
            )
            continue
        except ProviderUnknownError as exc:
            logger.error("payment %s: %s outcome unknown (%s)", key, name, exc)
            return await _reconcile(settings, key, amount_cents, currency, book, name)

        result.idempotency_key = key
        result.mandate_id = mandate_id or None
        if skipped:
            # The receipt names every provider passed over, so a simulated success
            # with Razorpay configured reads as fallback — never as "no credentials".
            result.raw["providers_attempted"] = skipped
        book.advance(key, PaymentState.AUTHORIZED, result)
        return result

    # Unreachable while "simulated" terminates the chain, but the ledger must never be
    # left holding a PENDING row if that ever changes.
    failed = _unknown_result(settings, key, amount_cents, currency, "simulated")
    failed.state = PaymentState.FAILED.value
    book.advance(key, PaymentState.FAILED, failed)
    return failed


async def _reconcile(
    settings: PaymentSettings,
    key: str,
    amount_cents: int,
    currency: str,
    book: PaymentLedger,
    provider_name: str | None = None,
) -> PaymentResult:
    """Asks the provider whether an order for ``key`` already exists.

    This is the only correct response to a transmitted-but-unanswered request. If the
    order is found the attempt completes normally; if it is not, the attempt is parked
    in :attr:`PaymentState.UNKNOWN` for a webhook or a later lookup to resolve — it is
    never retried against another provider.
    """
    name = provider_name or settings.provider
    provider = build_provider(name, settings)
    try:
        found = await provider.find_by_receipt(key)
    except Exception as exc:  # noqa: BLE001 - reconciliation must not mask the unknown
        logger.error("payment %s: reconciliation against %s failed (%s)", key, name, exc)
        found = None

    if found is not None:
        found.idempotency_key = key
        book.advance(key, PaymentState.AUTHORIZED, found)
        logger.info("payment %s reconciled to %s", key, found.reference_id)
        return found

    unknown = _unknown_result(settings, key, amount_cents, currency, name)
    book.advance(key, PaymentState.UNKNOWN, unknown)
    return unknown


def _unknown_result(
    settings: PaymentSettings,
    key: str,
    amount_cents: int,
    currency: str,
    provider: str,
) -> PaymentResult:
    """A receipt that admits the outcome is not known, rather than guessing."""
    return PaymentResult(
        provider=provider,
        status="unknown",
        reference_id="",
        amount_cents=amount_cents,
        currency=currency,
        live=settings.derive_live(),
        raw={
            "receipt": key,
            "note": (
                "provider did not answer; awaiting reconciliation. Resolve via "
                f"GET /api/payments/{key} or the provider webhook; to pay, mint a "
                "fresh mandate — the presented mandate stays single-use."
            ),
        },
        state=PaymentState.UNKNOWN.value,
        idempotency_key=key,
    )


async def _charge_razorpay(
    amount_cents: int, currency: str, receipt: str
) -> PaymentResult:
    """Compatibility wrapper: creates one Razorpay order, guard enforced."""
    return await RazorpayProvider().create_order(amount_cents, currency, receipt)


async def _charge_stripe(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Compatibility wrapper: creates one Stripe PaymentIntent, guard enforced."""
    return await StripeProvider().create_order(amount_cents, currency, receipt)


def _simulate(amount_cents: int, currency: str, receipt: str) -> PaymentResult:
    """Compatibility wrapper: a deterministic offline authorization."""
    return PaymentResult(
        provider="simulated",
        status="authorized",
        reference_id=f"sim_{uuid.uuid4().hex[:16]}",
        amount_cents=amount_cents,
        currency=currency,
        live=False,
        raw={"receipt": receipt, "note": "test-mode authorization recorded locally"},
        idempotency_key=receipt,
    )
