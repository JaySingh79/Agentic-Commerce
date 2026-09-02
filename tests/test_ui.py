"""Unit tests for the Gradio Chat UI and ChatEngine with multi-turn history."""

import gradio as gr

from agentic_commerce.backend.session import get_or_create_session
from agentic_commerce.ui.app import create_chat_app
from agentic_commerce.ui.chat_engine import ChatEngine, _product_card, _search_cards


def test_chat_engine_empty_input():
    engine = ChatEngine()
    chunks = list(engine.stream_response(message="   ", history=[]))
    assert len(chunks) == 1
    assert "Please provide a query" in chunks[0]


def test_chat_engine_multi_turn_flow():
    engine = ChatEngine()
    history = []

    # Turn 1: Search
    t1_chunks = list(engine.stream_response(message="Search for running shoes", history=history))
    assert len(t1_chunks) > 0
    t1_final = t1_chunks[-1]
    history.append({"role": "user", "content": "Search for running shoes"})
    history.append({"role": "assistant", "content": t1_final})

    # Turn 2: Relative instruction with history
    t2_chunks = list(engine.stream_response(message="add to cart", history=history))
    assert len(t2_chunks) > 0
    t2_final = t2_chunks[-1]
    history.append({"role": "user", "content": "add to cart"})
    history.append({"role": "assistant", "content": t2_final})

    # Turn 3: Generate mandate
    t3_chunks = list(
        engine.stream_response(message="generate ap2 payment mandate", history=history)
    )
    assert len(t3_chunks) > 0
    t3_final = t3_chunks[-1]
    assert "mandate" in t3_final.lower() or "cart" in t3_final.lower()


def test_create_chat_app_structure():
    app = create_chat_app()
    assert isinstance(app, gr.Blocks)
    assert app.title == "Agentic Commerce Hub"


def test_search_cards_render_image_deterministically():
    # Network-free: seed the session as the search tool would, then render cards.
    session = get_or_create_session("card_test_session")
    session.update_search_results([
        {
            "id": "gid://shopify/Product/1",
            "title": "Trail Runner",
            "price_range": {"min": {"amount": 11700}},
            "media": [{"type": "image", "url": "https://cdn.shopify.com/p/trail.jpg"}],
        }
    ])
    md = _search_cards(session)
    assert "](https://cdn.shopify.com/p/trail.jpg)" in md
    assert "Trail Runner" in md
    assert "$117.00" in md


def test_search_cards_empty_without_media():
    session = get_or_create_session("card_test_no_media")
    session.update_search_results([{"id": "gid://x", "title": "NoImage"}])
    assert _search_cards(session) == ""


def test_product_card_uses_active_product():
    session = get_or_create_session("card_test_product")
    session.active_product = {
        "id": "gid://shopify/Product/2",
        "title": "Detailed Shoe",
        "variants": [{"id": "v1", "price": {"amount": 13900}}],
        "media": [{"type": "image", "url": "https://cdn.shopify.com/p/detail.jpg"}],
    }
    md = _product_card(session)
    assert "](https://cdn.shopify.com/p/detail.jpg)" in md
    assert "Detailed Shoe" in md
