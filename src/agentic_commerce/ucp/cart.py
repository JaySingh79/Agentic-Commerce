"""Phase 2: Universal Cart MCP facade (create/get/update/cancel + totals).

Synchronous wrappers over the shared :class:`ShopifyUcpClient`, returning typed
:class:`~core.models.UCPCart` instances.
"""

from typing import Any

from agentic_commerce.core.models import CartLineItem, UCPCart
from agentic_commerce.core.runtime import run_async
from agentic_commerce.ucp.client import default_client


def _to_cart(raw: dict[str, Any], merchant_domain: str) -> UCPCart:
    """Normalizes a raw Shopify cart dict into a typed UCPCart."""
    line_items = [
        CartLineItem(
            variant_id=li.get("item", {}).get("id") or li.get("variant_id", ""),
            quantity=li.get("quantity", 1),
            title=li.get("title"),
        )
        for li in raw.get("line_items", [])
    ]
    return UCPCart(
        id=raw.get("id", ""),
        merchant_domain=raw.get("merchant_domain", merchant_domain),
        currency=raw.get("currency", "USD"),
        line_items=line_items,
        totals=raw.get("totals", []),
        continue_url=raw.get("continue_url"),
    )


def estimate_total_cents(cart: UCPCart) -> int:
    """Grand total in cents from a cart's Shopify totals array."""
    return cart.total_cents


def cart_create(merchant_domain: str, variant_id: str, quantity: int = 1) -> UCPCart:
    """Creates a Universal Cart on the merchant's Storefront Cart MCP server."""
    raw = run_async(default_client().create_cart(merchant_domain, variant_id, quantity))
    return _to_cart(raw, merchant_domain)


def cart_get(merchant_domain: str, cart_id: str) -> UCPCart:
    """Refreshes an existing cart's totals and availability."""
    raw = run_async(default_client().get_cart(merchant_domain, cart_id))
    return _to_cart(raw, merchant_domain)


def cart_update(
    merchant_domain: str, cart_id: str, line_items: list[dict[str, Any]]
) -> UCPCart:
    """Full-PUT replacement of a cart's line items."""
    raw = run_async(default_client().update_cart(merchant_domain, cart_id, line_items))
    return _to_cart(raw, merchant_domain)


def cart_cancel(merchant_domain: str, cart_id: str) -> dict[str, Any]:
    """Deletes an active cart session."""
    return run_async(default_client().cancel_cart(merchant_domain, cart_id))
