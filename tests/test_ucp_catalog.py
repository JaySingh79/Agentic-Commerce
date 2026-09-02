"""Phase 2 tests: catalog Product/variant parsing and Shopify GID shapes."""

from agentic_commerce.core.models import Product

CATALOG_FIXTURE = {
    "id": "gid://shopify/Product/12345",
    "title": "Trail Running Shoe",
    "price_range": {"min": {"amount": 11700}},
    "media": [
        {"type": "image", "url": "https://cdn.shopify.com/p/trail.jpg", "alt_text": "Trail shoe"}
    ],
    "variants": [
        {
            "id": "gid://shopify/ProductVariant/9",
            "price": {"amount": 11700, "currency": "USD"},
            "seller": {"name": "SprintAthletics", "domain": "sprint.myshopify.com"},
        }
    ],
    "options": [{"name": "Size", "values": ["8", "9", "10"]}],
}


def test_product_parses_with_gids_and_price():
    product = Product.model_validate(CATALOG_FIXTURE)
    assert product.id.startswith("gid://shopify/Product/")
    assert product.min_price_cents == 11700
    variant = product.variants[0]
    assert variant.id.startswith("gid://shopify/ProductVariant/")
    assert variant.price.amount == 11700
    assert variant.seller.domain == "sprint.myshopify.com"


def test_product_ignores_unknown_fields():
    product = Product.model_validate({"id": "gid://x", "title": "T", "unmapped": 123})
    assert product.title == "T"
    assert product.variants == []


def test_product_captures_media_and_primary_image():
    product = Product.model_validate(CATALOG_FIXTURE)
    assert product.media[0].url == "https://cdn.shopify.com/p/trail.jpg"
    assert product.media[0].alt_text == "Trail shoe"
    assert product.primary_image_url == "https://cdn.shopify.com/p/trail.jpg"


def test_product_primary_image_falls_back_to_variant():
    raw = {
        "id": "gid://x",
        "variants": [{"id": "v1", "image": {"url": "https://cdn.shopify.com/v/1.jpg"}}],
    }
    product = Product.model_validate(raw)
    assert product.primary_image_url == "https://cdn.shopify.com/v/1.jpg"
