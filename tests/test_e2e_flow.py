"""E2E tests: orchestrator pipeline Discovery -> Cart -> Mandate -> Settle -> Order.

Uses a fake async UCP client so the pipeline runs offline and deterministically.
"""

from typing import Any

from agentic_commerce.ap2.mandate import AP2Engine
from agentic_commerce.core.models import MandateStatus, OrderStatus
from agentic_commerce.orchestrator import CommerceOrchestrator


class FakeUcpClient:
    """Minimal async stand-in exposing the two live methods the pipeline calls."""

    async def search_catalog(
        self, query: str, price_max: int | None = None, limit: int = 5
    ) -> list[dict[str, Any]]:
        return [
            {
                "id": "gid://shopify/Product/1",
                "title": "Trail Runner",
                "price_range": {"min": {"amount": 11700}},
                "variants": [{"id": "gid://shopify/ProductVariant/9"}],
            }
        ]

    async def create_cart(
        self, merchant_domain: str, variant_id: str, quantity: int = 1
    ) -> dict[str, Any]:
        return {
            "id": "gid://shopify/Cart/1",
            "currency": "USD",
            "line_items": [{"quantity": quantity, "item": {"id": variant_id}}],
            "totals": [{"type": "total", "amount": 11700}],
            "continue_url": "https://store.myshopify.com/cart",
        }


def _orchestrator() -> CommerceOrchestrator:
    return CommerceOrchestrator(client=FakeUcpClient(), engine=AP2Engine(secret_key="k"))


def test_pipeline_end_to_end():
    result = _orchestrator().run(
        query="running shoes",
        merchant_domain="store.myshopify.com",
        variant_id="gid://shopify/ProductVariant/9",
    )
    assert result["products"][0].id.startswith("gid://shopify/Product/")
    assert result["cart"].total_cents == 11700
    assert result["mandate"]["status"] == MandateStatus.AUTHORIZED_PENDING_SETTLEMENT.value
    assert result["receipt"]["status"] == MandateStatus.SETTLED.value
    assert result["order"].status == OrderStatus.PAID
    assert result["order"].total.amount == 11700
    assert result["order"].id.startswith("gid://shopify/Order/")


def test_negotiation_seam_overrides_amount():
    orch = _orchestrator()
    orch.negotiate = lambda product: 9900
    result = orch.run(
        query="x", merchant_domain="store.myshopify.com", variant_id="gid://shopify/ProductVariant/9"
    )
    assert result["mandate"]["amount_cents"] == 9900
    assert result["receipt"]["amount_cents"] == 9900
