"""Tests for the HTTP + SSE transport.

These assert the contract a frontend depends on: the SSE event vocabulary, the
session snapshot shape, and the honesty invariants that must survive the trip over
the wire (test-mode payments, the Analyst's unscored criteria, non-purchasable web
results).
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentic_commerce.api import server as api
from agentic_commerce.backend.session import get_or_create_session


@pytest.fixture
def client() -> TestClient:
    """A client with the static frontend unmounted."""
    return TestClient(api.create_app(serve_frontend=False))


@pytest.fixture(autouse=True)
def no_live_payments(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "STRIPE_SECRET_KEY", "STRIPE_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def sse_events(body: str) -> list[dict[str, Any]]:
    """Parses an SSE response body into its JSON payloads."""
    return [
        json.loads(line[len("data: ") :])
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_health_reports_the_providers_actually_configured(client: TestClient):
    payload = client.get("/api/health").json()
    assert payload["status"] == "ok"
    assert payload["payment_provider"] == "simulated"
    assert payload["web_search_provider"] in {"tavily", "duckduckgo"}


def test_models_lists_the_default_first(client: TestClient):
    payload = client.get("/api/models").json()
    assert payload["models"][0] == payload["default"]


def test_session_ids_are_unguessable():
    """The id is a capability token — anyone holding it can read the cart."""
    ids = {api.new_session_id() for _ in range(50)}
    assert len(ids) == 50
    assert all(len(i) > 20 for i in ids)


def test_session_snapshot_carries_every_surface(client: TestClient):
    session = get_or_create_session("api_snapshot_session")
    session.update_search_results([{"id": "gid://1", "title": "Tee"}])
    session.update_crew_events([{"agent": "CatalogScout", "status": "done", "detail": "1 match"}])

    payload = client.get("/api/session/api_snapshot_session").json()

    assert payload["last_searched_products"][0]["title"] == "Tee"
    assert payload["crew_events"][0]["agent"] == "CatalogScout"
    for key in ("active_cart", "best_pick", "active_payment", "last_web_results"):
        assert key in payload


def test_clearing_results_empties_every_product_source(client: TestClient):
    session = get_or_create_session("api_clear_session")
    session.update_search_results([{"id": "gid://1", "title": "Tee"}])
    session.update_web_results([{"title": "W", "url": "https://x.com", "source": "x.com"}])

    assert client.delete("/api/session/api_clear_session/results").status_code == 204

    assert session.last_searched_products == []
    assert session.last_web_results == []
    assert session.best_pick is None


def test_best_pick_refuses_to_invent_a_winner_from_nothing(client: TestClient):
    get_or_create_session("api_empty_session").clear_search_results()
    response = client.post(
        "/api/analysis/best-pick", json={"session_id": "api_empty_session", "need": "anything"}
    )
    assert response.status_code == 409
    assert "search" in response.json()["detail"]


def test_best_pick_returns_its_blind_spots(client: TestClient):
    """`missing` must survive to the client, or the UI overstates the verdict."""
    products = [
        {
            "id": "gid://a",
            "title": "Cotton Tee",
            "rating": {"value": 4.5, "scale_max": 5, "count": 30},
            "metadata": {"tech_specs": "Fabric: 100% Cotton"},
            "variants": [{"id": "v", "availability": {"available": True}}],
        },
        {
            "id": "gid://b",
            "title": "Tech Tee",
            "metadata": {"tech_specs": "Fabric: 88% Polyester, 12% Elastane"},
            "variants": [{"id": "v", "availability": {"available": True}}],
        },
    ]
    payload = client.post(
        "/api/analysis/best-pick",
        json={"products": products, "need": "moisture wicking running shirt"},
    ).json()

    assert payload["winner"]["title"] == "Tech Tee"
    assert isinstance(payload["winner"]["missing"], list)
    assert "review count" in payload["caveat"]


def test_payment_receipt_states_that_no_real_money_moved(client: TestClient):
    payload = client.post("/api/payments/test", json={"amount_cents": 2400}).json()
    assert payload["live"] is False
    assert payload["provider"] == "simulated"


def test_payment_rejects_a_non_positive_amount(client: TestClient):
    assert client.post("/api/payments/test", json={"amount_cents": 0}).status_code == 422


def test_mandate_authorizes_exactly_the_cart_total(client: TestClient):
    payload = client.post(
        "/api/mandate", json={"cart_id": "cart_1", "amount_cents": 5000}
    ).json()
    assert payload["spending_limit_cents"] == payload["amount_cents"] == 5000
    assert payload["signature"]


def test_negotiation_never_exceeds_the_budget(client: TestClient):
    payload = client.post(
        "/api/negotiate",
        json={"item": "Trail Runner", "list_price_cents": 15000, "budget_cents": 12000},
    ).json()
    if payload["agreed"]:
        assert payload["final_price_cents"] <= 12000
    assert isinstance(payload["transcript"], list)


def test_discovery_reports_what_each_scout_did(client: TestClient, monkeypatch: pytest.MonkeyPatch):
    async def _catalog(**_: Any) -> list[dict[str, Any]]:
        return [{"id": "gid://1", "title": "Tee"}]

    async def _web(*_args: Any, **_kwargs: Any):
        raise RuntimeError("web offline")

    monkeypatch.setattr(api._crew.client, "search_catalog", _catalog)
    monkeypatch.setattr("agentic_commerce.backend.crew.search_web", _web)

    payload = client.post("/api/discovery", json={"query": "tee", "limit": 2}).json()

    assert payload["has_catalog_hits"]
    errors = [e for e in payload["events"] if e["status"] == "error"]
    assert errors and errors[0]["agent"] == "WebScout"
    # One scout failing must not take the other down with it.
    assert payload["catalog_products"]


def test_chat_stream_emits_the_documented_event_vocabulary(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class StubAgent:
        def __init__(self, **_: Any) -> None:
            pass

        def execute_stream(self, **_: Any):
            yield {"type": "status", "text": "Thinking..."}
            yield {"type": "tool_call", "tool": "search_products", "args": {}, "text": "..."}
            yield {"type": "tool_result", "tool": "search_products", "result": "Found 1."}
            yield {"type": "products", "products": [{"id": "gid://1", "title": "Tee"}]}
            yield {"type": "content", "text": "Here it is."}

    monkeypatch.setattr(api, "CommerceAgent", StubAgent)

    response = client.post("/api/chat/stream", json={"message": "find a tee"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    kinds = [e["type"] for e in sse_events(response.text)]
    assert kinds == ["status", "tool_call", "tool_result", "products", "content", "done"]


def test_chat_stream_forwards_crew_events_as_they_appear(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """Crew progress is produced during the turn and must reach the client."""

    class CrewyAgent:
        def __init__(self, session_id: str = "", **_: Any) -> None:
            self.session_id = session_id

        def execute_stream(self, **_: Any):
            session = get_or_create_session(self.session_id)
            session.update_crew_events(
                [
                    {
                        "agent": "CatalogScout",
                        "status": "done",
                        "detail": "5 matches",
                        "duration": 0.9,
                    },
                    {"agent": "WebScout", "status": "error", "detail": "offline", "duration": None},
                ]
            )
            yield {"type": "tool_result", "tool": "search_products", "result": "ok"}

    monkeypatch.setattr(api, "CommerceAgent", CrewyAgent)

    response = client.post(
        "/api/chat/stream", json={"message": "tee", "session_id": "api_crew_session"}
    )
    crew = [e for e in sse_events(response.text) if e["type"] == "crew"]

    assert [e["agent"] for e in crew] == ["CatalogScout", "WebScout"]
    assert crew[0]["duration"] == 0.9
    assert crew[1]["status"] == "error"


def test_a_failing_turn_ends_with_an_error_event_not_silence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class BoomAgent:
        def __init__(self, **_: Any) -> None:
            pass

        def execute_stream(self, **_: Any):
            yield {"type": "status", "text": "Thinking..."}
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(api, "CommerceAgent", BoomAgent)

    events = sse_events(client.post("/api/chat/stream", json={"message": "hi"}).text)

    assert events[-1]["type"] == "error"
    assert "unavailable" in events[-1]["message"]


def test_stream_mints_a_session_id_when_the_client_has_none(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class QuietAgent:
        def __init__(self, **_: Any) -> None:
            pass

        def execute_stream(self, **_: Any):
            yield {"type": "content", "text": "hi"}

    monkeypatch.setattr(api, "CommerceAgent", QuietAgent)

    response = client.post("/api/chat/stream", json={"message": "hi"})
    done = sse_events(response.text)[-1]

    assert done["session_id"].startswith("s_")
    assert response.headers["x-session-id"] == done["session_id"]


def test_the_session_cookie_recovers_an_id_the_client_forgot(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """A refresh that loses local storage must not orphan the cart."""

    class QuietAgent:
        def __init__(self, **_: Any) -> None:
            pass

        def execute_stream(self, **_: Any):
            yield {"type": "content", "text": "hi"}

    monkeypatch.setattr(api, "CommerceAgent", QuietAgent)

    first = client.post("/api/chat/stream", json={"message": "hi"})
    minted = first.headers["x-session-id"]
    assert client.cookies[api.SESSION_COOKIE] == minted

    # Second turn sends no session_id at all; the cookie rides along.
    second = client.post("/api/chat/stream", json={"message": "again"})
    assert second.headers["x-session-id"] == minted


def test_an_explicit_session_id_beats_the_cookie(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    class QuietAgent:
        def __init__(self, **_: Any) -> None:
            pass

        def execute_stream(self, **_: Any):
            yield {"type": "content", "text": "hi"}

    monkeypatch.setattr(api, "CommerceAgent", QuietAgent)
    client.cookies.set(api.SESSION_COOKIE, "s_cookie_one")

    response = client.post("/api/chat/stream", json={"message": "hi", "session_id": "s_body_one"})
    assert response.headers["x-session-id"] == "s_body_one"


def test_examples_come_from_the_live_catalog_when_it_answers(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    async def _catalog(**_: Any) -> list[dict[str, Any]]:
        return [
            {"title": "Unisex Cotton Tee - Black / XL / 2-Pack"},
            {"title": "Merino Wool Base Layer Long Sleeve"},
        ]

    monkeypatch.setattr(api._crew.client, "search_catalog", _catalog)
    monkeypatch.setattr(api._client, "search_catalog", _catalog)
    api._example_cache.update({"at": 0.0, "payload": None})

    payload = client.get("/api/examples").json()

    assert payload["source"] == "catalog"
    # SKU-shaped titles are truncated into something a query can match.
    assert payload["examples"][0] == "Find a Unisex Cotton Tee"
    assert "Which of these should I buy?" in payload["examples"]


def test_examples_fall_back_and_say_so_when_the_catalog_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    async def _boom(**_: Any) -> list[dict[str, Any]]:
        raise RuntimeError("catalog offline")

    monkeypatch.setattr(api._client, "search_catalog", _boom)
    api._example_cache.update({"at": 0.0, "payload": None})

    payload = client.get("/api/examples").json()

    assert payload["source"] == "fallback"
    assert payload["examples"] == api.FALLBACK_EXAMPLES


def test_adding_to_cart_records_it_on_the_session(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """The rail's cart summary reads from the session, so the write must happen."""

    async def _create_cart(**_: Any) -> dict[str, Any]:
        return {"id": "cart_9", "totals": [{"type": "total", "amount": 2400, "currency": "USD"}]}

    monkeypatch.setattr(api._client, "create_cart", _create_cart)

    cart = client.post(
        "/api/cart",
        json={
            "merchant_domain": "shop.myshopify.com",
            "variant_id": "gid://shopify/ProductVariant/1",
            "session_id": "api_cart_session",
        },
    ).json()

    assert cart["merchant_domain"] == "shop.myshopify.com"
    snapshot = client.get("/api/session/api_cart_session").json()
    assert snapshot["active_cart"]["id"] == "cart_9"


def test_a_chat_turn_does_not_replay_the_previous_turns_crew(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """"How are you" showed a 10.4s WebScout on a 5.2s turn: last search's events."""
    session = get_or_create_session("api_stale_crew")
    session.update_crew_events([
        {"agent": "CatalogScout", "status": "done", "detail": "5 matches", "duration": 1.2},
        {"agent": "WebScout", "status": "done", "detail": "5 listings", "duration": 10.4},
    ])

    class ChattyAgent:
        def __init__(self, **_: Any) -> None:
            pass

        def execute_stream(self, **_: Any):
            yield {"type": "content", "text": "I'm doing great."}

    monkeypatch.setattr(api, "CommerceAgent", ChattyAgent)

    events = sse_events(
        client.post(
            "/api/chat/stream", json={"message": "how are you", "session_id": "api_stale_crew"}
        ).text
    )

    assert [e for e in events if e["type"] == "crew"] == []


def test_a_second_search_in_one_turn_still_reports_its_scouts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """`update_crew_events` replaces the list, so length alone cannot track progress."""
    session = get_or_create_session("api_rerun_crew")
    session.update_crew_events([{"agent": "CatalogScout", "status": "done", "detail": "old"}])

    class SearchingAgent:
        def __init__(self, session_id: str = "", **_: Any) -> None:
            self.session_id = session_id

        def execute_stream(self, **_: Any):
            inner = get_or_create_session(self.session_id)
            inner.update_crew_events([{"agent": "CatalogScout", "status": "done", "detail": "new"}])
            yield {"type": "tool_result", "tool": "search_products", "result": "ok"}

    monkeypatch.setattr(api, "CommerceAgent", SearchingAgent)

    events = sse_events(
        client.post(
            "/api/chat/stream", json={"message": "find a tee", "session_id": "api_rerun_crew"}
        ).text
    )
    crew = [e for e in events if e["type"] == "crew"]

    assert [e["detail"] for e in crew] == ["new"]
