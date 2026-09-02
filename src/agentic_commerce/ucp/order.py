"""Phase 4: Order MCP facade + Order Webhook HMAC verification.

``order_get`` reads live order status; ``verify_order_webhook`` authenticates an
inbound Shopify order webhook using base64 HMAC-SHA256 over the raw request body
(constant-time compare), and ``parse_order_event`` normalizes the payload.
"""

import base64
import hashlib
import hmac
import os
from typing import Any

from agentic_commerce.core.models import Money, OrderEvent, OrderStatus, UCPOrder
from agentic_commerce.core.runtime import run_async
from agentic_commerce.ucp.client import default_client


def _to_order(raw: dict[str, Any], merchant_domain: str) -> UCPOrder:
    """Normalizes a raw Shopify order dict into a typed UCPOrder."""
    total_obj = next(
        (t for t in raw.get("totals", []) if t.get("type") == "total"),
        {},
    )
    status = raw.get("status") or raw.get("financial_status") or OrderStatus.PENDING.value
    try:
        status_enum = OrderStatus(status)
    except ValueError:
        status_enum = OrderStatus.PENDING
    return UCPOrder(
        id=raw.get("id", ""),
        status=status_enum,
        merchant_domain=merchant_domain,
        total=Money(
            amount=int(total_obj.get("amount", 0)),
            currency=total_obj.get("currency", "USD"),
        ),
        permalink_url=raw.get("permalink_url"),
        events=[parse_order_event(e) for e in raw.get("events", []) if isinstance(e, dict)],
    )


def order_get(merchant_domain: str, order_id: str) -> UCPOrder | None:
    """Reads current order status and fulfillment via Order MCP (get_order)."""
    raw = run_async(default_client().get_order(merchant_domain, order_id))
    return _to_order(raw, merchant_domain) if raw else None


def parse_order_event(payload: dict[str, Any]) -> OrderEvent:
    """Normalizes a webhook / fulfillment event dict into a typed OrderEvent."""
    return OrderEvent(
        topic=payload.get("topic", payload.get("type", "orders/updated")),
        status=payload.get("status"),
        occurred_at=payload.get("occurred_at") or payload.get("timestamp"),
        detail=payload.get("detail", payload),
    )


def verify_order_webhook(
    raw_body: bytes, header_signature: str, secret: str | None = None
) -> bool:
    """Verifies an inbound order webhook's ``X-Shopify-Hmac-SHA256`` signature.

    Computes base64(HMAC-SHA256(secret, raw_body)) and compares it to the header
    value in constant time. ``secret`` defaults to ``CLIENT_SECRET`` from the env.
    """
    shared_secret = secret or os.getenv("CLIENT_SECRET") or ""
    if not shared_secret or not header_signature:
        return False
    digest = hmac.new(shared_secret.encode("utf-8"), raw_body, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, header_signature)
