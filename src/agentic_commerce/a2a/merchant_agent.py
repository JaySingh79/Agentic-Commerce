"""Merchant sales agent: concedes toward a margin floor, never below it.

The floor is the merchant's walk-away price (unit cost plus minimum margin). It
is the agent's single hard invariant: no OFFER or COUNTER may ever quote below
it, regardless of how aggressively the buyer pushes. Everything else — opening
quote, concession size, loyalty tier discount — is negotiable policy.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentic_commerce.a2a.protocol import A2AMessage, Performative

#: Extra discount the merchant is willing to extend per loyalty tier.
TIER_DISCOUNTS: dict[str, float] = {
    "standard": 0.00,
    "silver": 0.03,
    "gold": 0.06,
    "platinum": 0.10,
}


@dataclass
class MerchantAgent:
    """Sells one item, conceding gradually but never under :attr:`floor_cents`."""

    name: str
    list_price_cents: int
    floor_cents: int
    tier: str = "standard"
    concession_rate: float = 0.35

    def __post_init__(self) -> None:
        if self.floor_cents > self.list_price_cents:
            raise ValueError(
                f"floor_cents ({self.floor_cents}) cannot exceed "
                f"list_price_cents ({self.list_price_cents})"
            )
        if self.tier not in TIER_DISCOUNTS:
            raise ValueError(f"unknown loyalty tier: {self.tier!r}")

    @property
    def effective_floor_cents(self) -> int:
        """The floor is absolute: tier discounts never erode it."""
        return self.floor_cents

    def opening_price_cents(self) -> int:
        """List price minus the buyer's loyalty tier discount."""
        discounted = round(self.list_price_cents * (1 - TIER_DISCOUNTS[self.tier]))
        return max(discounted, self.effective_floor_cents)

    def _concede_from(self, current_cents: int, buyer_offer_cents: int) -> int:
        """Moves part-way toward the buyer, clamped at the floor."""
        if buyer_offer_cents >= current_cents:
            return current_cents
        gap = current_cents - buyer_offer_cents
        conceded = current_cents - round(gap * self.concession_rate)
        return max(conceded, self.effective_floor_cents)

    def respond(self, message: A2AMessage, current_quote_cents: int | None = None) -> A2AMessage:
        """Produces the merchant's reply to an RFQ or a buyer COUNTER."""
        if message.performative is Performative.RFQ:
            price = self.opening_price_cents()
            return message.reply(
                Performative.OFFER,
                sender=self.name,
                price_cents=price,
                reason=(
                    f"{self.tier} tier quote"
                    if TIER_DISCOUNTS[self.tier]
                    else "list price"
                ),
            )

        if message.performative is Performative.COUNTER:
            buyer_price = message.price_cents or 0
            quote = current_quote_cents or self.opening_price_cents()

            # Buyer already meets or beats our quote - take it.
            if buyer_price >= quote:
                return message.reply(
                    Performative.ACCEPT,
                    sender=self.name,
                    price_cents=quote,
                    reason="buyer met the quote",
                )

            # Accept anything at or above the floor rather than lose the sale.
            if buyer_price >= self.effective_floor_cents:
                return message.reply(
                    Performative.ACCEPT,
                    sender=self.name,
                    price_cents=buyer_price,
                    reason="above margin floor",
                )

            conceded = self._concede_from(quote, buyer_price)
            if conceded <= self.effective_floor_cents:
                return message.reply(
                    Performative.COUNTER,
                    sender=self.name,
                    price_cents=self.effective_floor_cents,
                    reason="final price, at margin floor",
                )
            return message.reply(
                Performative.COUNTER,
                sender=self.name,
                price_cents=conceded,
                reason="partial concession",
            )

        return message.reply(
            Performative.REJECT,
            sender=self.name,
            reason=f"unsupported performative {message.performative.value}",
        )
