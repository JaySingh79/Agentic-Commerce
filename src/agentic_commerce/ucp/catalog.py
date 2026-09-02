"""Phase 2: Catalog MCP facade (catalog_search, get_product, get_variant_details).

Thin synchronous wrappers over the shared :class:`ShopifyUcpClient`, returning
typed :mod:`core.models` instances instead of raw dicts.
"""

from agentic_commerce.core.models import Product, ProductVariant
from agentic_commerce.core.runtime import run_async
from agentic_commerce.ucp.client import default_client


def catalog_search(query: str, max_price: float | None = None, limit: int = 5) -> list[Product]:
    """Searches the Shopify Global Catalog and returns typed Product models."""
    price_max_cents = int(max_price * 100) if max_price else None
    raw = run_async(
        default_client().search_catalog(query=query, price_max=price_max_cents, limit=limit)
    )
    return [Product.model_validate(p) for p in raw]


def get_product(product_id: str) -> Product | None:
    """Fetches full product details and returns a typed Product model."""
    raw = run_async(default_client().get_product(product_id))
    return Product.model_validate(raw) if raw else None


def get_variant_details(product_id: str, variant_index: int = 0) -> ProductVariant | None:
    """Returns a single variant of a product by position (default: featured)."""
    product = get_product(product_id)
    if not product or not product.variants:
        return None
    if variant_index < 0 or variant_index >= len(product.variants):
        variant_index = 0
    return product.variants[variant_index]
