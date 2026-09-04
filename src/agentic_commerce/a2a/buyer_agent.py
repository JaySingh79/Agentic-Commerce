"""Buyer agent: opens low, concedes toward a hard budget ceiling.

Mirror image of :class:`~agentic_commerce.a2a.merchant_agent.MerchantAgent`. The
buyer's hard invariant is :attr:`max_price_cents` — the shopper's stated budget.
The agent never accepts or offers above it, which is what makes an autonomous
negotiation safe to run on a real shopper's behalf.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_commerce.a2a.protocol import A2AMessage, Performative, new_conversation_id


@dataclass
class BuyerAgent:
    """Negotiates on the shopper's behalf under a firm budget ceiling."""

    name: str
    max_price_cents: int
    target_price_cents: int
    concession_rate: float = 0.4

    def __post_init__(self) -> None:
        if self.target_price_cents > self.max_price_cents:
            raise ValueError(
                f"target_price_cents ({self.target_price_cents}) cannot exceed "
                f"max_price_cents ({self.max_price_cents})"
            )

    def open_rfq(self, merchant_name: str, item: str, quantity: int = 1) -> A2AMessage:
        """Starts a negotiation with a request for quote."""
        return A2AMessage(
            performative=Performative.RFQ,
            sender=self.name,
            recipient=merchant_name,
            conversation_id=new_conversation_id(),
            reason=f"requesting quote for {quantity}x {item}",
            payload={"item": item, "quantity": quantity},
        )

    def _concede_from(self, current_cents: int, merchant_price_cents: int) -> int:
        """Moves part-way toward the merchant, clamped at the budget ceiling."""
        if merchant_price_cents <= current_cents:
            return current_cents
        gap = merchant_price_cents - current_cents
        conceded = current_cents + round(gap * self.concession_rate)
        return min(conceded, self.max_price_cents)

    def respond(self, message: A2AMessage, last_offer_cents: int | None = None) -> A2AMessage:
        """Replies to a merchant OFFER or COUNTER."""
        if message.performative not in {Performative.OFFER, Performative.COUNTER}:
            return message.reply(
                Performative.REJECT,
                sender=self.name,
                reason=f"unsupported performative {message.performative.value}",
            )

        merchant_price = message.price_cents
        if merchant_price is None:
            return message.reply(
                Performative.REJECT, sender=self.name, reason="offer carried no price"
            )

        # Good enough - close immediately.
        if merchant_price <= self.target_price_cents:
            return message.reply(
                Performative.ACCEPT,
                sender=self.name,
                price_cents=merchant_price,
                reason="at or below target price",
            )

        # Within budget but above target: concede toward it.
        my_last = last_offer_cents if last_offer_cents is not None else self.target_price_cents
        proposed = self._concede_from(my_last, merchant_price)

        if merchant_price <= self.max_price_cents and proposed >= merchant_price:
            return message.reply(
                Performative.ACCEPT,
                sender=self.name,
                price_cents=merchant_price,
                reason="within budget after concessions",
            )

        if proposed >= self.max_price_cents and merchant_price > self.max_price_cents:
            return message.reply(
                Performative.REJECT,
                sender=self.name,
                price_cents=self.max_price_cents,
                reason="merchant price exceeds budget",
            )

        return message.reply(
            Performative.COUNTER,
            sender=self.name,
            price_cents=proposed,
            reason="counter-offer",
        )
