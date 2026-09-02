"""Phase 4 tests: order normalization + Order Webhook HMAC verification."""

import base64
import hashlib
import hmac

from agentic_commerce.core.models import OrderStatus
from agentic_commerce.ucp.order import _to_order, parse_order_event, verify_order_webhook


def test_order_normalizes_status_and_total():
    raw = {
        "id": "gid://shopify/Order/9",
        "status": "paid",
        "totals": [{"type": "total", "amount": 12900, "currency": "USD"}],
        "permalink_url": "https://store.myshopify.com/orders/9",
        "events": [{"topic": "orders/create", "status": "paid"}],
    }
    order = _to_order(raw, "store.myshopify.com")
    assert order.status == OrderStatus.PAID
    assert order.total.amount == 12900
    assert order.events[0].topic == "orders/create"


def test_webhook_hmac_valid_and_tampered():
    secret = "shared_secret_abc"
    body = b'{"id":"gid://shopify/Order/9","status":"paid"}'
    good_sig = base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()

    assert verify_order_webhook(body, good_sig, secret) is True
    assert verify_order_webhook(body + b"tamper", good_sig, secret) is False
    assert verify_order_webhook(body, "not-a-signature", secret) is False
    assert verify_order_webhook(body, "", secret) is False


def test_parse_order_event():
    event = parse_order_event({"topic": "orders/updated", "status": "fulfilled"})
    assert event.topic == "orders/updated"
    assert event.status == "fulfilled"
