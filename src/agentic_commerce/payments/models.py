"""Payment domain models shared by Razorpay, Stripe, and the simulated gateway."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Currencies Razorpay bills in the "paise-like" 1/100 minor unit we already use.
_MINOR_UNIT_DIVISOR = 100


class PaymentConfigurationError(RuntimeError):
    """Raised when configured credentials are unusable or not test-mode."""


class PaymentSignatureError(RuntimeError):
    """Raised when a Razorpay payment or webhook signature does not verify."""


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
