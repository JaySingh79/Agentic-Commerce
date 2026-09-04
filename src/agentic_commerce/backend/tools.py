"""Tool Registry for Agentic Commerce exposing LangChain Tools.

Tools are built per session via :func:`make_commerce_tools`. Binding the session
id at construction time (rather than reading an ambient ``ContextVar``) is
required for correctness: Gradio advances a streaming generator via
``anyio.to_thread``, so consecutive steps can run on different worker threads and
a context var set earlier in the generator body is not visible when a tool later
executes. Ambient routing silently wrote results into ``default_user_session``,
leaving the product gallery empty for every real browser session.
"""

from langchain.tools import tool
from langchain_core.tools import BaseTool

from agentic_commerce.a2a.protocol import format_transcript
from agentic_commerce.backend.ap2 import AP2Engine
from agentic_commerce.backend.crew import CommerceCrew
from agentic_commerce.backend.session import DEFAULT_SESSION_ID, get_or_create_session
from agentic_commerce.backend.web_search import format_results, search_web
from agentic_commerce.core.runtime import run_async as _run_async
from agentic_commerce.core.telemetry import trace_tool_execution
from agentic_commerce.ucp.client import ShopifyUcpClient

# Global instances
_ucp_client = ShopifyUcpClient()
_ap2_engine = AP2Engine()
_crew = CommerceCrew(client=_ucp_client)


def primary_image_url(product: dict) -> str | None:
    """Extracts a product's primary image URL from its UCP media (or featured variant).

    Reads ``product["media"][0]["url"]`` first, then falls back to the featured
    variant's ``image``/``media``. Returns None when no image is present.
    """
    for item in product.get("media", []) or []:
        url = item.get("url") if isinstance(item, dict) else None
        if url:
            return url
    variants = product.get("variants", []) or []
    if variants and isinstance(variants[0], dict):
        featured = variants[0]
        image = featured.get("image")
        if isinstance(image, dict) and image.get("url"):
            return image["url"]
        for item in featured.get("media", []) or []:
            url = item.get("url") if isinstance(item, dict) else None
            if url:
                return url
    return None


def make_commerce_tools(session_id: str = DEFAULT_SESSION_ID) -> list[BaseTool]:
    """Builds the commerce tool set bound to *session_id*.

    Every tool writes its state into ``get_or_create_session(session_id)`` and
    attributes its telemetry span to the same id, so results are never routed by
    ambient thread context.
    """

    def _session():
        return get_or_create_session(session_id)

    @tool
    def search_products(query: str, max_price: float | None = None) -> str:
        """Search for products across Shopify's Global Catalog.

        Args:
            query: The product search query (e.g., 'running shoes', 'cotton hoodie').
            max_price: Optional maximum price in dollars (e.g. 150.0).
        """
        price_max_cents = int(max_price * 100) if max_price else None

        async def _search():
            products = await _ucp_client.search_catalog(
                query=query,
                price_max=price_max_cents,
                limit=5,
            )
            _session().update_search_results(products)

            if not products:
                return f"No products found matching '{query}'."

            lines = [f"Found {len(products)} products for '{query}':\n"]
            for idx, p in enumerate(products, 1):
                title = p.get("title", "Unknown")
                pid = p.get("id", "")
                price_range = p.get("price_range", {}).get("min", {}).get("amount")
                price_str = f"${(price_range / 100):.2f}" if price_range else "N/A"
                options = p.get("options", [])
                opt_str = (
                    ", ".join(f"{o.get('name')}: {len(o.get('values', []))} opts" for o in options)
                    if options
                    else "Standard"
                )
                img_str = " | 🖼 image" if primary_image_url(p) else ""
                lines.append(
                    f"{idx}. **{title}** | Price: {price_str} | ID: `{pid}` | {opt_str}{img_str}"
                )

            return "\n".join(lines)

        try:
            with trace_tool_execution(
                "search_products",
                {"query": query, "max_price": max_price},
                session_id=session_id,
            ):
                return _run_async(_search())
        except Exception as e:
            return f"Catalog search error: {e}"

    @tool
    def search_web_products(query: str, max_price: float | None = None) -> str:
        """Search the public internet for products when the Shopify catalog has no match.

        Use this as a fallback after `search_products` returns nothing, or when the
        shopper explicitly asks to look beyond the Shopify catalog (e.g. 'search the
        web', 'what else is out there', a brand or niche item the catalog lacks).
        Results are external listings and cannot be added to a Universal Cart.

        Args:
            query: What the shopper is looking for (e.g. 'waterproof trail runners').
            max_price: Optional maximum price in dollars, folded into the query.
        """
        phrase = f"{query} under ${max_price:.0f}" if max_price else query

        async def _web():
            results = await search_web(phrase, limit=5)
            _session().update_web_results([r.as_dict() for r in results])
            return format_results(phrase, results)

        try:
            with trace_tool_execution(
                "search_web_products",
                {"query": query, "max_price": max_price},
                session_id=session_id,
            ):
                return _run_async(_web())
        except Exception as e:
            return f"Web search error: {e}"

    @tool
    def get_product_details(product_id: str, selected: list[dict[str, str]] | None = None) -> str:
        """Fetch complete product and variant details including merchant domain and price.

        Args:
            product_id: The product GID (e.g., 'gid://shopify/p/...').
            selected: Optional variant filtering, e.g. [{"name": "Color", "label": "Black"}]
                      — mirrors UCP get_product catalog.selected; returns variant-filtered
                      media (product.media[].url/alt_text/type) when provided.
        """

        async def _get():
            prod = await _ucp_client.get_product(product_id, selected=selected)
            if not prod:
                return f"Could not find product details for `{product_id}`."

            title = prod.get("title", "Product")
            variants = prod.get("variants", [])
            if not variants:
                return f"Product '{title}' has no available variants."

            featured = variants[0]
            seller = featured.get("seller", {})
            price = featured.get("price", {}).get("amount", 0)
            domain = seller.get("domain") or seller.get("name") or "shopify.com"

            _session().active_product = prod

            lines = [
                f"### Product: {title}",
                f"- **Price:** ${(price / 100):.2f}",
                f"- **Merchant:** {seller.get('name', 'Merchant')} (`{domain}`)",
                f"- **Featured Variant ID:** `{featured.get('id')}`",
                f"- **Available Variants:** {len(variants)}",
            ]
            if primary_image_url(prod):
                lines.append("- **Image:** available")
            return "\n".join(lines)

        try:
            with trace_tool_execution(
                "get_product_details",
                {"product_id": product_id, "selected": selected},
                session_id=session_id,
            ):
                return _run_async(_get())
        except Exception as e:
            return f"Product lookup error: {e}"

    @tool
    def add_to_cart(merchant_domain: str, variant_id: str, quantity: int = 1) -> str:
        """Create a Universal Cart on the merchant's Storefront Cart MCP server.

        Args:
            merchant_domain: The merchant storefront domain (e.g. 'shop.myshopify.com').
            variant_id: The product variant GID (e.g., 'gid://shopify/ProductVariant/...').
            quantity: Number of items to add (default: 1).
        """

        async def _cart():
            cart = await _ucp_client.create_cart(
                merchant_domain=merchant_domain,
                variant_id=variant_id,
                quantity=quantity,
            )
            cart_id = cart.get("id", "Unknown")
            total_obj = next((t for t in cart.get("totals", []) if t.get("type") == "total"), None)
            total_cents = total_obj.get("amount", 0) if total_obj else 0
            continue_url = cart.get("continue_url", "")

            cart["merchant_domain"] = merchant_domain
            _session().update_cart(cart)

            return (
                f"🛒 **Cart Created Successfully!**\n"
                f"- **Cart ID:** `{cart_id}`\n"
                f"- **Merchant:** `{merchant_domain}`\n"
                f"- **Total:** ${(total_cents / 100):.2f}\n"
                f"- **Checkout Link:** [Finish Purchase on Storefront]({continue_url})\n"
                f"- *Use `generate_ap2_mandate` or `checkout_cart` to finalize.*"
            )

        try:
            with trace_tool_execution(
                "add_to_cart",
                {
                    "merchant_domain": merchant_domain,
                    "variant_id": variant_id,
                    "quantity": quantity,
                },
                session_id=session_id,
            ):
                return _run_async(_cart())
        except Exception as e:
            return f"Cart creation error: {e}"

    @tool
    def checkout_cart(
        cart_id: str, merchant_domain: str, buyer_email: str = "shopper@example.com"
    ) -> str:
        """Converts an active cart into a checkout session with referral handoff.

        Args:
            cart_id: The cart GID.
            merchant_domain: The merchant domain.
            buyer_email: The buyer's email for order updates.
        """

        async def _checkout():
            session = _session()
            cart_url = session.active_cart.get("continue_url") if session.active_cart else None

            res = await _ucp_client.create_checkout(
                merchant_domain=merchant_domain,
                cart_id=cart_id,
                cart_url=cart_url,
                buyer_email=buyer_email,
            )
            continue_url = res.get("continue_url", "")
            session.active_checkout = res

            return (
                f"✅ **Checkout Session Ready!**\n"
                f"- **Cart Ref:** `{cart_id}`\n"
                f"- **Buyer Email:** `{buyer_email}`\n"
                f"- **Referral Checkout Link:** [Click here to Complete Purchase]({continue_url})"
            )

        try:
            with trace_tool_execution(
                "checkout_cart",
                {
                    "cart_id": cart_id,
                    "merchant_domain": merchant_domain,
                    "buyer_email": buyer_email,
                },
                session_id=session_id,
            ):
                return _run_async(_checkout())
        except Exception as e:
            return f"Checkout error: {e}"

    @tool
    def generate_ap2_mandate(
        cart_id: str,
        amount_cents: int,
        currency: str = "USD",
        merchant_domain: str = "shopify.com",
    ) -> str:
        """Generate an Agent Payment Protocol (AP2) verifiable payment authorization mandate.

        Args:
            cart_id: The active Cart ID.
            amount_cents: The exact amount in minor units (cents, e.g. 2400 for $24.00).
            currency: The 3-letter currency code (default: 'USD').
            merchant_domain: The merchant domain receiving the payment.
        """
        try:
            with trace_tool_execution(
                "generate_ap2_mandate",
                {
                    "cart_id": cart_id,
                    "amount_cents": amount_cents,
                    "currency": currency,
                    "merchant_domain": merchant_domain,
                },
                session_id=session_id,
            ):
                mandate = _ap2_engine.create_payment_mandate(
                    cart_id=cart_id,
                    amount_cents=amount_cents,
                    currency=currency,
                    merchant_domain=merchant_domain,
                )
                _session().update_mandate(mandate)
                return _ap2_engine.format_mandate_display(mandate)
        except Exception as e:
            return f"AP2 mandate generation error: {e}"

    @tool
    def negotiate_price(
        product_title: str,
        list_price_cents: int,
        budget_cents: int,
        merchant_name: str = "MerchantAgent",
        loyalty_tier: str = "standard",
    ) -> str:
        """Negotiate a better price with the merchant's sales agent over A2A.

        Use when the shopper wants a discount, says a price is too high, or names
        a budget below the listed price. Runs an autonomous RFQ/OFFER/COUNTER
        exchange that never exceeds the shopper's budget.

        Args:
            product_title: The item being negotiated (e.g. 'Brooks Adrenaline GTS 22').
            list_price_cents: Current listed price in cents.
            budget_cents: The shopper's maximum acceptable price in cents.
            merchant_name: Display name of the merchant's agent.
            loyalty_tier: One of 'standard', 'silver', 'gold', 'platinum'.
        """
        try:
            with trace_tool_execution(
                "negotiate_price",
                {"product_title": product_title, "budget_cents": budget_cents},
                session_id=session_id,
            ):
                result = _crew.run_negotiation(
                    item=product_title,
                    list_price_cents=list_price_cents,
                    budget_cents=budget_cents,
                    merchant_name=merchant_name,
                    tier=loyalty_tier,
                )
                _session().update_negotiation(result.as_dict())
                header = (
                    f"🤝 **Deal agreed at {result.as_dict()['final_price_display']}** "
                    f"(saved {result.as_dict()['savings_display']} in {result.rounds} rounds)"
                    if result.agreed
                    else f"🤝 **No deal** — {result.reason}"
                )
                return f"{header}\n\n{format_transcript(result.transcript)}"
        except Exception as e:
            return f"Negotiation error: {e}"

    @tool
    def process_test_payment(
        amount_cents: int, currency: str = "USD", cart_id: str = ""
    ) -> str:
        """Capture a test-mode payment for an authorized cart or AP2 mandate.

        Routes to Razorpay when configured, otherwise Stripe, otherwise a clearly
        labelled simulated gateway. Never charges real money: only test keys are
        accepted.

        Args:
            amount_cents: Amount in minor units (cents/paise), e.g. 2400 for $24.00.
            currency: 3-letter currency code (default 'USD'; use 'INR' for Razorpay).
            cart_id: Optional cart reference used as the payment receipt label.
        """
        try:
            with trace_tool_execution(
                "process_test_payment",
                {"amount_cents": amount_cents, "currency": currency},
                session_id=session_id,
            ):
                result = _crew.run_payment(
                    amount_cents=amount_cents,
                    currency=currency,
                    receipt=cart_id or None,
                )
                _session().update_payment(result.as_dict())
                return result.format_display()
        except Exception as e:
            return f"Payment error: {e}"

    return [
        search_products,
        search_web_products,
        get_product_details,
        negotiate_price,
        process_test_payment,
        add_to_cart,
        checkout_cart,
        generate_ap2_mandate,
    ]


# Default-session tool set, kept for schema introspection and back-compat imports.
ALL_COMMERCE_TOOLS: list[BaseTool] = make_commerce_tools(DEFAULT_SESSION_ID)
