"""A2A tests: dialogue shape, margin-floor protection, and budget protection.

The two invariants that make autonomous negotiation safe to run unattended:
the merchant never quotes below its margin floor, and the buyer never commits
above the shopper's budget. Both are asserted directly.
"""

import pytest

from agentic_commerce.a2a.buyer_agent import BuyerAgent
from agentic_commerce.a2a.merchant_agent import TIER_DISCOUNTS, MerchantAgent
from agentic_commerce.a2a.negotiation import negotiate
from agentic_commerce.a2a.protocol import Performative, format_transcript


def test_rfq_is_answered_with_an_offer():
    buyer = BuyerAgent("buyer", max_price_cents=15000, target_price_cents=10000)
    merchant = MerchantAgent("merchant", list_price_cents=14000, floor_cents=9000)

    rfq = buyer.open_rfq("merchant", "running shoes")
    reply = merchant.respond(rfq)

    assert rfq.performative is Performative.RFQ
    assert reply.performative is Performative.OFFER
    assert reply.price_cents == 14000
    assert reply.conversation_id == rfq.conversation_id
    assert reply.recipient == "buyer"


def test_buyer_accepts_immediately_at_or_below_target():
    buyer = BuyerAgent("buyer", max_price_cents=15000, target_price_cents=12000)
    merchant = MerchantAgent("merchant", list_price_cents=11000, floor_cents=8000)

    result = negotiate(buyer, merchant, "hoodie")

    assert result.agreed is True
    assert result.final_price_cents == 11000
    assert result.rounds == 1


def test_merchant_never_quotes_below_its_margin_floor():
    """Core invariant: an aggressive buyer must not push price under the floor."""
    floor = 9000
    buyer = BuyerAgent("buyer", max_price_cents=20000, target_price_cents=1000)
    merchant = MerchantAgent("merchant", list_price_cents=15000, floor_cents=floor)

    result = negotiate(buyer, merchant, "jacket", max_rounds=25)

    quoted = [m.price_cents for m in result.transcript if m.sender == "merchant"]
    assert quoted, "merchant should have quoted at least once"
    assert min(quoted) >= floor
    if result.agreed:
        assert result.final_price_cents >= floor


def test_buyer_never_commits_above_budget():
    """Core invariant: budget ceiling holds even when the merchant won't move."""
    budget = 10000
    buyer = BuyerAgent("buyer", max_price_cents=budget, target_price_cents=8000)
    # Floor sits above the buyer's budget, so no deal is possible.
    merchant = MerchantAgent("merchant", list_price_cents=30000, floor_cents=25000)

    result = negotiate(buyer, merchant, "watch", max_rounds=25)

    assert result.agreed is False
    buyer_prices = [
        m.price_cents for m in result.transcript if m.sender == "buyer" and m.price_cents
    ]
    assert all(p <= budget for p in buyer_prices)


def test_negotiation_converges_and_saves_money():
    buyer = BuyerAgent("buyer", max_price_cents=14000, target_price_cents=10000)
    merchant = MerchantAgent("merchant", list_price_cents=13000, floor_cents=9500)

    result = negotiate(buyer, merchant, "sneakers")

    assert result.agreed is True
    assert 9500 <= result.final_price_cents <= 14000
    assert result.savings_cents > 0, "should beat the opening quote"


def test_bounded_rounds_prevent_an_infinite_loop():
    """Fractional concessions converge asymptotically; the loop must still stop."""
    buyer = BuyerAgent("buyer", max_price_cents=10000, target_price_cents=1, concession_rate=0.0)
    merchant = MerchantAgent(
        "merchant", list_price_cents=90000, floor_cents=80000, concession_rate=0.0
    )

    result = negotiate(buyer, merchant, "tv", max_rounds=4)

    assert result.agreed is False
    assert result.rounds <= 4


def test_loyalty_tier_discounts_the_opening_quote():
    base = MerchantAgent("m", list_price_cents=10000, floor_cents=5000, tier="standard")
    gold = MerchantAgent("m", list_price_cents=10000, floor_cents=5000, tier="gold")

    assert base.opening_price_cents() == 10000
    assert gold.opening_price_cents() == round(10000 * (1 - TIER_DISCOUNTS["gold"]))
    assert gold.opening_price_cents() < base.opening_price_cents()


def test_tier_discount_still_cannot_break_the_floor():
    merchant = MerchantAgent("m", list_price_cents=10000, floor_cents=9800, tier="platinum")
    assert merchant.opening_price_cents() >= 9800


def test_invalid_configurations_are_rejected_at_construction():
    with pytest.raises(ValueError):
        MerchantAgent("m", list_price_cents=5000, floor_cents=9000)
    with pytest.raises(ValueError):
        MerchantAgent("m", list_price_cents=9000, floor_cents=5000, tier="unobtanium")
    with pytest.raises(ValueError):
        BuyerAgent("b", max_price_cents=5000, target_price_cents=9000)


def test_transcript_is_ordered_and_renderable():
    buyer = BuyerAgent("buyer", max_price_cents=14000, target_price_cents=10000)
    merchant = MerchantAgent("merchant", list_price_cents=13000, floor_cents=9500)

    result = negotiate(buyer, merchant, "sneakers")
    convo_ids = {m.conversation_id for m in result.transcript}

    assert len(convo_ids) == 1, "all messages belong to one conversation"
    assert result.transcript[0].performative is Performative.RFQ
    rendered = format_transcript(result.transcript)
    assert "RFQ" in rendered and "A2A negotiation transcript" in rendered
