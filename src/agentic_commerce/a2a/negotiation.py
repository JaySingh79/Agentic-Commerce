"""Drives a bounded A2A negotiation between a buyer and a merchant agent.

The loop is deliberately bounded by ``max_rounds``: two agents that both concede
fractionally will converge asymptotically and never meet exactly, so an
unbounded loop would spin. On exhaustion the negotiation is reported as
unsuccessful rather than silently returning a half-agreed price.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentic_commerce.a2a.buyer_agent import BuyerAgent
from agentic_commerce.a2a.merchant_agent import MerchantAgent
from agentic_commerce.a2a.protocol import TERMINAL, A2AMessage, Performative
from agentic_commerce.core.telemetry import trace_tool_execution


@dataclass
class NegotiationResult:
    """Outcome of a negotiation plus the full auditable transcript."""

    agreed: bool
    final_price_cents: int | None
    rounds: int
    transcript: list[A2AMessage] = field(default_factory=list)
    reason: str = ""

    @property
    def savings_cents(self) -> int:
        """How much the agreed price beat the merchant's opening quote."""
        opening = next(
            (m.price_cents for m in self.transcript if m.performative is Performative.OFFER),
            None,
        )
        if opening is None or self.final_price_cents is None:
            return 0
        return max(0, opening - self.final_price_cents)

    def as_dict(self) -> dict[str, object]:
        """Serializable summary for session state and the UI."""
        return {
            "agreed": self.agreed,
            "final_price_cents": self.final_price_cents,
            "final_price_display": (
                f"${self.final_price_cents / 100:.2f}"
                if self.final_price_cents is not None
                else None
            ),
            "rounds": self.rounds,
            "savings_cents": self.savings_cents,
            "savings_display": f"${self.savings_cents / 100:.2f}",
            "reason": self.reason,
            "transcript": [m.as_dict() for m in self.transcript],
        }


def negotiate(
    buyer: BuyerAgent,
    merchant: MerchantAgent,
    item: str,
    quantity: int = 1,
    max_rounds: int = 6,
) -> NegotiationResult:
    """Runs RFQ -> OFFER -> COUNTER... until ACCEPT/REJECT or ``max_rounds``."""
    with trace_tool_execution(
        "a2a_negotiate",
        {"item": item, "quantity": quantity, "merchant": merchant.name},
    ):
        rfq = buyer.open_rfq(merchant.name, item, quantity)
        transcript: list[A2AMessage] = [rfq]

        merchant_msg = merchant.respond(rfq)
        transcript.append(merchant_msg)

        buyer_last: int | None = None
        merchant_quote = merchant_msg.price_cents

        for round_no in range(1, max_rounds + 1):
            buyer_msg = buyer.respond(merchant_msg, last_offer_cents=buyer_last)
            transcript.append(buyer_msg)
            if buyer_msg.price_cents is not None:
                buyer_last = buyer_msg.price_cents

            if buyer_msg.performative in TERMINAL:
                agreed = buyer_msg.performative is Performative.ACCEPT
                return NegotiationResult(
                    agreed=agreed,
                    final_price_cents=buyer_msg.price_cents if agreed else None,
                    rounds=round_no,
                    transcript=transcript,
                    reason=buyer_msg.reason,
                )

            merchant_msg = merchant.respond(buyer_msg, current_quote_cents=merchant_quote)
            transcript.append(merchant_msg)
            if merchant_msg.price_cents is not None:
                merchant_quote = merchant_msg.price_cents

            if merchant_msg.performative in TERMINAL:
                agreed = merchant_msg.performative is Performative.ACCEPT
                return NegotiationResult(
                    agreed=agreed,
                    final_price_cents=merchant_msg.price_cents if agreed else None,
                    rounds=round_no,
                    transcript=transcript,
                    reason=merchant_msg.reason,
                )

        return NegotiationResult(
            agreed=False,
            final_price_cents=None,
            rounds=max_rounds,
            transcript=transcript,
            reason=f"no agreement within {max_rounds} rounds",
        )
