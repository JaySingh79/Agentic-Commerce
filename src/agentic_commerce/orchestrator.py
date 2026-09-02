"""Phase 5: Master pipeline orchestrator.

Runs the core commerce pipeline end-to-end: discover (live Shopify) -> cart
(live) -> AP2 mandate (local) -> settlement (local mock) -> order (local mock,
since live checkout_complete on third-party stores is gated). A2A negotiation is
a no-op seam here, wired in a later phase.
"""

import uuid
from collections.abc import Callable
from typing import Any

from agentic_commerce.ap2.mandate import AP2Engine
from agentic_commerce.ap2.settlement import MockSettlementGateway
from agentic_commerce.core.models import (
    Money,
    OrderEvent,
    OrderStatus,
    Product,
    UCPCart,
    UCPOrder,
)
from agentic_commerce.core.runtime import run_async
from agentic_commerce.ucp.cart import _to_cart
from agentic_commerce.ucp.client import ShopifyUcpClient, default_client


class CommerceOrchestrator:
    """Sequential Discovery -> Cart -> Mandate -> Settlement -> Order pipeline."""

    def __init__(
        self,
        client: ShopifyUcpClient | None = None,
        engine: AP2Engine | None = None,
        gateway: MockSettlementGateway | None = None,
    ):
        self.client = client or default_client()
        self.engine = engine or AP2Engine()
        self.gateway = gateway or MockSettlementGateway(self.engine)
        # A2A negotiation seam: (Product) -> price_cents. Defaults to list price.
        self.negotiate: Callable[[Product], int] | None = None

    # ---------------------------------------------------------------- steps

    def discover(self, query: str, max_price: float | None = None, limit: int = 5) -> list[Product]:
        """Live catalog discovery -> typed Product list."""
        price_max = int(max_price * 100) if max_price else None
        raw = run_async(
            self.client.search_catalog(query=query, price_max=price_max, limit=limit)
        )
        return [Product.model_validate(p) for p in raw]

    def open_cart(self, merchant_domain: str, variant_id: str, quantity: int = 1) -> UCPCart:
        """Creates a Universal Cart on the merchant Cart MCP."""
        raw = run_async(self.client.create_cart(merchant_domain, variant_id, quantity))
        return _to_cart(raw, merchant_domain)

    def authorize_payment(
        self, cart: UCPCart, amount_cents: int | None = None, buyer_id: str = "user_default"
    ) -> dict[str, Any]:
        """Creates a signed AP2 mandate for the cart total (or an explicit amount)."""
        amount = amount_cents if amount_cents is not None else cart.total_cents
        return self.engine.create_payment_mandate(
            cart_id=cart.id,
            amount_cents=amount,
            currency=cart.currency,
            merchant_domain=cart.merchant_domain,
            buyer_id=buyer_id,
        )

    def settle(self, mandate: dict[str, Any]) -> dict[str, Any]:
        """Captures a verified mandate through the mock settlement gateway."""
        return self.gateway.capture(mandate)

    def place_order(self, cart: UCPCart, receipt: dict[str, Any]) -> UCPOrder:
        """Records a local order once settlement has captured (live checkout gated)."""
        return UCPOrder(
            id=f"gid://shopify/Order/{uuid.uuid4().hex[:16]}",
            status=OrderStatus.PAID,
            merchant_domain=cart.merchant_domain,
            total=Money(
                amount=receipt.get("amount_cents", cart.total_cents),
                currency=cart.currency,
            ),
            permalink_url=cart.continue_url,
            events=[OrderEvent(topic="orders/create", status=OrderStatus.PAID.value)],
        )

    # ------------------------------------------------------------------ run

    def run(
        self,
        query: str,
        merchant_domain: str,
        variant_id: str,
        quantity: int = 1,
        max_price: float | None = None,
        buyer_id: str = "user_default",
    ) -> dict[str, Any]:
        """Executes the full pipeline and returns every intermediate artifact."""
        products = self.discover(query, max_price=max_price)
        cart = self.open_cart(merchant_domain, variant_id, quantity)

        amount_cents = cart.total_cents
        if self.negotiate and products:
            amount_cents = self.negotiate(products[0])

        mandate = self.authorize_payment(cart, amount_cents=amount_cents, buyer_id=buyer_id)
        receipt = self.settle(mandate)
        order = self.place_order(cart, receipt)

        return {
            "products": products,
            "cart": cart,
            "mandate": mandate,
            "receipt": receipt,
            "order": order,
        }
