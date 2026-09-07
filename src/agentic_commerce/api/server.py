"""HTTP + SSE transport over the existing agent stack.

Every route here is a thin adapter: it calls a function that already exists in the
package and returns the structured dict that function already produces. The domain
layer needs no changes to serve a new frontend, because only the LangChain *tool*
layer wraps results in Markdown for the model to read — the objects underneath
(``WebResult``, ``BestPick``, ``PaymentResult``, ``NegotiationResult``, UCP product
dicts, signed AP2 mandates) are already JSON-shaped.

Threading: `CommerceAgent.execute_stream` is a blocking generator, so it is served
through a `StreamingResponse`, which drives sync iterators on a worker
thread. Tools reach the network through :func:`agentic_commerce.core.runtime.run_async`,
which owns its own background event loop, so nothing here competes with uvicorn's.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentic_commerce.ap2.mandate import AP2Engine
from agentic_commerce.backend.agent import CommerceAgent
from agentic_commerce.backend.analyst import pick_best
from agentic_commerce.backend.crew import CommerceCrew
from agentic_commerce.backend.payments import process_payment, select_provider
from agentic_commerce.backend.session import (
    DEFAULT_SESSION_ID,
    get_or_create_session,
    new_session_id,
)
from agentic_commerce.backend.session_graph import (
    build_graph,
    estimate_tokens,
    to_compact_text,
)
from agentic_commerce.backend.web_search import search_web
from agentic_commerce.core.runtime import run_async
from agentic_commerce.core.telemetry import get_latest_session_stats
from agentic_commerce.payments.flow import (
    MandateExpiredError,
    MandateScopeError,
    MandateTamperedError,
    authorize_payment,
)
from agentic_commerce.ucp.client import ShopifyUcpClient
from agentic_commerce.payments.ledger import MandateAlreadyUsedError, get_ledger
from agentic_commerce.payments.providers.razorpay import verify_webhook_signature
from agentic_commerce.payments.settings import PaymentSettings

WEB_ROOT = Path(__file__).resolve().parents[3] / "web"

SESSION_COOKIE = "ac_session"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30

# Starter prompts shown on an empty screen. The static set is the fallback only:
# hard-coded examples go stale against a live catalog and a first click that returns
# nothing is the worst possible first impression, so the real list is seeded from
# products the catalog actually carries right now.
EXAMPLE_SEED_QUERY = "shirt"
EXAMPLE_TTL_SECONDS = 600
FALLBACK_EXAMPLES = [
    "Breathable running shirt under $60",
    "Warm merino base layer for winter runs",
    "Soft cotton t-shirt for sensitive skin",
    "Which of these should I buy?",
]
_example_cache: dict[str, Any] = {"at": 0.0, "payload": None}

_client = ShopifyUcpClient()
_crew = CommerceCrew(client=_client)
_ap2 = AP2Engine()


# --------------------------------------------------------------------- models


class ChatRequest(BaseModel):
    """A shopper turn."""

    message: str = Field(min_length=1)
    session_id: str | None = None
    model: str | None = None
    temperature: float = Field(default=0.4, ge=0.0, le=1.0)
    system_prompt: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)


class CatalogSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = None
    price_min: int | None = None
    price_max: int | None = None
    ships_to_country: str = "US"
    limit: int = Field(default=10, ge=1, le=50)


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = None
    limit: int = Field(default=5, ge=1, le=20)
    with_images: bool = True


class DiscoveryRequest(BaseModel):
    query: str = Field(min_length=1)
    session_id: str | None = None
    max_price: float | None = None
    limit: int = Field(default=5, ge=1, le=20)


class BestPickRequest(BaseModel):
    session_id: str | None = None
    products: list[dict[str, Any]] | None = None
    need: str = ""


class NegotiationRequest(BaseModel):
    item: str
    list_price_cents: int = Field(ge=1)
    budget_cents: int = Field(ge=1)
    target_cents: int | None = None
    merchant_name: str = "MerchantAgent"
    tier: str = "standard"
    session_id: str | None = None


class CreateCartRequest(BaseModel):
    merchant_domain: str
    variant_id: str
    quantity: int = Field(default=1, ge=1)
    country: str = "US"
    session_id: str | None = None


class CreateCheckoutRequest(BaseModel):
    merchant_domain: str
    cart_id: str
    cart_url: str | None = None
    buyer_email: str = "shopper@example.com"
    session_id: str | None = None


class CreateMandateRequest(BaseModel):
    cart_id: str
    amount_cents: int = Field(ge=1)
    currency: str = "USD"
    merchant_domain: str = "shopify.com"
    max_duration_seconds: int = 3600
    buyer_id: str = "user_default"
    session_id: str | None = None


class PaymentRequest(BaseModel):
    amount_cents: int = Field(ge=1)
    currency: str = "USD"
    receipt: str | None = None
    session_id: str | None = None


class AuthorizePaymentRequest(BaseModel):
    """A charge authorized by a mandate the caller must present in full.

    The mandate travels with the request rather than being looked up by id: the
    signature is over the mandate's contents, so verifying what the caller actually
    holds is the only check that means anything.
    """

    mandate: dict[str, Any]
    session_id: str | None = None
    amount_cents: int | None = Field(default=None, ge=1)
    currency: str | None = None
    expected_cart_id: str | None = None
    expected_merchant: str | None = None


# -------------------------------------------------------------------- helpers


def _examples_from(products: list[dict[str, Any]]) -> list[str]:
    """Turns catalog titles into starter prompts a first click can actually satisfy.

    Titles are truncated rather than used whole: a merchant title like
    "Unisex Cotton Tee - Black / XL / 2-Pack" is a SKU, not a query, and pasting it
    into the composer would search for one exact listing.
    """
    prompts: list[str] = []
    for product in products:
        title = " ".join(str(product.get("title") or "").split())
        if not title:
            continue
        head = " ".join(title.replace("/", " ").split()[:4]).strip(" -–—,")
        if len(head) < 4:
            continue
        prompt = f"Find a {head}"
        if prompt not in prompts:
            prompts.append(prompt)
        if len(prompts) == 3:
            break

    if not prompts:
        return []
    # The comparison prompt needs no catalog knowledge and exercises the Analyst.
    prompts.append("Which of these should I buy?")
    return prompts


def _sse(payload: dict[str, Any]) -> str:
    """Frames one event for the ``text/event-stream`` wire format."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


def session_snapshot(session_id: str) -> dict[str, Any]:
    """Everything the UI renders outside the transcript, in one payload."""
    session = get_or_create_session(session_id)
    return {
        "session_id": session_id,
        "last_searched_products": session.last_searched_products,
        "last_web_results": session.last_web_results,
        "active_product": session.active_product,
        "active_cart": session.active_cart,
        "active_checkout": session.active_checkout,
        "active_mandate": session.active_mandate,
        "active_negotiation": session.active_negotiation,
        "active_payment": session.active_payment,
        "best_pick": session.best_pick,
        "crew_events": session.last_crew_events,
    }


def _stream_turn(request: ChatRequest, session_id: str) -> Iterator[str]:
    """Adapts the agent's event generator to SSE frames.

    Crew events recorded during the turn are replayed as `crew` events so the client
    can render which specialists ran, how long each took, and which failed — data the
    crew produces today and the Gradio UI discards.

    Only events produced *by this turn* are forwarded. The session still holds the
    previous turn's list, and `update_crew_events` replaces that list wholesale
    rather than appending, so progress is tracked by the list's identity plus an
    index: a new list means a fresh discovery run and the index restarts. Counting
    alone replayed the last search's scouts onto an unrelated question, complete
    with timings longer than the turn itself.
    """
    agent = CommerceAgent(
        session_id=session_id,
        model_name=request.model or None,
        temperature=request.temperature,
    )
    session = get_or_create_session(session_id)
    tracked_events = session.last_crew_events
    seen_events = len(tracked_events)
    # Identity, not equality: a mandate re-issued for the same cart and amount is a
    # different authorization and deserves its own ticket in the transcript.
    last_mandate = session.active_mandate
    last_payment = session.active_payment
    started = time.perf_counter()

    try:
        kwargs: dict[str, Any] = {"message": request.message, "history": request.history}
        if request.system_prompt:
            kwargs["system_prompt"] = request.system_prompt

        for event in agent.execute_stream(**kwargs):
            yield _sse(event)

            # The crew writes its events into the session as tools run; forward any
            # that appeared since the last check.
            events = session.last_crew_events
            if events is not tracked_events:
                tracked_events = events
                seen_events = 0
            if len(events) > seen_events:
                for record in events[seen_events:]:
                    yield _sse({"type": "crew", **record})
                seen_events = len(events)

            # Mandates and receipts are the two things in a turn worth keeping as an
            # object rather than a sentence, so they are emitted as artifacts the
            # client can render inline and act on.
            if session.active_mandate is not last_mandate:
                last_mandate = session.active_mandate
                if last_mandate:
                    yield _sse({"type": "artifact", "kind": "mandate", "data": last_mandate})
            if session.active_payment is not last_payment:
                last_payment = session.active_payment
                if last_payment:
                    yield _sse({"type": "artifact", "kind": "payment", "data": last_payment})
    except Exception as exc:  # noqa: BLE001 - a stream must end with a reportable event
        yield _sse({"type": "error", "error": "turn_failed", "message": str(exc)})
        return

    yield _sse(
        {
            "type": "done",
            "session_id": session_id,
            "elapsed": round(time.perf_counter() - started, 3),
            "stats": get_latest_session_stats(session_id),
        }
    )


# ------------------------------------------------------------------------ app


def create_app(serve_frontend: bool = True) -> FastAPI:
    """Builds the API application.

    Args:
        serve_frontend: Mount ``web/`` at the root. Disabled in tests so the API can
            be exercised without the static bundle present.
    """
    app = FastAPI(
        title="Agentic Commerce API",
        version="1.0.0",
        description="HTTP + SSE transport over the agent stack. See openapi.json.",
    )

    # Development convenience: a separately-served frontend needs cross-origin access.
    # Credentials are deliberately not allowed, because the session id is a bearer-like
    # capability and must not ride on ambient cookies from arbitrary origins.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/health")
    def get_health() -> dict[str, Any]:
        """Liveness plus which providers are actually configured."""
        import os

        return {
            "status": "ok",
            "payment_provider": select_provider(),
            "web_search_provider": "tavily" if os.getenv("TAVILY_API_KEY") else "duckduckgo",
            "model": os.getenv("MODEL", "gemini-2.5-flash"),
        }

    @app.get("/api/examples")
    def list_examples() -> dict[str, Any]:
        """Starter prompts, seeded from live catalog titles when the catalog answers.

        ``source`` tells the client which it got: a UI that cannot distinguish a live
        sample from the canned fallback would silently show stale suggestions whenever
        the catalog is down.
        """
        now = time.time()
        cached = _example_cache["payload"]
        if cached and now - float(_example_cache["at"]) < EXAMPLE_TTL_SECONDS:
            return cached

        try:
            products = run_async(_client.search_catalog(query=EXAMPLE_SEED_QUERY, limit=8))
        except Exception:  # noqa: BLE001 - an offline catalog falls back, it does not 500
            products = []

        seeded = _examples_from(products)
        payload = {
            "examples": seeded or FALLBACK_EXAMPLES,
            "source": "catalog" if seeded else "fallback",
        }
        _example_cache.update({"at": now, "payload": payload})
        return payload

    @app.get("/api/models")
    def list_models() -> dict[str, Any]:
        from agentic_commerce.ui.chat_engine import DEFAULT_MODEL

        models = list(dict.fromkeys([DEFAULT_MODEL, "gemini-2.5-flash", "gemini-2.5-pro"]))
        return {"models": models, "default": models[0]}

    @app.post("/api/chat/stream")
    def stream_chat(request: ChatRequest, http_request: Request) -> StreamingResponse:
        """Streams one agent turn as Server-Sent Events.

        Session resolution, in order: the body, then the ``ac_session`` cookie, then a
        freshly minted id. The cookie is the recovery path for a client whose local
        storage was cleared; it is ``HttpOnly`` because the id is a capability token,
        and the client learns its own id from ``X-Session-Id`` instead.
        """
        session_id = request.session_id or http_request.cookies.get(SESSION_COOKIE)
        session_id = session_id or new_session_id()
        response = StreamingResponse(
            _stream_turn(request, session_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                # Stops reverse proxies buffering the stream into one chunk.
                "X-Accel-Buffering": "no",
                "X-Session-Id": session_id,
            },
        )
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/api/session", status_code=201)
    def create_session(response: Response) -> dict[str, Any]:
        """Mints a fresh session and points the cookie at it.

        The old session is not deleted — it stays in the durable store under its
        own id, so "start over" costs the shopper nothing they might want back.
        """
        session_id = new_session_id()
        get_or_create_session(session_id)
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            max_age=SESSION_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        return {"session_id": session_id}

    @app.get("/api/session/{session_id}/graph")
    def get_session_graph(session_id: str, format: str = "text") -> dict[str, Any]:
        """The session as a knowledge graph, for handing context to another agent.

        Shipping a transcript or the raw snapshot spends most of its tokens on
        prose and repeated JSON keys. The graph states each fact once with a short
        id and names the relationships that matter — which merchant sells what,
        which cart a mandate authorizes, which mandate a payment settles.

        ``format=text`` returns the line form meant for a prompt; ``format=json``
        returns the nodes and edges. Both carry the same facts. Token counts are
        character-based estimates, not a tokenizer's output.
        """
        if format not in {"text", "json"}:
            raise HTTPException(status_code=422, detail="format must be 'text' or 'json'")

        session = get_or_create_session(session_id)
        graph = build_graph(session)
        text = to_compact_text(graph)
        raw = json.dumps(session_snapshot(session_id), default=str)

        payload: dict[str, Any] = {
            "session_id": session_id,
            "text": text,
            "estimated_tokens": estimate_tokens(text),
            "estimated_tokens_raw_snapshot": estimate_tokens(raw),
        }
        if format == "json":
            payload["graph"] = graph
        return payload

    @app.get("/api/session/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        return session_snapshot(session_id)

    @app.delete("/api/session/{session_id}/results", status_code=204)
    def clear_session_results(session_id: str) -> Response:
        get_or_create_session(session_id).clear_search_results()
        return Response(status_code=204)

    @app.post("/api/search/products")
    def search_catalog(request: CatalogSearchRequest) -> dict[str, Any]:
        """Catalog search with no model in the loop, for filter and sort refinements."""
        try:
            products = run_async(
                _client.search_catalog(
                    query=request.query,
                    price_min=request.price_min,
                    price_max=request.price_max,
                    ships_to_country=request.ships_to_country,
                    limit=request.limit,
                )
            )
        except Exception as exc:  # noqa: BLE001 - upstream failure is a client-visible state
            raise HTTPException(status_code=502, detail=f"Catalog unavailable: {exc}") from exc

        if request.session_id:
            get_or_create_session(request.session_id).update_search_results(products)
        return {"products": products}

    @app.post("/api/search/web")
    def search_web_listings(request: WebSearchRequest) -> dict[str, Any]:
        try:
            results = run_async(
                search_web(request.query, limit=request.limit, with_images=request.with_images)
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Web search failed: {exc}") from exc

        payload = [r.as_dict() for r in results]
        if request.session_id:
            get_or_create_session(request.session_id).update_web_results(payload)
        return {"results": payload}

    @app.post("/api/discovery")
    def run_discovery(request: DiscoveryRequest) -> dict[str, Any]:
        """Both scouts in parallel, with the events that record what each one did."""
        report = _crew.run_discovery(
            request.query, max_price=request.max_price, limit=request.limit
        )
        web = [r.as_dict() for r in report.web_results]

        if request.session_id:
            session = get_or_create_session(request.session_id)
            session.update_search_results(report.catalog_products)
            session.update_web_results(web)
            session.update_crew_events([e.as_dict() for e in report.events])

        return {
            "catalog_products": report.catalog_products,
            "web_results": web,
            "events": [e.as_dict() for e in report.events],
            "has_catalog_hits": report.has_catalog_hits,
        }

    @app.get("/api/products/{product_id:path}")
    def get_product(product_id: str, selected: list[str] | None = None) -> dict[str, Any]:
        """Full product detail, optionally filtered to a chosen variant."""
        chosen = None
        if selected:
            chosen = []
            for pair in selected:
                name, _, label = pair.partition(":")
                if name and label:
                    chosen.append({"name": name, "label": label})

        try:
            product = run_async(_client.get_product(product_id, selected=chosen))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if not product:
            raise HTTPException(status_code=404, detail="No such product")
        return product

    @app.post("/api/analysis/best-pick")
    def best_pick(request: BestPickRequest) -> dict[str, Any]:
        """Ranks candidates and names a winner, with its blind spots attached."""
        products = request.products
        session = get_or_create_session(request.session_id or DEFAULT_SESSION_ID)
        if products is None:
            products = session.last_searched_products
        if not products:
            raise HTTPException(
                status_code=409, detail="No candidates to compare — run a search first."
            )

        verdict = pick_best(products, request.need).as_dict()
        if request.session_id:
            session.update_best_pick(verdict)
        return verdict

    @app.post("/api/negotiate")
    def negotiate(request: NegotiationRequest) -> dict[str, Any]:
        result = _crew.run_negotiation(
            item=request.item,
            list_price_cents=request.list_price_cents,
            budget_cents=request.budget_cents,
            target_cents=request.target_cents,
            merchant_name=request.merchant_name,
            tier=request.tier,
        ).as_dict()
        if request.session_id:
            get_or_create_session(request.session_id).update_negotiation(result)
        return result

    @app.post("/api/cart", status_code=201)
    def create_cart(request: CreateCartRequest) -> dict[str, Any]:
        try:
            cart = run_async(
                _client.create_cart(
                    merchant_domain=request.merchant_domain,
                    variant_id=request.variant_id,
                    quantity=request.quantity,
                    country=request.country,
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        cart["merchant_domain"] = request.merchant_domain
        if request.session_id:
            get_or_create_session(request.session_id).update_cart(cart)
        return cart

    @app.get("/api/cart/{cart_id:path}")
    def get_cart(cart_id: str, merchant_domain: str) -> dict[str, Any]:
        try:
            return run_async(_client.get_cart(merchant_domain, cart_id))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/checkout")
    def create_checkout(request: CreateCheckoutRequest) -> dict[str, Any]:
        try:
            checkout = run_async(
                _client.create_checkout(
                    merchant_domain=request.merchant_domain,
                    cart_id=request.cart_id,
                    cart_url=request.cart_url,
                    buyer_email=request.buyer_email,
                )
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        if request.session_id:
            get_or_create_session(request.session_id).update_checkout(checkout)
        return checkout

    @app.post("/api/mandate", status_code=201)
    def create_mandate(request: CreateMandateRequest) -> dict[str, Any]:
        mandate = _ap2.create_payment_mandate(
            cart_id=request.cart_id,
            amount_cents=request.amount_cents,
            currency=request.currency,
            merchant_domain=request.merchant_domain,
            max_duration_seconds=request.max_duration_seconds,
            buyer_id=request.buyer_id,
        )
        if request.session_id:
            get_or_create_session(request.session_id).update_mandate(mandate)
        return mandate

    @app.post("/api/mandate/verify")
    def verify_mandate(mandate: dict[str, Any]) -> dict[str, Any]:
        """Re-checks a mandate's HMAC signature and expiry.

        The client holds a mandate it was handed over the wire; this is how it can
        prove that what it is showing is still the authorization the server signed.
        The failure is named — expired and tampered are different problems, and
        collapsing them to "invalid" tells the shopper nothing.
        """
        expires_at = int(mandate.get("expires_at") or 0)
        now = int(time.time())
        valid = _ap2.verify_mandate(mandate)

        if valid:
            reason = "Signature matches and the authorization is still in force."
        elif expires_at and expires_at < now:
            reason = "This authorization has expired."
        else:
            reason = "The signature does not match this mandate's contents."

        return {
            "valid": valid,
            "reason": reason,
            "expires_at": expires_at or None,
            "seconds_remaining": max(expires_at - now, 0) if expires_at else None,
        }

    @app.post("/api/payments/test")
    def process_test_payment(request: PaymentRequest) -> dict[str, Any]:
        """Test-mode capture only.

        ``backend.payments`` refuses any credential that is not a test key, so this
        cannot move real money. ``live`` is on the response because the client's
        rendering must follow the payload rather than an assumption.
        """
        try:
            result = run_async(
                process_payment(request.amount_cents, request.currency, request.receipt)
            ).as_dict()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if request.session_id:
            get_or_create_session(request.session_id).update_payment(result)
        return result

    @app.post("/api/payments/authorize")
    def authorize_payment_route(request: AuthorizePaymentRequest) -> dict[str, Any]:
        """Charges against an AP2 mandate, through every guard in the payment flow.

        This is the mandate-gated path: signature, expiry, spending limit, currency,
        cart, merchant and single-use are all checked before a provider is contacted,
        and each refusal is reported as itself rather than as a generic "invalid".
        ``/api/payments/test`` remains the mandate-free test endpoint.
        """
        session_id = request.session_id or ""
        try:
            result = run_async(
                authorize_payment(
                    request.mandate,
                    session_id=session_id,
                    amount_cents=request.amount_cents,
                    currency=request.currency,
                    expected_cart_id=request.expected_cart_id,
                    expected_merchant=request.expected_merchant,
                    engine=_ap2,
                )
            ).as_dict()
        except MandateAlreadyUsedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (MandateExpiredError, MandateTamperedError, MandateScopeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if session_id:
            get_or_create_session(session_id).update_payment(result)
        return result

    @app.get("/api/payments/{idempotency_key:path}")
    def get_payment(idempotency_key: str) -> dict[str, Any]:
        """Returns a recorded payment attempt, so a client can resolve an unknown.

        A payment whose outcome was never confirmed is not a failure and not a success;
        it is ``unknown`` until a webhook or a lookup says otherwise, and this is how a
        client asks.
        """
        attempt = get_ledger().get(idempotency_key)
        if attempt is None:
            raise HTTPException(status_code=404, detail="unknown payment")
        return {
            "idempotency_key": attempt["idempotency_key"],
            "state": attempt["state"],
            "provider": attempt["provider"],
            "amount_cents": attempt["amount_cents"],
            "currency": attempt["currency"],
            "reference_id": attempt["reference_id"] or None,
            "mandate_id": attempt["mandate_id"] or None,
            "result": attempt["result"],
        }

    @app.post("/api/payments/webhook/razorpay")
    async def razorpay_webhook(http_request: Request) -> dict[str, Any]:
        """Accepts a Razorpay webhook after verifying it against the raw body.

        Terminal payment status is something only the provider knows; a create-order
        response says an order exists, not that it was paid. The signature is checked
        over the exact bytes received (re-serializing would change them), events are
        deduplicated by id, and an unconfigured secret is a refusal rather than an
        open door.
        """
        settings = PaymentSettings.load()
        if not settings.razorpay_webhook_secret:
            raise HTTPException(
                status_code=503, detail="RAZORPAY_WEBHOOK_SECRET is not configured"
            )

        raw = await http_request.body()
        signature = http_request.headers.get("X-Razorpay-Signature", "")
        if not verify_webhook_signature(raw, signature, settings.razorpay_webhook_secret):
            raise HTTPException(status_code=401, detail="signature verification failed")

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="unparseable webhook body") from exc

        event_id = str(
            http_request.headers.get("X-Razorpay-Event-Id")
            or payload.get("id")
            or payload.get("event")
            or ""
        )
        if not event_id:
            raise HTTPException(status_code=400, detail="webhook carries no event id")

        fresh = get_ledger().record_event(event_id, "razorpay", payload)
        return {"received": True, "event_id": event_id, "duplicate": not fresh}

    @app.get("/api/telemetry/{session_id}")
    def get_telemetry(session_id: str) -> dict[str, Any]:
        return get_latest_session_stats(session_id)

    if serve_frontend and WEB_ROOT.is_dir():

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_ROOT / "index.html")

        app.mount("/", StaticFiles(directory=str(WEB_ROOT)), name="web")

    return app


app = create_app()


def main() -> None:
    """Runs the API and frontend on one port."""
    import os

    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8010")),
        log_level="info",
    )


if __name__ == "__main__":
    main()
