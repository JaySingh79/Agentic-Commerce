"""Phase 2/4 tests: cart totals normalization, checkout status + referral handoff."""

from agentic_commerce.core.models import CheckoutStatus
from agentic_commerce.ucp.cart import _to_cart, estimate_total_cents
from agentic_commerce.ucp.checkout import _to_checkout


def test_cart_normalizes_line_items_and_totals():
    raw = {
        "id": "gid://shopify/Cart/1",
        "currency": "USD",
        "line_items": [{"quantity": 2, "item": {"id": "gid://shopify/ProductVariant/9"}}],
        "totals": [{"type": "total", "amount": 4800}],
        "continue_url": "https://store.myshopify.com/cart",
    }
    cart = _to_cart(raw, "store.myshopify.com")
    assert cart.total_cents == 4800
    assert estimate_total_cents(cart) == 4800
    assert cart.line_items[0].variant_id == "gid://shopify/ProductVariant/9"
    assert cart.line_items[0].quantity == 2
    assert cart.continue_url.endswith("/cart")


def test_checkout_referral_handoff():
    raw = {
        "id": "gid://shopify/Cart/1",
        "status": "requires_escalation",
        "continue_url": "https://store.myshopify.com/cart",
        "mode": "referral_handoff",
    }
    checkout = _to_checkout(raw)
    assert checkout.status == CheckoutStatus.REQUIRES_ESCALATION
    assert checkout.mode == "referral_handoff"


def test_checkout_completed_exposes_order_id():
    raw = {
        "id": "gid://shopify/Checkout/1",
        "status": "completed",
        "order": {"id": "gid://shopify/Order/5"},
    }
    checkout = _to_checkout(raw)
    assert checkout.status == CheckoutStatus.COMPLETED
    assert checkout.order_id == "gid://shopify/Order/5"
