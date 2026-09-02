# Project Context: Agentic Commerce

## 1. Project Details
- **Repository Name**: Agentic Commerce
- **Purpose**: Autonomous e-commerce agent platform integrating Shopify's Universal Commerce Protocol (UCP), Model Context Protocol (MCP), Agent-to-Agent (A2A) communications, and Agent Payment Protocol (AP2) for natural language discovery, cart iteration, automated checkout, and order monitoring.
- **Target Audience / End Users**: AI Agents, shoppers, autonomous commerce interfaces, and e-commerce platforms.

## 2. Technology Stack
- **Backend / Protocol Layer**: Node.js (v22+), ES Modules, JSON-RPC 2.0, Express (for Order Webhook endpoints).
- **Frontend / Agent Orchestration**: Python 3.12+ (`.venv` managed via `uv`), FastAPI / Gradio UI, Google Gemini 2.5 Flash / Pro, LiteLLM.
- **Protocol Specifications**:
  - Shopify Universal Commerce Protocol (UCP) `2026-04-08` / `2026-08-25`
  - Model Context Protocol (MCP) `2026-04-08`
  - Agent Payments Protocol (AP2) Mandates
  - RFC 9421 HTTP Message Signatures

## 3. Core Integrations & Endpoints
- **Shopify Global Auth**: `https://api.shopify.com/auth/access_token`
- **Shopify Global Catalog MCP**: `https://catalog.shopify.com/api/ucp/mcp`
- **Merchant Storefront Discovery**: `https://{shop-domain}/.well-known/ucp`
- **Merchant Cart & Checkout MCP**: `https://{shop-domain}/api/ucp/mcp`

## 4. Key Project Files
- `src/agentic_commerce/backend/auth.js`: OAuth Client Credentials JWT minting.
- `src/agentic_commerce/backend/search.js`: Global Catalog search (`search_catalog`).
- `src/agentic_commerce/backend/product.js`: Variant lookup & option resolution (`get_product`).
- `src/agentic_commerce/backend/mcp.js`: Dynamic merchant endpoint discovery (`/.well-known/ucp`).
- `src/agentic_commerce/backend/cart.js`: Cart MCP server interactions (`create_cart`, `get_cart`, `update_cart`, `cancel_cart`).
- `src/agentic_commerce/backend/checkout.js`: Checkout MCP server interactions (`create_checkout`, `update_checkout`, `complete_checkout`, `cancel_checkout`).
- `src/agentic_commerce/backend/orders.js`: Order MCP (`get_order`) and Order Webhook HMAC verification.
- `src/agentic_commerce/backend/ucp_demo.js`: End-to-end multi-merchant interactive CLI demo.
- `project_memory.md`: Complete protocol glossary, tool parameter specs, and operational learnings.
