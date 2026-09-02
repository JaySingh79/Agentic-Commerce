"""Phase 4: Checkout MCP facade (create/get/update/complete/cancel).

Synchronous wrappers over the shared :class:`ShopifyUcpClient`, returning typed
:class:`~core.models.UCPCheckout` instances. ``checkout_create`` falls back to a
signed referral handoff when direct programmatic checkout is not permitted.
"""

from typing import Any

from agentic_commerce.core.models import CheckoutStatus, UCPCheckout
from agentic_commerce.core.runtime import run_async
from agentic_commerce.ucp.client import default_client


def _to_checkout(raw: dict[str, Any]) -> UCPCheckout:
    """Normalizes a raw checkout / referral-handoff dict into a typed UCPCheckout."""
    status = raw.get("status", CheckoutStatus.INCOMPLETE.value)
    try:
        status_enum = CheckoutStatus(status)
    except ValueError:
        status_enum = CheckoutStatus.INCOMPLETE
    order = raw.get("order", {})
    return UCPCheckout(
        id=raw.get("id", ""),
        status=status_enum,
        continue_url=raw.get("continue_url"),
        mode=raw.get("mode"),
        order_id=order.get("id") if isinstance(order, dict) else None,
    )


def checkout_create(
    merchant_domain: str,
    cart_id: str,
    cart_url: str | None = None,
    buyer_email: str = "shopper@example.com",
) -> UCPCheckout:
    """Converts a cart to a checkout session, or returns a referral handoff."""
    raw = run_async(
        default_client().create_checkout(merchant_domain, cart_id, cart_url, buyer_email)
    )
    return _to_checkout(raw)


def checkout_get(merchant_domain: str, checkout_id: str) -> UCPCheckout:
    """Inspects taxes, shipping, and required fields on a checkout session."""
    raw = run_async(default_client().get_checkout(merchant_domain, checkout_id))
    return _to_checkout(raw)


def checkout_update(
    merchant_domain: str, checkout_id: str, buyer_email: str
) -> UCPCheckout:
    """Attaches buyer email/address/delivery to a checkout session."""
    raw = run_async(
        default_client().update_checkout(merchant_domain, checkout_id, buyer_email)
    )
    return _to_checkout(raw)


def checkout_complete(
    merchant_domain: str, checkout_id: str, payment: dict[str, Any]
) -> UCPCheckout:
    """Submits an authorized payment instrument to place the order."""
    raw = run_async(default_client().complete_checkout(merchant_domain, checkout_id, payment))
    return _to_checkout(raw)


def checkout_cancel(merchant_domain: str, checkout_id: str) -> dict[str, Any]:
    """Cancels an active checkout session."""
    return run_async(default_client().cancel_checkout(merchant_domain, checkout_id))
