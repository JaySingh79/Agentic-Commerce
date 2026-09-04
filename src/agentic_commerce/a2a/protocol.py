"""A2A negotiation protocol: message envelope and performatives.

Agent-to-Agent messaging for price negotiation between a buyer-side agent and a
merchant-side sales agent. Every exchange is an immutable :class:`A2AMessage`
appended to a transcript, which gives the UI (and OpenTelemetry) a complete,
auditable record of how a price was reached.

Performatives follow the classic contract-net shape:

    RFQ -> OFFER -> COUNTER -> (COUNTER ...) -> ACCEPT | REJECT

Money is handled exclusively in integer minor units (cents) to avoid float
rounding drift across negotiation rounds.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Performative(StrEnum):
    """The speech act a negotiation message performs."""

    RFQ = "RFQ"
    OFFER = "OFFER"
    COUNTER = "COUNTER"
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


#: Performatives that terminate a negotiation.
TERMINAL = frozenset({Performative.ACCEPT, Performative.REJECT})


@dataclass(frozen=True)
class A2AMessage:
    """One immutable message in an A2A negotiation transcript."""

    performative: Performative
    sender: str
    recipient: str
    conversation_id: str
    price_cents: int | None = None
    currency: str = "USD"
    reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    message_id: str = field(default_factory=lambda: f"a2a_{uuid.uuid4().hex[:12]}")
    timestamp: float = field(default_factory=time.time)

    @property
    def price_display(self) -> str:
        """Human-readable price, or '' when the message carries no price."""
        if self.price_cents is None:
            return ""
        return f"${self.price_cents / 100:.2f}"

    def as_dict(self) -> dict[str, Any]:
        """Serializable form for session state, telemetry, and the UI."""
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "performative": self.performative.value,
            "sender": self.sender,
            "recipient": self.recipient,
            "price_cents": self.price_cents,
            "price_display": self.price_display,
            "currency": self.currency,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }

    def reply(
        self,
        performative: Performative,
        sender: str,
        price_cents: int | None = None,
        reason: str = "",
        **payload: Any,
    ) -> A2AMessage:
        """Builds a response in the same conversation, addressed back to the sender."""
        return A2AMessage(
            performative=performative,
            sender=sender,
            recipient=self.sender,
            conversation_id=self.conversation_id,
            price_cents=price_cents,
            currency=self.currency,
            reason=reason,
            payload=payload,
        )


def new_conversation_id() -> str:
    """Fresh id correlating every message in one negotiation."""
    return f"conv_{uuid.uuid4().hex[:12]}"


def format_transcript(transcript: list[A2AMessage]) -> str:
    """Renders a negotiation transcript as Markdown for the chat surface."""
    if not transcript:
        return "_No negotiation took place._"
    lines = ["**A2A negotiation transcript**", ""]
    for msg in transcript:
        price = f" — {msg.price_display}" if msg.price_cents is not None else ""
        reason = f" _({msg.reason})_" if msg.reason else ""
        lines.append(
            f"- `{msg.performative.value}` **{msg.sender} → {msg.recipient}**{price}{reason}"
        )
    return "\n".join(lines)
