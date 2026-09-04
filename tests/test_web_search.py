"""Tests for internet product discovery (parser, formatting, and tool wiring)."""

from typing import Any

import pytest

from agentic_commerce.backend import web_search as ws
from agentic_commerce.backend.session import get_or_create_session
from agentic_commerce.backend.tools import make_commerce_tools
from agentic_commerce.core.runtime import run_async

# Mirrors lite.duckduckgo.com: single-quoted attributes, href before class.
DDG_HTML = """
<table>
  <tr><td valign="top">1.&nbsp;</td>
  <td><a rel="nofollow" href="https://www.footlocker.com/trail-runners" class='result-link'>
  Trail Runners Under $150 - Foot Locker</a></td></tr>
  <tr><td>&nbsp;</td><td class='result-snippet'>
  Lightweight <b>trail</b> shoes built for grip.</td></tr>

  <tr><td valign="top">2.&nbsp;</td>
  <td><a rel="nofollow" href="https://www.rei.com/c/trail-running-shoes" class='result-link'>
  Trail Running Shoes | REI</a></td></tr>
  <tr><td>&nbsp;</td><td class='result-snippet'>Shop trail running shoes.</td></tr>

  <tr><td><a href="https://duckduckgo.com/settings" class='nav-link'>Settings</a></td></tr>
</table>
"""


def test_parser_handles_single_quoted_attrs_and_href_before_class():
    """The original regex assumed double quotes and class-before-href, matching nothing."""
    results = ws._parse_ddg_lite(DDG_HTML, limit=5)

    assert [r.url for r in results] == [
        "https://www.footlocker.com/trail-runners",
        "https://www.rei.com/c/trail-running-shoes",
    ]
    assert results[0].title == "Trail Runners Under $150 - Foot Locker"
    assert results[0].source == "footlocker.com"
    assert "grip" in results[0].snippet
    assert "<b>" not in results[0].snippet, "HTML tags must be stripped"


def test_parser_ignores_non_result_links():
    """Navigation anchors must not be reported as products."""
    urls = [r.url for r in ws._parse_ddg_lite(DDG_HTML, limit=5)]
    assert "https://duckduckgo.com/settings" not in urls


def test_parser_respects_limit():
    assert len(ws._parse_ddg_lite(DDG_HTML, limit=1)) == 1


def test_format_results_is_actionable_when_empty():
    out = ws.format_results("obscure item", [])
    assert "No web results" in out and "obscure item" in out


def test_format_results_renders_markdown_links():
    out = ws.format_results("trail", ws._parse_ddg_lite(DDG_HTML, limit=2))
    expected = "[Trail Runners Under $150 - Foot Locker](https://www.footlocker.com/trail-runners)"
    assert expected in out
    assert "cannot be added to a Universal Cart" in out


def test_web_tool_is_registered_and_bound_to_session(monkeypatch: pytest.MonkeyPatch):
    """The tool must store results in its bound session, not the ambient default."""

    async def _fake_search(query: str, limit: int = 5) -> list[ws.WebResult]:
        return [ws.WebResult("Nike Pegasus", "https://nike.com/p", "fast", "nike.com")]

    monkeypatch.setattr(ws, "search_web", _fake_search)
    monkeypatch.setattr("agentic_commerce.backend.tools.search_web", _fake_search)

    session_id = "web_tool_session"
    tools: dict[str, Any] = {t.name: t for t in make_commerce_tools(session_id)}
    assert "search_web_products" in tools

    out = tools["search_web_products"].invoke({"query": "pegasus", "max_price": 150.0})

    assert "Nike Pegasus" in out
    stored = get_or_create_session(session_id).last_web_results
    assert stored == [
        {
            "title": "Nike Pegasus",
            "url": "https://nike.com/p",
            "snippet": "fast",
            "source": "nike.com",
            "image_url": None,
            "favicon_url": "https://www.google.com/s2/favicons?domain=nike.com&sz=128",
        }
    ]


def test_max_price_is_folded_into_the_query(monkeypatch: pytest.MonkeyPatch):
    seen: list[str] = []

    async def _capture(query: str, limit: int = 5) -> list[ws.WebResult]:
        seen.append(query)
        return []

    monkeypatch.setattr("agentic_commerce.backend.tools.search_web", _capture)
    tools = {t.name: t for t in make_commerce_tools("web_price_session")}
    tools["search_web_products"].invoke({"query": "hoodie", "max_price": 80.0})

    assert seen == ["hoodie under $80"]


def test_enrich_attaches_images_to_a_non_empty_result_list(monkeypatch: pytest.MonkeyPatch):
    """Regression: the only prior coverage was the empty-list early return.

    That let a broken ``async with httpx.AsyncClient() as client`` scope ship —
    every real (non-empty) enrichment raised ``NameError`` while the suite stayed
    green.
    """
    results = [
        ws.WebResult("A", "https://a.example/p", "", "a.example"),
        ws.WebResult("B", "https://b.example/p", "", "b.example"),
    ]

    async def _preview(client: Any, url: str) -> str | None:
        assert client is not None, "enrichment must run inside an httpx client"
        return f"{url}/og.jpg" if "a.example" in url else None

    monkeypatch.setattr(ws, "_fetch_preview_image", _preview)

    enriched = run_async(ws.enrich_with_images(results))

    assert [r.image_url for r in enriched] == ["https://a.example/p/og.jpg", None]
    # Order must survive the concurrent gather.
    assert [r.title for r in enriched] == ["A", "B"]


def test_enrich_survives_a_failing_preview_fetch(monkeypatch: pytest.MonkeyPatch):
    """One dead retailer must not drop the whole result set."""

    async def _boom(client: Any, url: str) -> str | None:
        raise RuntimeError("host unreachable")

    monkeypatch.setattr(ws, "_fetch_preview_image", _boom)

    enriched = run_async(ws.enrich_with_images([ws.WebResult("A", "https://a.example", "", "a")]))

    assert len(enriched) == 1
    assert enriched[0].image_url is None
