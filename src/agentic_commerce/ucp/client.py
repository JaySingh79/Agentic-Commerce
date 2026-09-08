"""UCP transport: async client for Shopify Universal Commerce Protocol (JSON-RPC 2.0).

Single pooled ``httpx.AsyncClient`` and a cached OAuth token are shared across
every MCP tool call (Catalog, Cart, Checkout, Order). The per-tool modules in
this package (``catalog``, ``cart``, ``checkout``, ``order``) are thin functional
facades over one shared instance of :class:`ShopifyUcpClient`.
"""

import os
import time
import uuid
from typing import Any

import httpx
from dotenv import find_dotenv, load_dotenv

# Load environment variables
load_dotenv(find_dotenv(usecwd=True))

CATALOG_URL = "https://catalog.shopify.com/api/ucp/mcp"
AGENT_PROFILE = "https://shopify.dev/ucp/agent-profiles/2026-04-08/valid-with-capabilities.json"

_META = {"ucp-agent": {"profile": AGENT_PROFILE}}


def _idempotency_meta() -> dict[str, Any]:
    """Meta block carrying the agent profile plus a fresh idempotency key."""
    return {"ucp-agent": {"profile": AGENT_PROFILE}, "idempotency-key": str(uuid.uuid4())}


class ShopifyUcpClient:
    """Asynchronous client for Shopify Universal Commerce Protocol (UCP) JSON-RPC 2.0 endpoints."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout: float = 20.0,
    ):
        self.client_id = client_id or os.getenv("CLIENT_ID") or os.getenv("SHOPIFY_CLIENT_ID")
        self.client_secret = (
            client_secret or os.getenv("CLIENT_SECRET") or os.getenv("SHOPIFY_CLIENT_SECRET")
        )
        self.timeout = timeout
        self._cached_token: str | None = None
        self._token_expiry: float = 0.0
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Returns a pooled AsyncClient, recreating it only if closed (keep-alive reuse)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def aclose(self) -> None:
        """Closes the pooled HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def get_access_token(self, force_refresh: bool = False) -> str:
        """Mints or returns a cached Global API OAuth access token."""
        now = time.time()
        if not force_refresh and self._cached_token and now < self._token_expiry - 60:
            return self._cached_token

        if not self.client_id or not self.client_secret:
            raise ValueError(
                "Shopify CLIENT_ID and CLIENT_SECRET must be configured in environment."
            )

        client = await self._get_client()
        res = await client.post(
            "https://api.shopify.com/auth/access_token",
            json={
                "client_id": self.client_id.strip(),
                "client_secret": self.client_secret.strip(),
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/json"},
        )
        if res.status_code != 200:
            raise RuntimeError(
                f"Failed to authenticate with Shopify API (HTTP {res.status_code}): {res.text}"
            )

        data = res.json()
        token = data.get("access_token")
        if not token:
            raise RuntimeError(f"Shopify auth returned no access_token: {data}")

        expires_in = data.get("expires_in", 3600)
        self._cached_token = token
        self._token_expiry = now + expires_in
        return token

    async def _bearer_headers(self) -> dict[str, str]:
        """Content-Type + Bearer Authorization for token-tier calls."""
        token = await self.get_access_token()
        return {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}

    async def _rpc(
        self,
        endpoint: str,
        rpc_id: int,
        name: str,
        arguments: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        """Executes a single JSON-RPC ``tools/call`` and returns the parsed response body."""
        client = await self._get_client()
        res = await client.post(
            endpoint,
            json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "id": rpc_id,
                "params": {"name": name, "arguments": arguments},
            },
            headers=headers,
        )
        return res.json()

    # ------------------------------------------------------------------ Catalog

    async def search_catalog(
        self,
        query: str,
        catalog_id: str | None = None,
        price_min: int | None = None,
        price_max: int | None = None,
        ships_to_country: str = "US",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Searches the Shopify Global Catalog using the search_catalog tool."""
        catalog_payload: dict[str, Any] = {"query": query.strip()}

        if catalog_id and catalog_id.strip():
            catalog_payload["catalog_id"] = catalog_id.strip()

        filters: dict[str, Any] = {}
        if price_min is not None or price_max is not None:
            price_filter: dict[str, int] = {}
            if price_min is not None:
                price_filter["min"] = price_min
            if price_max is not None:
                price_filter["max"] = price_max
            filters["price"] = price_filter

        if ships_to_country:
            filters["ships_to"] = {"country": ships_to_country}

        if filters:
            catalog_payload["filters"] = filters

        data = await self._rpc(
            CATALOG_URL,
            1,
            "search_catalog",
            {"meta": _META, "catalog": catalog_payload},
            await self._bearer_headers(),
        )

        if "error" in data:
            raise RuntimeError(f"Catalog API Error: {data['error']}")

        structured = data.get("result", {}).get("structuredContent", {})
        products = structured.get("products")
        if products is not None:
            return products[:limit]

        # Fallback text parsing
        content = data.get("result", {}).get("content", [])
        if content and isinstance(content[0], dict) and "text" in content[0]:
            import json

            try:
                parsed = json.loads(content[0]["text"])
                return parsed.get("products", [])[:limit]
            except Exception:
                pass

        return []

    async def get_product(
        self, product_id: str, selected: list[dict[str, str]] | None = None
    ) -> dict[str, Any] | None:
        """Fetches full details for a product via get_product on Catalog MCP.

        When *selected* is provided (e.g. [{"name":"Color","label":"Black"}]),
        it is passed as ``catalog.selected`` so the response media is variant-
        filtered per the UCP spec (product.media contains the matching variant image).
        """
        catalog_payload: dict[str, Any] = {"id": product_id}
        if selected:
            catalog_payload["selected"] = selected
        data = await self._rpc(
            CATALOG_URL,
            2,
            "get_product",
            {"meta": _META, "catalog": catalog_payload},
            await self._bearer_headers(),
        )
        structured = data.get("result", {}).get("structuredContent", {})
        return structured.get("product") or structured

    # -------------------------------------------------------------- Discovery

    async def discover_merchant_mcp(self, merchant_domain: str) -> str:
        """Fetches /.well-known/ucp to discover the merchant's MCP endpoint."""
        origin = (
            merchant_domain if merchant_domain.startswith("http") else f"https://{merchant_domain}"
        )
        try:
            client = await self._get_client()
            res = await client.get(
                f"{origin}/.well-known/ucp",
                headers={"Accept": "application/json"},
                timeout=10.0,
            )
            if res.status_code == 200:
                profile = res.json()
                endpoint = profile.get("services", {}).get("mcp", {}).get("endpoint")
                if endpoint:
                    return endpoint
        except Exception:
            pass

        return f"{origin}/api/ucp/mcp"

    # ------------------------------------------------------------------- Cart

    async def create_cart(
        self,
        merchant_domain: str,
        variant_id: str,
        quantity: int = 1,
        country: str = "US",
    ) -> dict[str, Any]:
        """Creates a universal cart on the merchant's Storefront Cart MCP server."""
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        data = await self._rpc(
            mcp_endpoint,
            3,
            "create_cart",
            {
                "meta": _META,
                "cart": {
                    "line_items": [{"quantity": quantity, "item": {"id": variant_id}}],
                    "context": {"address_country": country},
                    "attribution": {
                        "utm_source": "agentic_commerce",
                        "utm_medium": "ai_agent",
                    },
                },
            },
            {"Content-Type": "application/json"},
        )
        if "error" in data:
            raise RuntimeError(f"Cart MCP Error: {data['error']}")
        structured = data.get("result", {}).get("structuredContent", {})
        cart = structured.get("cart") or structured
        if not cart.get("id"):
            # No "error" key, but no cart id either: the guessed/discovered endpoint
            # answered with something that isn't a real cart (e.g. a merchant whose
            # Storefront doesn't implement Cart MCP at all). Reporting this as a
            # $0/"Unknown" cart "created successfully" is the honesty violation this
            # guards against — surface it as the failure it is instead.
            raise RuntimeError(
                f"Cart MCP at {mcp_endpoint} did not return a cart id for "
                f"{merchant_domain} — it may not implement create_cart."
            )
        return cart

    async def get_cart(self, merchant_domain: str, cart_id: str) -> dict[str, Any]:
        """Refreshes an existing cart's totals and availability (get_cart)."""
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        data = await self._rpc(
            mcp_endpoint,
            10,
            "get_cart",
            {"id": cart_id, "meta": _META},
            {"Content-Type": "application/json"},
        )
        if "error" in data:
            raise RuntimeError(f"Cart MCP Error: {data['error']}")
        structured = data.get("result", {}).get("structuredContent", {})
        cart = structured.get("cart") or structured
        if not cart.get("id"):
            raise RuntimeError(
                f"Cart MCP at {mcp_endpoint} did not return a cart id for "
                f"{merchant_domain}/{cart_id}."
            )
        return cart

    async def update_cart(
        self,
        merchant_domain: str,
        cart_id: str,
        line_items: list[dict[str, Any]],
        country: str = "US",
    ) -> dict[str, Any]:
        """Full-PUT replacement of a cart's line items and context (update_cart)."""
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        data = await self._rpc(
            mcp_endpoint,
            11,
            "update_cart",
            {
                "id": cart_id,
                "meta": _META,
                "cart": {
                    "line_items": line_items,
                    "context": {"address_country": country},
                },
            },
            {"Content-Type": "application/json"},
        )
        if "error" in data:
            raise RuntimeError(f"Cart MCP Error: {data['error']}")
        structured = data.get("result", {}).get("structuredContent", {})
        return structured.get("cart") or structured

    async def cancel_cart(self, merchant_domain: str, cart_id: str) -> dict[str, Any]:
        """Deletes an active cart session (cancel_cart)."""
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        data = await self._rpc(
            mcp_endpoint,
            12,
            "cancel_cart",
            {"id": cart_id, "meta": _idempotency_meta()},
            {"Content-Type": "application/json"},
        )
        return data.get("result", {}).get("structuredContent", {}) or data

    # --------------------------------------------------------------- Checkout

    async def create_checkout(
        self,
        merchant_domain: str,
        cart_id: str,
        cart_url: str | None = None,
        buyer_email: str = "shopper@example.com",
    ) -> dict[str, Any]:
        """Converts a cart to a checkout session or returns the attributed referral permalink."""
        origin = (
            merchant_domain if merchant_domain.startswith("http") else f"https://{merchant_domain}"
        )
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        fallback_url = cart_url or f"{origin}/cart"

        try:
            data = await self._rpc(
                mcp_endpoint,
                4,
                "create_checkout",
                {"cart_id": cart_id, "meta": _META},
                await self._bearer_headers(),
            )
            if "error" not in data and "result" in data:
                structured = data.get("result", {}).get("structuredContent", {})
                checkout = structured.get("checkout") or structured
                if checkout and checkout.get("continue_url"):
                    return checkout
        except Exception:
            pass

        # Return structured referral handoff
        return {
            "id": cart_id,
            "status": "requires_escalation",
            "continue_url": fallback_url,
            "mode": "referral_handoff",
        }

    async def get_checkout(self, merchant_domain: str, checkout_id: str) -> dict[str, Any]:
        """Inspects taxes, shipping, and required fields on a checkout (get_checkout)."""
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        data = await self._rpc(
            mcp_endpoint,
            5,
            "get_checkout",
            {"id": checkout_id, "meta": _META},
            await self._bearer_headers(),
        )
        if "error" in data:
            raise RuntimeError(f"Checkout MCP Error: {data['error']}")
        structured = data.get("result", {}).get("structuredContent", {})
        return structured.get("checkout") or structured

    async def update_checkout(
        self,
        merchant_domain: str,
        checkout_id: str,
        buyer_email: str,
        line_items: list[dict[str, Any]] | None = None,
        currency: str = "USD",
        country: str = "US",
    ) -> dict[str, Any]:
        """Full-PUT: attaches buyer email/address/delivery to a checkout (update_checkout)."""
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        checkout_payload: dict[str, Any] = {
            "currency": currency,
            "context": {"address_country": country},
            "buyer": {"email": buyer_email},
        }
        if line_items is not None:
            checkout_payload["line_items"] = line_items
        data = await self._rpc(
            mcp_endpoint,
            6,
            "update_checkout",
            {"id": checkout_id, "checkout": checkout_payload, "meta": _META},
            await self._bearer_headers(),
        )
        if "error" in data:
            raise RuntimeError(f"Checkout MCP Error: {data['error']}")
        structured = data.get("result", {}).get("structuredContent", {})
        return structured.get("checkout") or structured

    async def complete_checkout(
        self,
        merchant_domain: str,
        checkout_id: str,
        payment: dict[str, Any],
    ) -> dict[str, Any]:
        """Submits an authorized payment instrument to place the order (complete_checkout)."""
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        data = await self._rpc(
            mcp_endpoint,
            7,
            "complete_checkout",
            {"id": checkout_id, "checkout": {"payment": payment}, "meta": _idempotency_meta()},
            await self._bearer_headers(),
        )
        if "error" in data:
            raise RuntimeError(f"Checkout MCP Error: {data['error']}")
        structured = data.get("result", {}).get("structuredContent", {})
        return structured.get("checkout") or structured

    async def cancel_checkout(self, merchant_domain: str, checkout_id: str) -> dict[str, Any]:
        """Cancels an active checkout session (cancel_checkout)."""
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        data = await self._rpc(
            mcp_endpoint,
            8,
            "cancel_checkout",
            {"id": checkout_id, "meta": _idempotency_meta()},
            await self._bearer_headers(),
        )
        return data.get("result", {}).get("structuredContent", {}) or data

    # ------------------------------------------------------------------ Order

    async def get_order(self, merchant_domain: str, order_id: str) -> dict[str, Any] | None:
        """Reads current order status and fulfillment via Order MCP (get_order)."""
        mcp_endpoint = await self.discover_merchant_mcp(merchant_domain)
        data = await self._rpc(
            mcp_endpoint,
            9,
            "get_order",
            {"id": order_id, "meta": _META},
            await self._bearer_headers(),
        )
        result = data.get("result", {})
        if result.get("isError"):
            messages = result.get("structuredContent", {}).get("messages", [])
            raise RuntimeError(f"Order MCP Error: {messages[0] if messages else 'unknown'}")
        structured = result.get("structuredContent", {})
        return structured.get("order") or structured or None


_default_client: ShopifyUcpClient | None = None


def default_client() -> ShopifyUcpClient:
    """Returns the process-wide shared UCP client (pooled connection + token cache)."""
    global _default_client
    if _default_client is None:
        _default_client = ShopifyUcpClient()
    return _default_client
