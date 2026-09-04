"""Safe HTML rendering of product result cards for the Gradio UI.

Why this exists rather than Markdown in the chat bubble: Gradio's chatbot
sanitizes standard HTML (``allow_tags`` only preserves *custom* tags), so a
Markdown image list is the only thing that renders there — and Markdown images
stack vertically, one per line. Disabling sanitization is not an option because
web-search titles and snippets are attacker-influenced text.

So cards are rendered into a dedicated ``gr.HTML`` panel instead, which we
control end to end. **Every interpolated value is escaped** with
:func:`html.escape`, and URLs are scheme-checked by :func:`_safe_url`, so a
malicious result title or a ``javascript:`` link cannot execute.
"""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}
_PLACEHOLDER = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='300'>"
    "<rect width='100%25' height='100%25' fill='%23222a3d'/>"
    "<text x='50%25' y='50%25' fill='%23788' font-family='sans-serif' "
    "font-size='16' text-anchor='middle'>no image</text></svg>"
)


def _safe_url(url: str | None) -> str | None:
    """Returns *url* only when it is an ordinary http(s) link.

    Blocks ``javascript:``/``data:`` URLs that would otherwise become an XSS
    sink once interpolated into an ``href`` or ``src``.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        return None
    return url


def _price_text(price_cents: Any) -> str:
    """Formats minor units as a display price, or '' when unknown."""
    if price_cents is None:
        return ""
    try:
        return f"${float(price_cents) / 100:.2f}"
    except (TypeError, ValueError):
        return ""


def _card(
    *,
    image: str | None,
    title: str,
    subtitle: str,
    badge: str,
    link: str | None,
) -> str:
    """Renders one escaped card; the whole card is clickable when *link* is set."""
    img = _safe_url(image) or _PLACEHOLDER
    safe_title = html.escape(title or "Product")
    safe_sub = html.escape(subtitle)
    safe_badge = html.escape(badge)

    inner = (
        f'<div class="ac-card-media"><img src="{html.escape(img, quote=True)}" '
        f'alt="{safe_title}" loading="lazy"></div>'
        f'<div class="ac-card-body">'
        f'<div class="ac-card-title" title="{safe_title}">{safe_title}</div>'
        f'<div class="ac-card-sub">{safe_sub}</div>'
        f'{f"<span class=ac-card-badge>{safe_badge}</span>" if safe_badge else ""}'
        f"</div>"
    )

    href = _safe_url(link)
    if href:
        return (
            f'<a class="ac-card" href="{html.escape(href, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer nofollow">{inner}</a>'
        )
    return f'<div class="ac-card">{inner}</div>'


def _grid(title: str, cards: list[str], note: str = "") -> str:
    """Wraps cards in the responsive grid container."""
    if not cards:
        return ""
    note_html = f'<div class="ac-grid-note">{html.escape(note)}</div>' if note else ""
    return (
        f'<div class="ac-results">'
        f'<div class="ac-grid-head">{html.escape(title)}</div>'
        f"{note_html}"
        f'<div class="ac-grid">{"".join(cards)}</div>'
        f"</div>"
    )


def render_product_grid(products: list[dict[str, Any]]) -> str:
    """Renders UCP catalog products as a grid of cards."""
    cards = []
    for product in products:
        media = product.get("media") or []
        image = next(
            (m.get("url") for m in media if isinstance(m, dict) and m.get("url")),
            None,
        )
        price = _price_text(product.get("price_cents"))
        variants = product.get("variants") or []
        link = None
        if variants and isinstance(variants[0], dict):
            link = variants[0].get("url") or variants[0].get("checkout_url")
        cards.append(
            _card(
                image=image,
                title=str(product.get("title", "Product")),
                subtitle=price,
                badge="Shopify catalog",
                link=link,
            )
        )
    return _grid("Catalog matches", cards)


def render_web_grid(results: list[dict[str, Any]]) -> str:
    """Renders external web listings as a grid of linked cards."""
    cards = []
    for result in results:
        cards.append(
            _card(
                image=result.get("image_url") or result.get("favicon_url"),
                title=str(result.get("title", "Listing")),
                subtitle=str(result.get("source", "")),
                badge="Web",
                link=result.get("url"),
            )
        )
    return _grid(
        "Web results",
        cards,
        note="External listings — open in a new tab; not addable to a Universal Cart.",
    )


def render_results_panel(
    products: list[dict[str, Any]] | None,
    web_results: list[dict[str, Any]] | None,
) -> str:
    """Combines catalog and web grids into the single results panel."""
    sections = [
        render_product_grid(products or []),
        render_web_grid(web_results or []),
    ]
    body = "".join(s for s in sections if s)
    if not body:
        return (
            '<div class="ac-empty">Search for a product to see visual results here.</div>'
        )
    return body
