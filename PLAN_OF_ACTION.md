# 🛒 Agentic Commerce Prototype: Plan of Action & Executable Roadmap
### Based on the Universal Commerce Protocol (UCP) Standard (Co-developed by Shopify & Google)

This document defines the comprehensive architecture, protocol specifications, implementation phases, and verification criteria for building a production-ready **Agentic Commerce Prototype** integrating **Shopify Universal Commerce Protocol (UCP)**, **Model Context Protocol (MCP)**, **Agent-to-Agent (A2A)** negotiation, and **Agentic Payment Protocol (AP2)** verifiable mandates.

---

## 🏛️ 1. Architecture & Protocol Layering

```
+-----------------------------------------------------------------------------------------+
|                                 USER INTERFACE LAYER                                    |
|            Gradio Interactive Chatbot • Live Event Stream • HITL Authorizations          |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
|                  UCP FOUNDATION: AGENT IDENTITY & CAPABILITY NEGOTIATION                |
|  • Well-Known Agent Profile (`/.well-known/ucp-profile` / `agent.yaml`)                 |
|  • Trust Tiers: Tier 1 (Discovery) ➔ Tier 2 (Cart & Handoff) ➔ Tier 3 (Direct AP2 Buy)   |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
|                         STEP 1: UCP CATALOG & DISCOVERY (MCP)                           |
|  • Global Catalog MCP (`catalog_search` across all merchant catalogs)                   |
|  • Storefront Catalog MCP (`get_product`, `get_variant_details` scoped to merchant)     |
|  • Shopify Standard GIDs (`gid://shopify/Product/...`, `gid://shopify/ProductVariant/...`)  |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
|                         STEP 2: A2A MULTI-AGENT NEGOTIATION                             |
|  • Autonomous Buyer Agent (User proxy, budget limits, discount goals)                   |
|  • Merchant Sales Agent (Inventory rules, margin floors, bulk tier discount curves)     |
|  • Structured Turn-Based Negotiation (`RFQ` ➔ `OFFER` ➔ `COUNTER` ➔ `ACCEPT`/`REJECT`) |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
|                    STEP 3 & 4: UNIVERSAL CART, CHECKOUT & AP2 MANDATES                  |
|  • Universal Cart MCP (`cart_create`, `cart_update`, line items, multi-merchant)        |
|  • Checkout MCP (`checkout_create`, `checkout_update`, `continue_url` escalation)       |
|  • AP2 Payment Mandate Engine (SHA-256 HMAC cryptographic signing, tamper detection)     |
|  • Explicit Human-in-the-Loop (HITL) 1-Click User Authorization Card                    |
|  • Checkout Completion (`checkout_complete` with signed mandate ➔ Mock Ledger)          |
+-----------------------------------------------------------------------------------------+
                                             │
                                             ▼
+-----------------------------------------------------------------------------------------+
|                         STEP 5: UCP ORDER LIFECYCLE & MONITORING                        |
|  • Order MCP (`get_order` by `gid://shopify/Order/...`, financial & fulfillment status) |
|  • Order Webhooks & Lifecycle Event Dispatcher                                          |
+-----------------------------------------------------------------------------------------+
```

---

## 🎯 2. Deliverables by Implementation Phase

### 🏁 Phase 1: Core Domain Schemas & UCP Profile
- [ ] Define Pydantic models in `src/agentic_commerce/core/models.py`:
  - `UCPProfile`, `TrustTier`
  - `ProductVariant`, `Product`, `MerchantSeller`
  - `CartLineItem`, `UCPCart`
  - `UCPCheckout`, `CheckoutStatus`
  - `AP2PaymentMandate`, `MandateStatus`
  - `UCPOrder`, `OrderStatus`, `OrderEvent`
- [ ] Implement UCP Agent Profile & Capability Negotiation in `src/agentic_commerce/core/profile.py`.

### 🔍 Phase 2: UCP Catalog, Discovery & Universal Cart MCP
- [ ] Implement multi-merchant catalog database in `src/agentic_commerce/ucp/catalog.py`:
  - Multi-brand catalog (AudioGear, SprintAthletics, ByteTech, HomeComfort).
  - MCP Tools: `catalog_search`, `get_product`, `get_variant_details`.
- [ ] Implement Universal Cart MCP in `src/agentic_commerce/ucp/cart.py`:
  - `cart_create`, `cart_update`, line-item management, tax/totals estimation, and `continue_url` generation.

### 🤝 Phase 3: Layer 2 – A2A Multi-Agent Negotiation
- [ ] Implement A2A structured message protocol in `src/agentic_commerce/a2a/protocol.py`.
- [ ] Build **Merchant Sales Agent** in `src/agentic_commerce/a2a/merchant_agent.py`:
  - Enforces margin floor protection.
  - Dynamically calculates tier discounts (e.g., bulk discount on >= 2 units).
- [ ] Build **Buyer Agent** in `src/agentic_commerce/a2a/buyer_agent.py`:
  - Formulates RFQs and negotiates counter-offers autonomously within buyer's ceiling.

### 💳 Phase 4: UCP Checkout, AP2 Mandate Engine & Settlement
- [ ] Implement Checkout MCP in `src/agentic_commerce/ucp/checkout.py`:
  - `checkout_create`: Converts cart to checkout session.
  - `checkout_update`: Applies shipping address and customer details.
  - `checkout_complete`: Finalizes order for trusted agents with signed payment tokens.
  - `continue_url`: Fallback / escalation handoff for browser payment.
- [ ] Implement AP2 Mandate Engine in `src/agentic_commerce/ap2/mandate.py`:
  - Creates immutable, SHA-256 HMAC cryptographically signed mandate.
  - Strict validation of budget ceiling, merchant domain, expiry, and signature.
- [ ] Implement Mock Settlement Gateway in `src/agentic_commerce/ap2/settlement.py`.
- [ ] Implement Order MCP & Webhook Dispatcher in `src/agentic_commerce/ucp/order.py` (`get_order`).

### 🖥️ Phase 5: Interactive Gradio UI & Live Telemetry
- [ ] Implement master pipeline orchestrator in `src/agentic_commerce/orchestrator.py`.
- [ ] Upgrade `ChatEngine` in `src/agentic_commerce/ui/chat_engine.py` to stream live events:
  - 🔍 **UCP Catalog Search Results**
  - 🤝 **Live A2A Negotiation Dialogue**
  - 🛒 **Universal Cart & Checkout Card**
  - 💳 **AP2 Mandate Card with 1-Click Approval**
  - 📦 **Order Tracking Card (`gid://shopify/Order/...`)**

---

## 🧪 3. Verification & Testing Matrix

| Test Suite | Scope | Command |
| :--- | :--- | :--- |
| **UCP Catalog & Discovery** | Global search, variant lookup, GIDs | `uv run pytest tests/test_ucp_catalog.py` |
| **UCP Cart & Checkout** | Universal Cart lifecycle, totals, handoff URL | `uv run pytest tests/test_ucp_cart_checkout.py` |
| **A2A Negotiation** | Multi-agent dialogue, margin rules, floor protection | `uv run pytest tests/test_a2a_negotiation.py` |
| **AP2 Mandates & Settlement** | HMAC cryptographic signatures, tamper detection, settlement | `uv run pytest tests/test_ap2_mandate.py` |
| **Order MCP & Webhooks** | `get_order`, lifecycle status changes | `uv run pytest tests/test_ucp_order.py` |
| **Full E2E Orchestration** | Discovery ➔ A2A ➔ Cart ➔ AP2 ➔ Order | `uv run pytest tests/test_e2e_flow.py` |
| **Code Quality & Typing** | Ruff linting and type contracts | `uv run ruff check src/ tests/` |

---

## 🚀 4. Execution Commands

1. **Install and Sync Dependencies:**
   ```bash
   uv sync
   ```
2. **Execute Full Test Suite:**
   ```bash
   uv run pytest -v
   ```
3. **Launch the Interactive Agentic Commerce Hub:**
   ```bash
   uv run python app.py
   ```
4. **Access Web Interface:** `http://localhost:7860`
