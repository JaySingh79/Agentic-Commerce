"""Regression tests for explicit session binding of commerce tools.

Gradio drives a sync generator by calling ``next()`` from ``anyio.to_thread``,
which may schedule each step on a *different* worker thread. A ``ContextVar``
set inside the generator body therefore does not survive to the step that
actually invokes a tool, so ambient session routing silently wrote catalog
results into ``default_user_session`` instead of the browser's session — the
product gallery and image cards were then always empty.

These tests pin the fix: tools are bound to an explicit session id at
construction time and never depend on ambient context.
"""

import threading
from typing import Any

import pytest

from agentic_commerce.backend import tools as tools_mod
from agentic_commerce.backend.session import get_or_create_session
from agentic_commerce.backend.tools import make_commerce_tools

FAKE_PRODUCT: dict[str, Any] = {
    "id": "gid://shopify/p/1",
    "title": "Trail Runner",
    "price_range": {"min": {"amount": 12000}},
    "media": [{"type": "image", "url": "https://cdn.example/shoe.jpg", "alt_text": "shoe"}],
    "variants": [{"id": "gid://shopify/ProductVariant/1", "price": {"amount": 12000}}],
}


@pytest.fixture
def stub_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replaces the live UCP catalog call with a deterministic single-product result."""

    class _StubClient:
        async def search_catalog(self, **_: Any) -> list[dict[str, Any]]:
            return [FAKE_PRODUCT]

        async def get_product(self, product_id: str, selected: Any = None) -> dict[str, Any]:
            return FAKE_PRODUCT

    stub = _StubClient()
    monkeypatch.setattr(tools_mod, "_ucp_client", stub)
    # `search_products` reaches the catalog through the crew now, and the crew holds
    # its own client reference, so stubbing only the module global would let these
    # tests hit the live catalog.
    monkeypatch.setattr(tools_mod._crew, "client", stub)

    async def _no_web(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr("agentic_commerce.backend.crew.search_web", _no_web)


def _tool(name: str, session_id: str):
    """Returns the named tool from a freshly session-bound tool set."""
    return {t.name: t for t in make_commerce_tools(session_id)}[name]


def test_search_writes_to_bound_session_from_a_foreign_thread(stub_catalog: None) -> None:
    """The bug: tool ran on a worker thread with no ambient context var."""
    session_id = "browser_hash_abc123"
    tool = _tool("search_products", session_id)

    # Invoke from a thread that has never seen set_active_session_id — exactly
    # what Gradio's per-step threadpool does.
    thread = threading.Thread(target=lambda: tool.invoke({"query": "running shoes"}))
    thread.start()
    thread.join()

    session = get_or_create_session(session_id)
    assert [p["id"] for p in session.last_searched_products] == ["gid://shopify/p/1"]


def test_search_does_not_leak_into_the_default_session(stub_catalog: None) -> None:
    """Results must land only in the bound session, never the ambient default."""
    default = get_or_create_session("default_user_session")
    default.clear_search_results()

    _tool("search_products", "browser_hash_isolated").invoke({"query": "hoodie"})

    assert default.last_searched_products == []


def test_product_details_writes_to_bound_session(stub_catalog: None) -> None:
    """get_product_details must also honour the bound session."""
    session_id = "browser_hash_details"
    _tool("get_product_details", session_id).invoke({"product_id": "gid://shopify/p/1"})

    assert get_or_create_session(session_id).active_product == FAKE_PRODUCT


def test_tool_telemetry_is_attributed_to_the_bound_session(stub_catalog: None) -> None:
    """Tool spans recorded stats under the default session, so the UI badge stayed empty."""
    from agentic_commerce.core.telemetry import get_latest_session_stats, trace_turn

    session_id = "browser_hash_telemetry"
    with trace_turn(session_id, "find shoes"):
        _tool("search_products", session_id).invoke({"query": "shoes"})

    called = get_latest_session_stats(session_id)["tools_called"]
    assert [c["tool"] for c in called] == ["search_products"]
