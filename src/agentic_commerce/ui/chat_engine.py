"""Chat engine connecting Gradio ChatInterface to the CommerceAgent backend."""

import os
from collections.abc import Generator
from typing import Any

from agentic_commerce.backend.agent import (
    COMMERCE_SYSTEM_PROMPT,
    CommerceAgent,
    _serialize_products,
)
from agentic_commerce.backend.session import CommerceSession, get_or_create_session
from agentic_commerce.backend.tools import primary_image_url
from agentic_commerce.core.telemetry import get_latest_session_stats
from agentic_commerce.ui.cards import render_results_panel

DEFAULT_SYSTEM_PROMPT = COMMERCE_SYSTEM_PROMPT
DEFAULT_MODEL = os.getenv("MODEL") or "gemini-2.5-flash"


def _price_str(product: dict[str, Any]) -> str:
    """Best-effort formatted price for a raw catalog product dict."""
    amount = product.get("price_range", {}).get("min", {}).get("amount")
    if amount is None:
        variants = product.get("variants", []) or []
        if variants and isinstance(variants[0], dict):
            amount = variants[0].get("price", {}).get("amount")
    return f"${(amount / 100):.2f}" if amount else ""


def _telemetry_badge(session_id: str) -> str:
    """Renders the per-turn token/tool/latency badge appended to a reply."""
    stat = get_latest_session_stats(session_id)
    if not (stat.get("total_tokens", 0) > 0 or stat.get("tools_called")):
        return ""
    tools = stat.get("tools_called") or []
    tools_info = f" • Tools: {len(tools)}" if tools else ""
    tok_details = (
        f"({stat.get('prompt_tokens', 0)} prompt / {stat.get('completion_tokens', 0)} comp)"
    )
    return (
        f"\n\n> 📊 **Telemetry:** {stat.get('total_tokens', 0)} tokens "
        f"{tok_details}{tools_info} • {stat.get('latency_seconds', 0.0):.2f}s"
    )


def _card(product: dict[str, Any]) -> str:
    """Renders one product image card as Markdown, or '' if it has no image.

    Markdown image syntax is used because Gradio's chatbot sanitizer strips raw
    ``<img>``/``<figure>`` HTML; Markdown images pass through and render. Image
    size is capped via CSS (see theme.py).
    """
    url = primary_image_url(product)
    if not url:
        return ""
    title = str(product.get("title", "Product"))
    price = _price_str(product)
    alt = title.replace("[", "").replace("]", "")
    caption = f"**{title}** — {price}" if price else f"**{title}**"
    return f"![{alt}]({url})\n{caption}"


def _search_cards(session: CommerceSession) -> str:
    """Image cards for the latest search results (deterministic, LLM-proof)."""
    cards = [_card(p) for p in session.last_searched_products[:5]]
    cards = [c for c in cards if c]
    if not cards:
        return ""
    return "**Product previews**\n\n" + "\n\n".join(cards)


def _product_card(session: CommerceSession) -> str:
    """Image card for the active product detail view."""
    if not session.active_product:
        return ""
    return _card(session.active_product)


def _media_for(product: dict[str, Any]) -> list[dict[str, str]]:
    """Extract normalized media list from product (spec: product.media[].url/type/alt_text)."""
    media = product.get("media")
    if isinstance(media, list) and media:
        out = []
        for m in media:
            if isinstance(m, dict) and m.get("url"):
                out.append({  # noqa: E501
                    "url": str(m["url"]),
                    "alt_text": str(m.get("alt_text", "")),
                    "type": str(m.get("type", "image")),
                })
        if out:
            return out
    url = primary_image_url(product)
    if url:
        return [{"url": url, "alt_text": str(product.get("title", "")), "type": "image"}]
    return []


def get_gallery_items(session: CommerceSession) -> list[tuple[str, str]]:
    """Builds (image_url, caption) pairs for gr.Gallery from session state.

    The two product surfaces have distinct jobs (§3.2). The results panel
    (``ui/cards.py``) is the *browsable set*: one card per match. This gallery is
    the *close-up*: when a product is active it shows every image that product
    has, so the shopper can actually look at it, instead of repeating the grid
    one thumbnail at a time.

    With no catalog matches it falls back to web listing imagery (§3.4) rather
    than sitting empty next to a rail full of web results; those captions name
    their source, because a web photo is not a catalog product.
    """
    if session.active_product and _media_for(session.active_product):
        return _images_of(session.active_product, limit=8)

    if session.last_searched_products:
        items: list[tuple[str, str]] = []
        for product in session.last_searched_products[:6]:
            items.extend(_images_of(product, limit=1))
        return items

    return _web_gallery_items(session.last_web_results)


def _images_of(product: dict[str, Any], limit: int) -> list[tuple[str, str]]:
    """Up to *limit* (url, caption) pairs for one product."""
    price = _price_str(product)
    title = str(product.get("title", "Product"))
    caption = f"{title} — {price}" if price else title
    items: list[tuple[str, str]] = []
    for medium in _media_for(product):
        if medium.get("type") != "image":
            continue
        items.append((medium["url"], caption))
        if len(items) >= limit:
            break
    return items


def _web_gallery_items(results: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Gallery entries for web listings, favicons excluded.

    A favicon is site branding, not the product, and putting them in a preview
    gallery says "here is what you would buy" about a 128px logo.
    """
    items: list[tuple[str, str]] = []
    for result in results[:6]:
        url = result.get("image_url")
        if not url:
            continue
        source = str(result.get("source") or "web")
        items.append((str(url), f"{result.get('title', 'Listing')} — {source}"))
    return items


def products_to_gallery(products: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """Converts agent 'products' payload (with media[]) to Gallery tuples."""
    items: list[tuple[str, str]] = []
    for p in products:
        media = p.get("media") or []
        url = None
        for m in media:
            if isinstance(m, dict) and m.get("url") and m.get("type", "image") == "image":
                url = str(m["url"])
                break
        if not url:
            url = primary_image_url(p)
        if not url:
            continue
        price_cents = p.get("price_cents")
        price = f"${(price_cents / 100):.2f}" if price_cents is not None else _price_str(p)  # noqa: E501
        caption = f"{p.get('title','Product')} — {price}" if price else str(  # noqa: E501
            p.get("title", "Product")
        )
        items.append((url, caption))
    return items


class ChatEngine:
    """Handles conversation state and streaming response generation for Gradio UI.

    A `CommerceAgent` is cached per browser session so the LLM client and bound
    tools are initialized once, not rebuilt on every message.
    """

    def __init__(self, agent_name: str = "Agentic Commerce Assistant"):
        self.agent_name = agent_name
        self._agents: dict[str, CommerceAgent] = {}

    def _get_agent(self, session_id: str, model_name: str, temperature: float) -> CommerceAgent:
        """Returns a cached agent for the session, re-initializing only on config change."""
        agent = self._agents.get(session_id)
        if agent is None or agent.model_name != model_name or agent.temperature != temperature:
            agent = CommerceAgent(
                model_name=model_name,
                temperature=temperature,
                session_id=session_id,
            )
            self._agents[session_id] = agent
        return agent

    def stream_response(
        self,
        message: str,
        history: list[dict[str, str]],
        model_name: str = DEFAULT_MODEL,
        temperature: float = 0.4,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        session_id: str = "default_user_session",
    ) -> Generator[str, None, None]:
        """Streams tool-execution badges and live agent tokens to the Gradio chatbot."""
        user_text = message.strip()
        if not user_text:
            yield "Please provide a query or instruction."
            return

        agent = self._get_agent(session_id, model_name or DEFAULT_MODEL, temperature)
        session = get_or_create_session(session_id)

        trace_lines: list[str] = []
        answer = ""
        cards = ""

        def render() -> str:
            parts: list[str] = []
            if trace_lines:
                parts.append("\n".join(trace_lines))
            if answer:
                parts.append(answer)
            if cards:
                parts.append(cards)
            return "\n\n".join(parts) or "…"

        for event in agent.execute_stream(
            message=user_text, history=history, system_prompt=system_prompt
        ):
            etype = event.get("type")

            if etype == "tool_call":
                tname = event.get("tool", "")
                ttext = event.get("text", f"Running `{tname}`...")
                trace_lines.append(f"> 🔧 {ttext}")
                yield render()
            elif etype == "tool_result":
                # Render product images deterministically from session state, so
                # they survive the LLM's synthesis rewrite of the tool output.
                tool = event.get("tool")
                if tool == "search_products":
                    cards = _search_cards(session)
                elif tool == "get_product_details":
                    cards = _product_card(session)
                if cards:
                    yield render()
            elif etype == "products":
                # Structured media payload from agent.py — drives the gallery + chat cards
                prods = event.get("products", [])
                if prods and not cards:
                    # Keep chat markdown in sync even if tool_result was suppressed/rewritten
                    # Build markdown from payload directly (no session dependency)
                    tmp_cards = []
                    for p in prods:
                        media = p.get("media") or []  # noqa: E501
                        url = next(  # noqa: E501
                            (m.get("url") for m in media if isinstance(m, dict) and m.get("url")),
                            None,
                        )
                        if not url:
                            url = primary_image_url(p)
                        if url:
                            title = str(p.get("title", "Product"))
                            pc = p.get("price_cents")
                            price = f"${(pc/100):.2f}" if pc else _price_str(p)
                            alt = title.replace("[", "").replace("]", "")
                            cap = f"**{title}** — {price}" if price else f"**{title}**"
                            tmp_cards.append(f"![{alt}]({url})\n{cap}")
                    if tmp_cards:
                        cards = "**Product previews**\n\n" + "\n\n".join(tmp_cards)
                        yield render()
            elif etype == "content":
                answer += event.get("text", "")
                yield render()
            # "status" events are internal; not surfaced verbatim.

        stat = get_latest_session_stats(session_id)
        telemetry_badge = ""
        if stat.get("total_tokens", 0) > 0 or stat.get("tools_called"):
            tools_info = (
                f" • Tools: {len(stat.get('tools_called', []))}"
                if stat.get("tools_called")
                else ""
            )
            tok_details = (
                f"({stat.get('prompt_tokens', 0)} prompt / "
                f"{stat.get('completion_tokens', 0)} comp)"
            )
            telemetry_badge = (
                f"\n\n> 📊 **Telemetry:** {stat.get('total_tokens', 0)} tokens "
                f"{tok_details}{tools_info} • {stat.get('latency_seconds', 0.0):.2f}s"
            )

        if not answer:
            base = render() if (trace_lines or cards) else "Done — no textual response generated."
            yield base + telemetry_badge
        else:
            yield render() + telemetry_badge

    def stream_with_gallery(
        self,
        message: str,
        history: list[dict[str, str]],
        model_name: str = DEFAULT_MODEL,
        temperature: float = 0.4,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        session_id: str = "default_user_session",
    ) -> Generator[tuple[str, list[tuple[str, str]], str], None, None]:
        """Streams (chat_markdown, gallery_items, results_html) for the UI.

        Product imagery is emitted as ``results_html`` (a CSS grid, see
        ``ui/cards.py``) rather than Markdown images in the chat bubble, because
        Markdown images can only stack one per line.
        """
        user_text = message.strip()
        if not user_text:
            yield "Please provide a query or instruction.", [], render_results_panel([], [])
            return

        agent = self._get_agent(session_id, model_name or DEFAULT_MODEL, temperature)
        session = get_or_create_session(session_id)

        trace_lines: list[str] = []
        answer = ""
        products: list[dict[str, Any]] = _serialize_products(session.last_searched_products)
        gallery: list[tuple[str, str]] = get_gallery_items(session)

        def render_chat() -> str:
            parts: list[str] = []
            if trace_lines:
                parts.append("\n".join(trace_lines))
            if answer:
                parts.append(answer)
            return "\n\n".join(parts) or "…"

        # The panel is re-emitted on every streamed token, but products cannot change
        # mid-sentence. Rebuilding (and re-escaping) every card per token was the source
        # of the grid flicker, so the HTML is cached against a cheap identity signature
        # and only rebuilt when the underlying results actually change.
        panel_cache: dict[str, str] = {}

        def render_panel() -> str:
            signature = (
                f"{len(products)}:{id(products)}:"
                f"{len(session.last_web_results)}:{id(session.last_web_results)}:"
                f"{id(session.best_pick)}"
            )
            if signature not in panel_cache:
                panel_cache.clear()
                panel_cache[signature] = render_results_panel(
                    products, session.last_web_results, session.best_pick
                )
            return panel_cache[signature]

        yield render_chat(), gallery, render_panel()

        for event in agent.execute_stream(
            message=user_text, history=history, system_prompt=system_prompt
        ):
            etype = event.get("type")
            if etype == "tool_call":
                tname = event.get("tool", "")
                ttext = event.get("text", f"Running `{tname}`...")
                trace_lines.append(f"> 🔧 {ttext}")
                yield render_chat(), gallery, render_panel()
            elif etype == "tool_result":
                tool = event.get("tool")
                if tool in {
                    "search_products",
                    "get_product_details",
                    "pick_best_product",
                    "search_web_products",
                }:
                    gallery = get_gallery_items(session)
                    products = _serialize_products(
                        [session.active_product]
                        if tool == "get_product_details" and session.active_product
                        else session.last_searched_products
                    )
                yield render_chat(), gallery, render_panel()
            elif etype == "products":
                prods = event.get("products", [])
                if prods:
                    products = prods
                    gallery = products_to_gallery(prods)
                yield render_chat(), gallery, render_panel()
            elif etype == "content":
                answer += event.get("text", "")
                yield render_chat(), gallery, render_panel()

        telemetry_badge = _telemetry_badge(session_id)

        if not answer and not trace_lines:
            yield (
                f"Done — no textual response generated.{telemetry_badge}",
                gallery,
                render_panel(),
            )
        else:
            yield render_chat() + telemetry_badge, gallery, render_panel()
