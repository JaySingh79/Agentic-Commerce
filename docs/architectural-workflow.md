# Agentic Commerce — Architectural Workflow

> Stack: Python 3.11+ via `uv` / FastAPI + vanilla-JS (Gradio retired, retained as legacy) / Gemini 2.5 Flash / Shopify UCP + MCP + A2A + AP2 / Razorpay test-mode + Stripe fallback + simulated / OTel + Prometheus + Tempo + Grafana.
> Entrance (single port): FastAPI serves the HTTP API + static `web/` on `http://localhost:8010` (via `agentic_commerce.api.server:create_app` + `main`). Gradio (`app.py`, `src/agentic_commerce/ui/`) is legacy and not served.
> API surface: 24 paths in `openapi.json` (OpenAPI 3.1.0).

## 1. Layer Map (the one mental model)

```text
+------------------------------------------------------------------+
| DOMAIN (UI-independent, structured data only, no Markdown)        |
|  core/      : models.py, runtime.py, telemetry.py, profile.py     |
|  ucp/       : catalog.py, cart.py, checkout.py, order.py,         |
|               client.py (ShopifyUcpClient)                       |
|  a2a/       : protocol.py, buyer_agent.py, merchant_agent.py,     |
|               negotiation.py -> NegotiationResult                 |
|  ap2/       : mandate.py (AP2Engine, HMAC-SHA256), settlement.py  |
|  payments/  : settings.py, models.py, ledger.py, flow.py,         |
|               gateway.py, providers/{razorpay,stripe,             |
|               simulated,mcp}.py                                   |
+------------------------------------------------------------------+
                          | thin shims (stable imports)
                          v
+------------------------------------------------------------------+
| AGENT RUNTIME (backend/)                                          |
|  agent.py         : CommerceAgent + COMMERCE_SYSTEM_PROMPT        |
|  crew.py          : Concierge + CatalogScout + WebScout +         |
|                     Negotiator + Cashier + Analyst                |
|  tools.py         : LangChain @tool wrappers (Markdown for model |
|                     only, trace_tool_execution for progress)      |
|  analyst.py       : pick_best -> BestPick + ProductScore.missing  |
|  session.py       : CommerceSession, get_or_create_session        |
|  session_store.py : SQLite (.agentic_commerce/sessions.db,       |
|                     compose: /data/sessions.db shared volume)     |
|  session_graph.py : build_graph, to_compact_text (~19x fewer     |
|                     tokens than raw snapshot)                     |
|  ucp_client.py, web_search.py, model.py, mcp_testing.py           |
|  payments.py, ap2.py : re-export shims only                      |
+------------------------------------------------------------------+
                          | same objects, no conversion
                          v
+------------------------------------------------------------------+
| TRANSPORTS (api/ + web/ — single FastAPI port)                  |
|  api/server.py : FastAPI, StreamingResponse SSE, StaticFiles      |
|                  (`create_app(serve_frontend=True)` mounts        |
|                  `web/` at `/`; `main()` runs API + frontend      |
|                  together on `API_PORT 8010`)                     |
|  web/          : index.html + styles.css + app.js (1326 lines,    |
|                  reference SSE consumer)                          |
|  orchestrator.py : CommerceOrchestrator pipeline (non-chat path,  |
|                    UI-independent)                                |
|  LEGACY (retained, not served): app.py + ui/chat_engine.py,       |
|                  cards.py, app.py, theme.py (Gradio)              |
+------------------------------------------------------------------+
                          | sidecars
                          v
+------------------------------------------------------------------+
| EXTERNAL + INFRA                                                  |
|  Shopify: api.shopify.com/auth/access_token,                      |
|           catalog.shopify.com/api/ucp/mcp,                        |
|           {shop}/.well-known/ucp, {shop}/api/ucp/mcp              |
|  Razorpay remote MCP server (mcp.razorpay.com, streamable HTTP)   |
|  Tavily (else DuckDuckGo) for web search                          |
|  OTel Collector -> Tempo / Prometheus -> Grafana                  |
+------------------------------------------------------------------+
```

Rule from `README.md` + `api/server.py` docstring: domain functions return
JSON-shaped dicts/dataclasses. Only `backend/tools.py` wraps them in Markdown
for the LLM. API routes call domain functions directly and return the struct.

## 2. Process Topology (what runs where)

```text
Local (uv sync, single port):
  uv run python -m agentic_commerce.api.server  -> :8010 (API + web/index.html)

Docker (recommended):
  docker compose up -d --build
    api           : python -m agentic_commerce.api.server :8010
                    (serves /api/* + /openapi.json + web/ at /)
                    volumes: session-data:/data
                    env: AC_SESSION_DB=/data/sessions.db,
                         AC_PAYMENTS_DB=/data/payments.db,
                         RAZORPAY_MCP_BRIDGE=1
                    Razorpay payments (when RAZORPAY_MCP_BRIDGE=1) reach
                    Razorpay's hosted remote MCP server directly
                    (https://mcp.razorpay.com/mcp, HTTP Basic auth) — no
                    extra container, no docker.sock.
    LEGACY (not served): ui service (python app.py :7860, Gradio) is
                    retained in docker-compose.yml for reference only.
    otel-collector, tempo, prometheus, grafana : telemetry stack, defined in
                    docker-compose.telemetry.yml and folded into the single
                    `docker compose up -d --build` via docker-compose.yml's
                    top-level `include:` (Grafana :3000, Prometheus :9090,
                    OTLP :4318). Opt out with an explicit service list:
                    `docker compose up -d --build api ui`.
```

Single image (`Dockerfile`): `python:3.13-slim` + `uv` + `src/` + `web/` +
`openapi.json` (+ legacy `app.py` retained but not served). Default CMD is the
API (`python -m agentic_commerce.api.server`). Non-root `appuser` everywhere —
no Docker CLI/socket in the image, no root override in compose, since
`payments/providers/mcp.py` talks to Razorpay's remote MCP server over HTTP.
No secrets baked in — all via `.env` / `env_file`.

## 3. End-to-End Turn Workflow (chat path — the primary path)

```text
Step 1 — Shopper input (single frontend)
  web/app.js (fetch POST /api/chat/stream, SSE)
    + session id: localStorage ac_session_id + HttpOnly cookie ac_session
    + payload: { message, session_id }
  (Legacy Gradio ui/chat_engine.py path retired; see §7.)

Step 2 — Session resolve
  api/server.py -> backend/session.py:get_or_create_session()
    -> session_store.py (SQLite read)
    -> if missing: new_session_id()

Step 3 — Agent loop
  backend/agent.py:CommerceAgent.execute_stream (blocking generator)
    -> Starlette StreamingResponse drives it on worker thread
    -> tools use core/runtime.py:run_async (own background loop,
       never competes with uvicorn loop)
    -> COMMERCE_SYSTEM_PROMPT decides tool:
         search_products | get_product_details | search_web_products |
         pick_best_product | add_to_cart | checkout_cart |
         generate_ap2_mandate | negotiate_price | process_test_payment

Step 4 — Parallel crew fan-out (visible collaboration)
  backend/crew.py:CommerceCrew.run_discovery
    asyncio.gather(CatalogScout, WebScout)
      CatalogScout -> ucp/catalog.py -> ucp/client.py:search_catalog
                      -> Shopify Global Catalog MCP
      WebScout     -> backend/web_search.py:search_web
                      -> Tavily else DuckDuckGo -> WebResult[]
    Each specialist yields CrewEvent{agent,status,detail,payload,duration}
      -> SSE event type `crew` (renders live in web/; the retired Gradio
         UI discarded these per api/server.py:_stream_turn)

Step 5 — SSE stream back (the contract — see web/app.js)
  status | content | tool_call | tool_result | products | crew |
  artifact | done | error
  Emitted by CommerceAgent.execute_stream, consumed verbatim by web/app.js.
  Single-web-UI surfaces: agent timeline + answer + catalog band + web band +
               Analyst comparison drawer.

Step 6 — Persistence + observability (every turn)
  session_store.py: snapshot turn -> SQLite
  core/telemetry.py: trace_turn, trace_llm_call, trace_tool_execution,
                     record_llm_usage, estimate_tokens
    -> GET /api/telemetry/{session_id} (spans + token accounting)
    -> GET /api/session/{id}/graph (build_graph, compact text)
    -> OTLP export if OTEL_EXPORTER_OTLP_ENDPOINT set
```

## 4. Sub-workflows

### 4a. Discovery -> Best Pick (Analyst)

```text
POST /api/search/products  -> ucp/catalog.py (typed Product[])
POST /api/search/web       -> backend/web_search.py (WebResult[], no cart)
POST /api/discovery        -> both, source-emptiness reported separately
GET  /api/products/{id}    -> ucp catalog variant/option resolution
POST /api/analysis/best-pick -> backend/analyst.py:pick_best(need_text)
  Scores: rating + review volume + fabric-fit vs need + relative price +
          availability. Missing signals dropped + renormalized, never zero.
  Returns: BestPick{winner, scores[], reasoning, missing[]}
  Honesty invariants (UI must respect):
    - unrated product carries NO rating key, never 0 stars
    - ProductScore.missing surfaced as blind spots
    - web results get NO cart button (absence, not disabled)
    - review counts = popularity, never sales
```

### 4b. Cart + Checkout (UCP merchant path)

```text
POST /api/cart          -> ucp/cart.py:_to_cart + ucp/client.py:create_cart
                           (merchant Cart MCP at {shop}/api/ucp/mcp)
GET/PUT /api/cart/{id}  -> get_cart / full-replacement update_cart
POST /api/checkout      -> ucp/checkout.py:create_checkout/update/complete
Discovery of merchant endpoints: ucp/client.py + backend/mcp.js pattern
  via GET {shop}/.well-known/ucp
Non-chat equivalent: orchestrator.py:open_cart(merchant_domain, variant_id)
```

### 4c. Negotiation (A2A)

```text
POST /api/negotiate {product, budget_cents}
  -> a2a/buyer_agent.py (never exceeds budget)
   vs a2a/merchant_agent.py (sales policy)
  -> a2a/negotiation.py:negotiate() -> NegotiationResult{rounds[],
     transcript, agreed_price_cents, savings_cents}
  -> crew.py NEGOTIATOR specialist wraps it as CrewEvent
  -> orchestrator.py exposes `negotiate` seam (defaults to list price
     in Phase-5 pipeline, wired live in chat path)
```

### 4d. Mandate + Payment (AP2 + test-mode capture)

```text
POST /api/mandate {cart_id, amount_cents, currency, merchant, buyer}
  -> ap2/mandate.py:AP2Engine.create_payment_mandate()
  -> signed HMAC-SHA256 ticket {expiry, signature, scope}
  -> UI: live countdown + Verify + Copy-JSON + raw payload disclosure

POST /api/mandate/verify {mandate}
  -> distinguishes expired vs tampered, never collapses to invalid
  -> AC_PAYMENTS_STRICT=1 refuses placeholder key when CLIENT_SECRET unset

POST /api/payments/authorize {mandate}  (mandate-gated, single-use)
  -> payments/flow.py:authorize_payment (guards: signature, expiry,
     spending-limit, cart match, merchant match, single-use via ledger)
  -> payments/gateway.py: provider resolution Razorpay -> Stripe -> simulated
  -> providers/{razorpay,stripe,simulated,mcp}.py behind one protocol
  -> ledger.py: write attempt row BEFORE provider call; repeat
     idempotency_key returns stored result without re-calling provider
  -> UNKNOWN state (sent but unanswered) resolved ONLY by find_by_receipt,
     never by retry elsewhere (prevents double orders; Razorpay has no
     native idempotency header)

POST /api/payments/test {amount_cents, currency}  (mandate-free, for probes)
  -> same gateway, live-key guard: rzp_live_* refused -> simulated
  -> every receipt carries `live`; UI badge renders TEST/SIMULATED from it

MCP bridge (when RAZORPAY_MCP_BRIDGE=1):
  payments/providers/mcp.py: real MCP client (mcp.ClientSession over
  streamablehttp_client -> Razorpay's hosted remote MCP server,
  https://mcp.razorpay.com/mcp, HTTP Basic auth) -> initialize handshake ->
  tools/list assertion -> inputSchema-driven arg resolution -> call_tool
  with per-request timeout. Bridge failure RAISED, not degraded to REST
  (both create orders). Razorpay's recommended deployment path — no local
  container, no docker.sock. To add tools (payment links/settlements;
  create_refund/close_qr_code/create_instant_settlement are local-only,
  unavailable here): add call_tool mapper + hermetic test with fake
  streamablehttp_client/ClientSession (see tests/test_payments_mcp.py).

POST /api/payments/webhook/razorpay
  -> providers/razorpay.py:verify_webhook_signature (X-Razorpay-Signature)
  -> unset RAZORPAY_WEBHOOK_SECRET => 503 refuse
  -> ledger dedupe on webhook id

GET /api/payments/{idempotency_key} -> ledger lookup
```

### 4e. Session introspection

```text
POST /api/session -> new session
GET  /api/session/{id} -> snapshot
GET  /api/session/{id}/results -> products + picks
GET  /api/session/{id}/graph -> session_graph.py:build_graph
GET  /api/telemetry/{id} -> core/telemetry.py:get_latest_session_stats
GET  /api/health + GET /api/models + GET /api/examples
```

## 5. Configuration (env only, never baked in)

| Variable | Effect |
|---|---|
| `MODEL` | Gemini id (`backend/model.py`), default `gemini-2.5-flash` |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | LLM auth; chat fails without |
| `CLIENT_ID` / `CLIENT_SECRET` | Shopify OAuth + webhook HMAC + AP2 signing |
| `CATALOG_ID` | empty = Global Catalog |
| `RAZORPAY_KEY_ID` (`rzp_test_*` only) / `RAZORPAY_KEY_SECRET` | test gateway; live keys refused |
| `STRIPE_SECRET_KEY` (`sk_test_*` only) | fallback |
| `RAZORPAY_MCP_BRIDGE`, `RAZORPAY_MCP_URL` | remote MCP switch + endpoint override |
| `TAVILY_API_KEY` | else DuckDuckGo |
| `RAZORPAY_WEBHOOK_SECRET` | unset => webhook route 503 |
| `AC_PAYMENTS_PERSIST` / `AC_PAYMENTS_DB` | ledger on + path |
| `AC_SESSION_PERSIST` / `AC_SESSION_DB` | snapshots on + path |
| `HOST` / `API_PORT` | default `127.0.0.1` / `8010` (single FastAPI port; legacy Gradio `PORT 7860` retired) |
| `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_SDK_DISABLED` | tracing export |

## 6. Verification (definition of done)

```text
uv run ruff check src tests        # E,F,I,UP,B,SIM, line-length 100
uv run pytest -q                   # 265 passed, 26 files, ~65s
uv run pytest tests/test_payments.py tests/test_payments_mcp.py -q
curl http://localhost:8010/openapi.json | head -c 200
curl -o /dev/null -w "%{http_code}" http://localhost:8010/  # -> 200 (web/index.html)
Browser JS covered from pytest via tests/js/markdown_harness.mjs (node vm).
```

Loop: trace root cause -> minimal anchored edit -> ruff + pytest ->
clean exit required. No swallowed exceptions, no dummy fallbacks,
no deleted tests (contract change must be explicit).

## 7. Legacy Gradio (retained, not served)

```text
Retained files: app.py (launch_chat_app, PORT 7860) +
  src/agentic_commerce/ui/{app.py, chat_engine.py, cards.py, theme.py}
Status: retired. Not started locally, not required in Docker, not part of
  the single-port topology. Kept for reference only.
Active path: src/agentic_commerce/api/server.py:create_app
  (serve_frontend=True mounts web/ at /) + main() (API + frontend on
  API_PORT 8010). orchestrator.py is UI-independent and unchanged.
Drift to be aware of: docker-compose.yml still defines a ui:7860 service,
  Dockerfile header mentions Gradio, and README entrances may still list
  :7860 — all marked LEGACY where they appear.
```
