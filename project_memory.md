# Project Memory: Shopify Universal Commerce Protocol (UCP) & MCP Architecture

This document serves as the persistent master memory, technical glossary, and operational harness for the Agentic Commerce system building on Shopify's Universal Commerce Protocol (UCP) and Model Context Protocol (MCP) servers.

---

## 1. Core Architecture & Protocol Fundamentals

### Universal Commerce Protocol (UCP)
UCP is an open, standardized capability negotiation and commerce execution protocol designed for autonomous AI agents.
* **Specification Version:** `2026-04-08` (Standard), `2026-08-25` (Merchant Profiles).
* **Wire Protocol:** JSON-RPC 2.0 over HTTP `POST` to MCP endpoints.
* **Capability Negotiation:** Server-selects intersection between the platform agent's declared profile and the merchant's supported capabilities.

### Profile Discovery
* **Merchant Business Profile:** Published at `https://{shop-domain}/.well-known/ucp`. Exposes protocol version, active MCP endpoint (`/api/ucp/mcp`), payment handlers, and signing keys.
* **Agent Platform Profile:** Hosted HTTPS JSON fixture passed in `meta["ucp-agent"].profile`. Declares the agent's supported capability namespaces.

---

## 2. Authentication & Trust Tiers

| Tier | Authentication Method | Capabilities & Permissions | Rate Limits |
| :--- | :--- | :--- | :--- |
| **Token Tier** | Bearer JWT via `https://api.shopify.com/auth/access_token` (`grant_type: client_credentials`) using `CLIENT_ID` and `CLIENT_SECRET` | Global Catalog search, Cart MCP, Checkout MCP, `complete_checkout` (if permitted), Order MCP (`read_global_api_orders`), Personalized Search | **Highest** |
| **Signed Tier** | HTTP Message Signatures (RFC 9421, ECDSA P-256) verified via agent's public key in `.well-known/ucp` | Catalog Search, Cart MCP, Checkout MCP (No `complete_checkout`, No Order MCP) | **Medium** |
| **Anonymous Tier** | No `Authorization` or signature headers | Catalog Search, Cart MCP, Checkout MCP (Referral path only) | **Standard** |
| **Buyer-Linked Tokens** | Delegated IdP OAuth exchange for signed-in Shop customer accounts | Personalized search, saved addresses, buyer-specific pricing | Inherits Token tier |

---

## 3. MCP Servers & Tool Glossary

### A. Catalog MCP Server
**Endpoint:** `https://catalog.shopify.com/api/ucp/mcp`

1. **`search_catalog`**
   - **Purpose:** Natural language discovery across hundreds of millions of products in Shopify Global Catalog or scoped to a custom catalog.
   - **Arguments (`catalog`):**
     - `query` *(string)*: Free-text search phrase.
     - `catalog_id` *(string, optional)*: Specific custom catalog ID from Dev Dashboard. *(Leave empty `''` to search entire Global Catalog)*.
     - `like` *(array, optional)*: Image/item references for multimodal search.
     - `filters` *(object, optional)*: `price` (`min`/`max` in minor units/cents), `available` (`true`/`false`), `ships_to` (`country`, `postal_code`), `ships_from`, `condition` (`["new"]`, `["secondhand"]`), `rating`, `attributes`, `price_tier`.
     - `context` *(object, optional)*: `address_country`, `currency`, `language`, `intent`.
     - `pagination` *(object, optional)*: `limit` (1–50) and `cursor`.

2. **`lookup_catalog`**
   - **Purpose:** Batch-resolves known product IDs, variant GIDs, or store URLs to live catalog records.
   - **Arguments (`catalog`):** `ids` (Array of 1–50 GIDs or URLs).

3. **`get_product`**
   - **Purpose:** Fetches complete product details, description HTML, seller domain, variant combinations, and availability signals (`available: true`, `exists: true`).
   - **Arguments (`catalog`):** `id` (`gid://shopify/p/...` or `gid://shopify/ProductVariant/...`), `selected` (Array of chosen options: `[{"name": "Color", "label": "Black"}]`), `preferences`.

---

### B. Cart MCP Server
**Endpoint:** `https://{shop-domain}/api/ucp/mcp` (Resolved dynamically via `/.well-known/ucp`)

1. **`create_cart`**
   - **Purpose:** Initializes a pre-checkout container with long TTL for exploratory multi-turn buyer sessions.
   - **Arguments:**
     - `meta`: `{"ucp-agent": {"profile": "..."}}`.
     - `cart`: `line_items` (array with variant `id` and `quantity`), `context` (`address_country`, `postal_code`), `attribution` (UTM tags, click IDs), `buyer`.
   - **Key Outputs:** `id` (`gid://shopify/Cart/...`), calculated itemized `totals`, and `continue_url` permalink.

2. **`get_cart`**
   - **Purpose:** Refreshes estimated totals, checks stock availability, or resumes an active cart.
   - **Arguments:** `id` (`gid://shopify/Cart/...`), `meta`.

3. **`update_cart`**
   - **Purpose:** Modifies cart items, quantities, or context signals.
   - **Semantics:** **Full PUT replacement** (all desired line items and context must be resent).

4. **`cancel_cart`**
   - **Purpose:** Cleans up and deletes an active cart session.
   - **Arguments:** `id`, `meta` with `idempotency-key` (UUID).

---

### C. Checkout MCP Server
**Endpoint:** `https://{shop-domain}/api/ucp/mcp`

1. **`create_checkout`**
   - **Purpose:** Converts an existing cart (`cart_id`) into a short-lived checkout transaction, or initializes a direct Buy-It-Now session.
   - **Arguments:** `cart_id` (or `checkout.line_items`), `meta`.
   - **Statuses:** `incomplete`, `ready_for_complete`, `requires_escalation`.

2. **`get_checkout`**
   - **Purpose:** Inspects taxes, shipping rates, required fields, and messages on an active checkout session.
   - **Arguments:** `id` (`gid://shopify/Checkout/...`), `meta`.

3. **`update_checkout`**
   - **Purpose:** Attaches buyer email, shipping/billing address, discounts, and delivery method selection.
   - **Semantics:** **Full PUT replacement**.

4. **`complete_checkout`**
   - **Purpose:** Submits authorized payment instruments (Shop Pay, Google Pay, card tokens) to place the order in-application.
   - **Arguments:** `id`, `checkout.payment`, `meta` with `idempotency-key` (UUID).
   - **Output:** `status: "completed"`, `order.id` (`gid://shopify/Order/...`), `order.permalink_url`.

5. **`cancel_checkout`**
   - **Purpose:** Cancels active checkout session upon abandonment.
   - **Arguments:** `id`, `meta` with `idempotency-key` (UUID).

---

### D. Order MCP Server & Order Webhooks
**Order MCP Endpoint:** `https://{shop-domain}/api/ucp/mcp`

1. **`get_order` (Order MCP)**
   - **Purpose:** On-demand read for buyer order status inquiries ("Where is my order?") and missed event reconciliation.
   - **Authentication:** Token tier with `read_global_api_orders` scope.
   - **Propagation Delay:** Allow ~10 seconds after `complete_checkout` before first call.

2. **Order Webhooks**
   - **Purpose:** Primary proactive update channel pushed by Shopify to your agent's webhook endpoint.
   - **Topics:**
     - `orders/create`: Order placed through your agent.
     - `orders/updated`: Status change, payment capture, fulfillment update, tracking added, refund/return committed.
     - `orders/delete`: Order deleted/cancelled.
   - **Full Snapshot Principle:** Every delivery carries the **full, current state** of the order payload (never merge or replay diffs; latest payload is truth).
   - **Security Headers:**
     - `X-Shopify-Hmac-SHA256`: Base64 HMAC-SHA256 signature calculated over raw request body using `CLIENT_SECRET`. Verified with `crypto.timingSafeEqual`.
     - `X-Shopify-Webhook-Id`: Delivery UUID for deduplication.

---

## 4. Operational Insights & Technical Learnings

1. **Custom `catalog_id` Scope vs Global Search:**
   - Specifying a custom `catalog_id` restricts queries strictly to that dashboard catalog whitelist. If the custom catalog has restrictive filters or 0 items, all searches return empty.
   - Leaving `CATALOG_ID = ''` queries across the full multi-merchant Shopify Global Catalog.

2. **Third-Party Merchant Direct Checkout vs Cart Referral Handoff:**
   - Direct `create_checkout` tool calls on third-party merchant domains may return `-32000 AuthenticationFailed` when stores restrict direct programmatic checkout creation to their own apps.
   - **Universal Standard:** For third-party stores, Cart MCP generates a signed, pre-filled `continue_url` permalink with UTM attribution (`utm_source=agentic_commerce`) which seamlessly hands the buyer off to complete purchase with 100% merchant compatibility.

3. **Node.js Environment Execution:**
   - Run CLI scripts with: `node --env-file=.env src/agentic_commerce/backend/ucp_demo.js`.
