"""Tests for the session knowledge graph and the new-session endpoint.

The graph exists to move a session's meaning to another agent for far fewer
tokens than a transcript or a raw snapshot. Two things therefore need cover: that
it is genuinely smaller, and that nothing load-bearing is lost on the way —
in particular the honesty markers (unknown rating, non-purchasable web result,
test-mode payment, unscored criteria) that the rest of the stack maintains.
"""

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from agentic_commerce.api import server as api
from agentic_commerce.backend.session import get_or_create_session
from agentic_commerce.backend.session_graph import (
    build_graph,
    estimate_tokens,
    to_compact_text,
)


@pytest.fixture
def client() -> TestClient:
    return TestClient(api.create_app(serve_frontend=False))


def furnished_session(session_id: str = "graph_session"):
    """A session carrying every surface the graph knows how to render."""
    session = get_or_create_session(session_id)
    session.update_search_results([
        {
            "id": "gid://shopify/Product/1",
            "title": "White Everyday Crew Neck Tee",
            "rating": {"value": 4.6, "count": 1487},
            "metadata": {"tech_specs": "Fabric: 100% Cotton"},
            "variants": [
                {
                    "id": "gid://shopify/ProductVariant/11",
                    "price": {"amount": 2300, "currency": "USD"},
                    "availability": {"available": True},
                    "seller": {"domain": "youngla.myshopify.com", "name": "YoungLA"},
                }
            ],
        },
        {
            "id": "gid://shopify/Product/2",
            "title": "Washed Tailored Tees",
            "variants": [
                {
                    "id": "gid://shopify/ProductVariant/22",
                    "price": {"amount": 4000, "currency": "USD"},
                    "availability": {"available": False},
                    "seller": {"domain": "youngla.myshopify.com"},
                }
            ],
        },
    ])
    session.update_web_results([
        {"title": "Best tees 2026", "url": "https://mag.example/tees", "source": "mag.example"}
    ])
    session.update_best_pick({
        "winner": {"product_id": "gid://shopify/Product/1", "score": 0.81, "missing": ["fit"]},
        "caveat": "popularity is inferred from review count",
    })
    session.update_cart({
        "id": "cart_1",
        "merchant_domain": "youngla.myshopify.com",
        "totals": [{"type": "total", "amount": 4000, "currency": "USD"}],
    })
    session.update_mandate({
        "mandate_id": "ap2_1",
        "amount_cents": 4000,
        "currency": "USD",
        "expires_at": 1788635885,
        "status": "AUTHORIZED_PENDING_SETTLEMENT",
    })
    session.update_payment({
        "provider": "simulated",
        "status": "captured",
        "amount_cents": 4000,
        "currency": "USD",
        "live": False,
    })
    session.add_message("user", "find me a white tee")
    return session


# ------------------------------------------------------------------ structure


def test_the_graph_names_the_relationships_not_just_the_objects():
    graph = build_graph(furnished_session("graph_edges"))
    edges = {(e["from"], e["rel"], e["to"]) for e in graph["edges"]}

    assert ("P1", "sold_by", "M1") in edges
    assert ("A1", "authorizes", "C1") in edges
    assert ("X1", "settles", "A1") in edges
    assert ("B1", "recommends", "P1") in edges


def test_a_merchant_selling_two_products_is_stated_once():
    """Deduplication is the point: repeating a domain per product wastes tokens."""
    graph = build_graph(furnished_session("graph_dedupe"))
    merchants = [n for n in graph["nodes"] if n["type"] == "merchant"]

    assert len(merchants) == 1
    assert merchants[0]["domain"] == "youngla.myshopify.com"


def test_an_unrated_product_carries_no_rating_rather_than_a_zero():
    graph = build_graph(furnished_session("graph_unrated"))
    second = next(n for n in graph["nodes"] if n["id"] == "P2")

    assert "rating" not in second


def test_a_web_result_is_marked_non_purchasable():
    """A downstream agent must not try to cart an open-web listing."""
    graph = build_graph(furnished_session("graph_web"))
    web = next(n for n in graph["nodes"] if n["type"] == "web_result")

    assert web["purchasable"] is False


def test_the_payment_node_always_states_whether_money_moved():
    graph = build_graph(furnished_session("graph_live"))
    payment = next(n for n in graph["nodes"] if n["type"] == "payment")

    assert payment["live"] is False


def test_the_verdict_keeps_its_blind_spots():
    graph = build_graph(furnished_session("graph_missing"))
    verdict = next(n for n in graph["nodes"] if n["type"] == "verdict")

    assert verdict["not_scored"] == ["fit"]


# -------------------------------------------------------------- the text form


def test_the_compact_text_is_much_smaller_than_the_raw_snapshot():
    session = furnished_session("graph_size")
    text = to_compact_text(build_graph(session))
    raw = json.dumps(api.session_snapshot(session.session_id), default=str)

    assert estimate_tokens(text) < estimate_tokens(raw) / 2


def test_the_text_carries_a_legend_so_a_cold_reader_can_parse_it():
    text = to_compact_text(build_graph(furnished_session("graph_legend")))
    assert "legend:" in text
    assert "E P1-sold_by->M1" in text or "P1-sold_by->M1" in text


def test_the_text_marks_the_winner_the_stock_and_the_fabric():
    text = to_compact_text(build_graph(furnished_session("graph_marks")))

    assert "*best" in text
    assert "oos" in text  # the unavailable variant
    assert "fabric:" in text
    assert "live:no" in text


def test_an_empty_session_says_so_instead_of_emitting_an_empty_graph():
    session = get_or_create_session("graph_empty")
    session.clear_search_results()
    text = to_compact_text(build_graph(session))

    assert "no commerce state yet" in text


# ------------------------------------------------------------------ endpoints


def test_the_graph_endpoint_reports_both_sizes(client: TestClient):
    furnished_session("graph_api")
    payload = client.get("/api/session/graph_api/graph").json()

    assert payload["estimated_tokens"] < payload["estimated_tokens_raw_snapshot"]
    assert "legend:" in payload["text"]
    assert "graph" not in payload  # text format by default


def test_the_json_format_returns_nodes_and_edges(client: TestClient):
    furnished_session("graph_api_json")
    payload = client.get("/api/session/graph_api_json/graph?format=json").json()

    assert payload["graph"]["nodes"]
    assert payload["graph"]["edges"]


def test_an_unknown_format_is_rejected(client: TestClient):
    assert client.get("/api/session/x/graph?format=yaml").status_code == 422


def test_a_new_session_is_minted_without_destroying_the_old_one(client: TestClient):
    """Starting over must not cost the shopper a cart they might still want."""
    old = furnished_session("graph_keepme")

    response = client.post("/api/session")
    fresh = response.json()["session_id"]

    assert response.status_code == 201
    assert fresh != old.session_id
    assert client.cookies[api.SESSION_COOKIE] == fresh
    # The old session is still readable under its own id.
    assert client.get(f"/api/session/{old.session_id}").json()["active_cart"]["id"] == "cart_1"


def test_a_new_session_starts_empty(client: TestClient):
    fresh = client.post("/api/session").json()["session_id"]
    snapshot: dict[str, Any] = client.get(f"/api/session/{fresh}").json()

    assert snapshot["last_searched_products"] == []
    assert snapshot["active_cart"] is None
