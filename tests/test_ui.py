"""Unit tests for the Gradio Chat UI and ChatEngine with multi-turn history."""

import gradio as gr

from agentic_commerce.backend.agent import CommerceAgent
from agentic_commerce.backend.session import get_or_create_session
from agentic_commerce.ui.app import create_chat_app
from agentic_commerce.ui.chat_engine import ChatEngine, _product_card, _search_cards


def test_chat_engine_empty_input():
    engine = ChatEngine()
    chunks = list(engine.stream_response(message="   ", history=[]))
    assert len(chunks) == 1
    assert "Please provide a query" in chunks[0]


def test_chat_engine_multi_turn_flow():
    from agentic_commerce.backend.session import _SESSION_STORE

    # Isolate from polluting global session (previous tests leave last_searched_products)
    _SESSION_STORE.clear()
    engine = ChatEngine()
    # Force the deterministic heuristic fallback: clearing the cache alone still
    # rebuilds a live-LLM agent, which made tool choice (and this test) flaky.
    # Pre-seeding an agent with llm=None pins the fallback dispatcher instead.
    engine._agents.clear()
    agent = CommerceAgent(session_id="default_user_session")
    agent.llm = None
    engine._agents["default_user_session"] = agent
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


def test_stream_with_gallery_flow():
    from agentic_commerce.backend.session import _SESSION_STORE

    _SESSION_STORE.clear()
    engine = ChatEngine()
    engine._agents.clear()

    # Stream query
    frames = list(
        engine.stream_with_gallery(
            message="Search for running shoes",
            history=[],
            session_id="test_gal_session",
        )
    )
    assert len(frames) > 0
    # Every frame is (chat_markdown, gallery_items, results_html)
    text, gallery, results_html = frames[-1]
    assert isinstance(text, str)
    assert isinstance(gallery, list)
    assert isinstance(results_html, str)
    # Final frame contains response and images in gallery
    assert len(gallery) > 0
    # Product imagery renders as a grid panel, not stacked Markdown in the bubble
    assert 'class="ac-grid"' in results_html
    assert "![" not in text, "chat bubble must no longer stack Markdown images"
    assert "running shoes" in text.lower() or "shoes" in text.lower()


def test_session_clear_search_results():
    session = get_or_create_session("test_clear_session")
    session.update_search_results([{"id": "gid://1", "title": "Sneaker"}])
    assert len(session.last_searched_products) == 1

    session.clear_search_results()
    assert len(session.last_searched_products) == 0


def test_clear_search_results_empties_every_gallery_source():
    """active_product outranks search results in get_gallery_items, so it must
    also be cleared or the gallery repopulates right after "Clear Gallery"."""
    from agentic_commerce.ui.chat_engine import get_gallery_items

    session = get_or_create_session("test_clear_all_sources")
    session.update_search_results([{"id": "gid://1", "title": "Sneaker"}])
    session.active_product = {
        "id": "gid://2",
        "title": "Boot",
        "media": [{"type": "image", "url": "https://cdn.example/boot.jpg"}],
    }
    session.update_web_results([{"title": "Web hit", "url": "https://x.com"}])
    assert get_gallery_items(session), "precondition: gallery is populated"

    session.clear_search_results()

    assert session.active_product is None
    assert session.last_web_results == []
    assert get_gallery_items(session) == []


# --------------------------------------------------------------- layout (§3)


def _product(pid: str, images: int) -> dict:
    return {
        "id": pid,
        "title": f"Product {pid}",
        "media": [
            {"url": f"https://cdn.example/{pid}-{i}.jpg", "type": "image"} for i in range(images)
        ],
    }


def test_the_gallery_shows_every_image_of_the_active_product():
    """The close-up view is the gallery's job; the grid already lists the set."""
    from agentic_commerce.ui.chat_engine import get_gallery_items

    session = get_or_create_session("ui_gallery_active")
    session.update_search_results([_product("a", 2), _product("b", 2)])
    session.update_active_product(_product("a", 3))

    items = get_gallery_items(session)

    assert [url for url, _ in items] == [
        "https://cdn.example/a-0.jpg",
        "https://cdn.example/a-1.jpg",
        "https://cdn.example/a-2.jpg",
    ]


def test_without_an_active_product_the_gallery_shows_one_image_per_match():
    from agentic_commerce.ui.chat_engine import get_gallery_items

    session = get_or_create_session("ui_gallery_browse")
    session.clear_search_results()
    session.update_search_results([_product("a", 3), _product("b", 3)])

    items = get_gallery_items(session)

    assert [url for url, _ in items] == [
        "https://cdn.example/a-0.jpg",
        "https://cdn.example/b-0.jpg",
    ]


def test_web_imagery_reaches_the_gallery_when_the_catalog_is_empty():
    from agentic_commerce.ui.chat_engine import get_gallery_items

    session = get_or_create_session("ui_gallery_web")
    session.clear_search_results()
    session.update_web_results([
        {
            "title": "Trail Runner",
            "url": "https://shop.example/x",
            "source": "shop.example",
            "image_url": "https://shop.example/x.jpg",
        },
        # Favicon-only: site branding, not the product.
        {
            "title": "Category page",
            "url": "https://other.example/c",
            "source": "other.example",
            "image_url": None,
            "favicon_url": "https://icons.example/f.png",
        },
    ])

    items = get_gallery_items(session)

    assert [url for url, _ in items] == ["https://shop.example/x.jpg"]
    assert "shop.example" in items[0][1]


def test_a_web_search_refreshes_the_gallery():
    """`search_web_products` used to be skipped, so web images never appeared."""
    import inspect

    from agentic_commerce.ui import chat_engine

    source = inspect.getsource(chat_engine.ChatEngine.stream_with_gallery)
    assert "search_web_products" in source
