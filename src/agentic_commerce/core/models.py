"""Phase 1: Core UCP Pydantic domain models.

Typed contracts for the Universal Commerce Protocol layer. Shapes mirror the
dicts already produced by the live Shopify UCP client, the AP2 engine, and the
session store, so live payloads parse loosely (``extra="ignore"``).
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    """Base model: ignore unknown keys so live Shopify payloads parse cleanly."""

    model_config = ConfigDict(extra="ignore")


class TrustTier(StrEnum):
    """UCP authentication trust tiers (see project_memory.md section 2)."""

    TOKEN = "token"
    SIGNED = "signed"
    ANONYMOUS = "anonymous"
    BUYER_LINKED = "buyer_linked"


class CheckoutStatus(StrEnum):
    """Lifecycle states for a UCP checkout session."""

    INCOMPLETE = "incomplete"
    READY_FOR_COMPLETE = "ready_for_complete"
    REQUIRES_ESCALATION = "requires_escalation"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class MandateStatus(StrEnum):
    """Lifecycle states for an AP2 payment mandate."""

    AUTHORIZED_PENDING_SETTLEMENT = "AUTHORIZED_PENDING_SETTLEMENT"
    SETTLED = "SETTLED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


class OrderStatus(StrEnum):
    """Financial / fulfillment status for a UCP order."""

    PENDING = "pending"
    PAID = "paid"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class Money(_Base):
    """A monetary amount in minor units (cents)."""

    amount: int = 0
    currency: str = "USD"


class MerchantSeller(_Base):
    """The merchant/seller that owns a product or fulfills an order."""

    name: str = "Merchant"
    domain: str = "shopify.com"


class MediaItem(_Base):
    """A product/variant media asset (typically an image on the Shopify CDN)."""

    type: str = "image"
    url: str
    alt_text: str | None = None


class ProductVariant(_Base):
    """A single purchasable variant of a product (Shopify ProductVariant GID)."""

    id: str = ""
    title: str | None = None
    price: Money = Field(default_factory=Money)
    seller: MerchantSeller = Field(default_factory=MerchantSeller)
    available: bool = True
    image: MediaItem | None = None


class Product(_Base):
    """A catalog product with one or more variants (Shopify Product GID)."""

    id: str
    title: str = "Unknown"
    description: Any = None  # live catalog returns {"plain": "...", "html": "..."}
    seller: MerchantSeller = Field(default_factory=MerchantSeller)
    variants: list[ProductVariant] = Field(default_factory=list)
    options: list[dict[str, Any]] = Field(default_factory=list)
    price_range: dict[str, Any] = Field(default_factory=dict)
    media: list[MediaItem] = Field(default_factory=list)

    @property
    def min_price_cents(self) -> int | None:
        """Best-effort minimum price in cents from the Shopify price_range block."""
        amount = self.price_range.get("min", {}).get("amount")
        return int(amount) if amount is not None else None

    @property
    def primary_image_url(self) -> str | None:
        """First product image URL, falling back to the featured variant image."""
        if self.media:
            return self.media[0].url
        for variant in self.variants:
            if variant.image and variant.image.url:
                return variant.image.url
        return None


class CartLineItem(_Base):
    """A quantity of a variant inside a cart."""

    variant_id: str
    quantity: int = 1
    title: str | None = None


class UCPCart(_Base):
    """A Universal Cart (Shopify Cart GID) with itemized totals."""

    id: str
    merchant_domain: str = "shopify.com"
    currency: str = "USD"
    line_items: list[CartLineItem] = Field(default_factory=list)
    totals: list[dict[str, Any]] = Field(default_factory=list)
    continue_url: str | None = None

    @property
    def total_cents(self) -> int:
        """Grand total in cents, read from the Shopify totals array."""
        return int(
            next((t.get("amount", 0) for t in self.totals if t.get("type") == "total"), 0)
        )


class UCPCheckout(_Base):
    """A checkout session created from a cart, or a referral handoff."""

    id: str
    status: CheckoutStatus = CheckoutStatus.INCOMPLETE
    continue_url: str | None = None
    mode: str | None = None
    order_id: str | None = None


class AP2PaymentMandate(_Base):
    """A signed AP2 payment authorization mandate."""

    ap2_version: str = "2026-04-08"
    mandate_id: str
    buyer_id: str = "user_default"
    cart_id: str
    merchant_domain: str = "shopify.com"
    amount_cents: int = 0
    currency: str = "USD"
    created_at: int = 0
    expires_at: int = 0
    status: MandateStatus = MandateStatus.AUTHORIZED_PENDING_SETTLEMENT
    spending_limit_cents: int = 0
    signature: str = ""


class OrderEvent(_Base):
    """A single lifecycle event on an order (webhook or fulfillment history)."""

    topic: str
    status: str | None = None
    occurred_at: int | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class UCPOrder(_Base):
    """A placed order (Shopify Order GID) with financial + fulfillment status."""

    id: str
    status: OrderStatus = OrderStatus.PENDING
    merchant_domain: str = "shopify.com"
    total: Money = Field(default_factory=Money)
    permalink_url: str | None = None
    events: list[OrderEvent] = Field(default_factory=list)


class UCPProfile(_Base):
    """A UCP agent capability profile advertised in the ``ucp-agent`` meta block."""

    profile_url: str
    version: str = "2026-04-08"
    capabilities: list[str] = Field(default_factory=list)
    trust_tier: TrustTier = TrustTier.ANONYMOUS
