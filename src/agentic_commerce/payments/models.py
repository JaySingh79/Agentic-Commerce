"""Payment domain models shared by Razorpay, Stripe, and the simulated gateway.

The error taxonomy is load-bearing, not decoration: the gateway decides whether a
failure may be retried on another provider purely from the exception class, so a
provider that has already transmitted a request must raise
:class:`ProviderUnknownError` (reconcile) rather than :class:`ProviderTransientError`
(safe to fall back).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

#: Currencies Razorpay bills in the "paise-like" 1/100 minor unit we already use.
_MINOR_UNIT_DIVISOR = 100

#: Currencies the payment layer accepts. Anything else is refused at the boundary
#: rather than forwarded to a provider that would interpret the minor unit itself.
ALLOWED_CURRENCIES = frozenset({"USD", "INR", "EUR", "GBP", "AUD", "CAD", "SGD"})


class PaymentError(RuntimeError):
    """Base class for every payment failure raised by this package."""


class PaymentConfigurationError(PaymentError):
    """Raised when configured credentials are unusable or not test-mode.

    Pre-flight only: nothing has been sent to a provider, so a caller may safely
    try a different provider.
    """


class PaymentSignatureError(PaymentError):
    """Raised when a Razorpay payment or webhook signature does not verify."""


class ProviderTransientError(PaymentError):
    """Provider was unreachable; the request was never transmitted.

    Safe to fall back to another provider — no order can exist on the far side.
    """


class ProviderRejectedError(PaymentError):
    """Provider received the request and refused it (a 4xx/5xx answer).

    The outcome is known and negative, so falling back would only duplicate work.
    """


class ProviderUnknownError(PaymentError):
    """The request was transmitted but no answer arrived.

    An order may or may not exist. The only correct next step is reconciliation
    by idempotency key — never a retry against a different provider.
    """


class PaymentState(StrEnum):
    """States a payment attempt can occupy in the ledger and the flow machine."""

    PENDING = "pending"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    SETTLED = "settled"
    FAILED = "failed"
    EXPIRED = "expired"
    #: Transmitted, outcome not yet known. Resolved by reconciliation, never by retry.
    UNKNOWN = "unknown"


#: States from which no further transition is legal.
TERMINAL_STATES = frozenset(
    {PaymentState.SETTLED, PaymentState.FAILED, PaymentState.EXPIRED}
)

#: Legal state transitions. An attempt to move outside this table raises.
ALLOWED_TRANSITIONS: dict[PaymentState, frozenset[PaymentState]] = {
    PaymentState.PENDING: frozenset(
        {
            PaymentState.AUTHORIZED,
            PaymentState.FAILED,
            PaymentState.EXPIRED,
            PaymentState.UNKNOWN,
        }
    ),
    PaymentState.AUTHORIZED: frozenset(
        {PaymentState.CAPTURED, PaymentState.FAILED, PaymentState.EXPIRED}
    ),
    PaymentState.CAPTURED: frozenset({PaymentState.SETTLED, PaymentState.FAILED}),
    PaymentState.UNKNOWN: frozenset(
        {PaymentState.AUTHORIZED, PaymentState.FAILED, PaymentState.EXPIRED}
    ),
    PaymentState.SETTLED: frozenset(),
    PaymentState.FAILED: frozenset(),
    PaymentState.EXPIRED: frozenset(),
}


class IllegalTransitionError(PaymentError):
    """Raised when a payment is asked to move between states that do not connect."""


def assert_transition(current: PaymentState, target: PaymentState) -> None:
    """Raises :class:`IllegalTransitionError` unless ``current -> target`` is legal."""
    if target == current:
        return
    if target not in ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise IllegalTransitionError(
            f"Illegal payment transition {current.value!r} -> {target.value!r}."
        )


def validate_amount(amount_cents: Any) -> int:
    """Returns a validated integer minor-unit amount, or raises ``ValueError``.

    Floats are refused rather than coerced: money never becomes a float in this
    package, so a float here means the caller's arithmetic is already suspect.
    """
    if isinstance(amount_cents, bool) or not isinstance(amount_cents, int):
        raise ValueError(
            f"amount_cents must be an int in minor units, got {type(amount_cents).__name__}"
        )
    if amount_cents <= 0:
        raise ValueError(f"amount_cents must be positive, got {amount_cents}")
    return amount_cents


def validate_currency(currency: str) -> str:
    """Returns the upper-cased currency if supported, else raises ``ValueError``."""
    code = str(currency).upper()
    if code not in ALLOWED_CURRENCIES:
        raise ValueError(
            f"unsupported currency {code!r}; allowed: {', '.join(sorted(ALLOWED_CURRENCIES))}"
        )
    return code


@dataclass
class PaymentResult:
    """Normalized outcome across Razorpay, Stripe, and the simulated gateway."""

    provider: str
    status: str
    reference_id: str
    amount_cents: int
    currency: str
    live: bool
    receipt_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)
    #: Ledger state for this attempt. Additive: older clients ignore it.
    state: str = PaymentState.AUTHORIZED.value
    #: The key that makes a retry of this attempt a no-op.
    idempotency_key: str | None = None
    #: The AP2 mandate that authorized this attempt, when one did.
    mandate_id: str | None = None

    @property
    def amount_display(self) -> str:
        """Human-readable amount in major units."""
        return f"{self.amount_cents / _MINOR_UNIT_DIVISOR:.2f} {self.currency}"

    def as_dict(self) -> dict[str, Any]:
        """Serializable summary for session state and the UI."""
        return {
            "provider": self.provider,
            "status": self.status,
            "reference_id": self.reference_id,
            "amount_cents": self.amount_cents,
            "amount_display": self.amount_display,
            "currency": self.currency,
            "live": self.live,
            "receipt_url": self.receipt_url,
            "state": self.state,
            "idempotency_key": self.idempotency_key,
            "mandate_id": self.mandate_id,
        }

    def format_display(self) -> str:
        """Markdown block describing the payment for the chat surface."""
        mode = "LIVE" if self.live else "TEST / SIMULATED"
        lines = [
            f"💳 **Payment {self.status.upper()}** — `{self.provider}` ({mode})",
            f"- **Amount:** {self.amount_display}",
            f"- **Reference:** `{self.reference_id}`",
        ]
        if self.receipt_url:
            lines.append(f"- **Receipt:** [View]({self.receipt_url})")
        if not self.live:
            lines.append("- *No real money moved; this is a test-mode authorization.*")
        return "\n".join(lines)
