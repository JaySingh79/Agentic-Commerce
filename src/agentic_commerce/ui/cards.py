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

from agentic_commerce.backend.analyst import extract_fabric

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
    details: list[tuple[str, str]] | None = None,
    highlight: str = "",
    compact: bool = False,
) -> str:
    """Renders one escaped card.

    The media and title are wrapped in the outbound link, but the ``<details>``
    disclosure is a *sibling* of that anchor, never a child: an interactive
    element inside an ``<a>`` is invalid HTML and clicking it would navigate away
    instead of expanding. ``<details>``/``<summary>`` is used rather than a
    scripted dropdown so the layer works with no JavaScript in the ``gr.HTML``
    panel and stays keyboard-accessible for free.
    """
    img = _safe_url(image) or _PLACEHOLDER
    safe_title = html.escape(title or "Product")
    safe_sub = html.escape(subtitle)
    safe_badge = html.escape(badge)

    body = (
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
        clickable = (
            f'<a class="ac-card-link" href="{html.escape(href, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer nofollow">{body}</a>'
        )
    else:
        clickable = f'<div class="ac-card-link">{body}</div>'

    classes = "ac-card ac-card-best" if highlight else "ac-card"
    if compact:
        classes += " ac-card-compact"
    ribbon = f'<div class="ac-card-ribbon">{html.escape(highlight)}</div>' if highlight else ""
    return f'<div class="{classes}">{ribbon}{clickable}{_details_block(details)}</div>'


def _details_block(details: list[tuple[str, str]] | None) -> str:
    """Renders the click-to-expand detail layer, or nothing when there is no data.

    An empty disclosure that opens onto blank space is worse than no control, so
    rows with no value are dropped and the whole block is omitted if none remain.
    """
    rows = [(label, value) for label, value in (details or []) if str(value).strip()]
    if not rows:
        return ""
    items = "".join(
        f'<div class="ac-detail-row"><span class="ac-detail-key">{html.escape(str(label))}</span>'
        f'<span class="ac-detail-val">{html.escape(str(value))}</span></div>'
        for label, value in rows
    )
    return (
        '<details class="ac-card-more">'
        "<summary>Details</summary>"
        f'<div class="ac-detail-list">{items}</div>'
        "</details>"
    )


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


def render_product_grid(products: list[dict[str, Any]],
    best_pick: dict[str, Any] | None = None,
) -> str:
    """Renders UCP catalog products as a grid of cards.

    *best_pick* is the Analyst's verdict (``BestPick.as_dict()``); when supplied,
    the winning product is ribboned and its reasoning is folded into that card's
    detail layer, so the recommendation sits on the product rather than only in
    the transcript.
    """
    winner_id = (best_pick or {}).get("winner", {}).get("product_id", "") if best_pick else ""
    reasons = _winner_reasons(best_pick)

    cards = []
    for product in products:
        media = product.get("media") or []
        image = next(
            (m.get("url") for m in media if isinstance(m, dict) and m.get("url")),
            None,
        )
        price = _price_text(product.get("price_cents")) or _range_price_text(product)
        variants = product.get("variants") or []
        first = variants[0] if variants and isinstance(variants[0], dict) else {}
        link = first.get("url") or first.get("checkout_url")

        is_winner = bool(winner_id) and str(product.get("id", "")) == winner_id
        details = _product_details(product, price, first)
        if is_winner:
            details = reasons + details

        cards.append(
            _card(
                image=image,
                title=str(product.get("title", "Product")),
                subtitle=price,
                badge="Shopify catalog",
                link=link,
                details=details,
                highlight="★ Best pick" if is_winner else "",
            )
        )
    return _grid("Catalog matches", cards)


def _range_price_text(product: dict[str, Any]) -> str:
    """Display price from the UCP ``price_range.min`` shape."""
    minimum = (product.get("price_range") or {}).get("min") or {}
    amount = minimum.get("amount")
    if amount is None:
        return ""
    currency = str(minimum.get("currency") or "").strip()
    try:
        formatted = f"{float(amount) / 100:.2f}"
    except (TypeError, ValueError):
        return ""
    return f"{formatted} {currency}".strip() if currency else f"${formatted}"


def _rating_text(product: dict[str, Any]) -> str:
    """Rating summary from the product or, failing that, its first rated variant."""
    rating = product.get("rating")
    if not isinstance(rating, dict) or rating.get("value") is None:
        rating = next(
            (
                v.get("rating")
                for v in product.get("variants") or []
                if isinstance(v, dict)
                and isinstance(v.get("rating"), dict)
                and v["rating"].get("value") is not None
            ),
            None,
        )
    if not isinstance(rating, dict) or rating.get("value") is None:
        return ""
    scale = rating.get("scale_max") or 5
    count = int(rating.get("count") or 0)
    suffix = f" ({count} review{'s' if count != 1 else ''})" if count else ""
    return f"{rating['value']:g}/{scale:g}{suffix}"


def _product_details(
    product: dict[str, Any], price: str, variant: dict[str, Any]
) -> list[tuple[str, str]]:
    """The short, relevant facts shown when a catalog card is expanded."""
    options = product.get("options") or []
    option_text = ", ".join(
        f"{o.get('name')} ({len(o.get('values') or [])})"
        for o in options
        if isinstance(o, dict) and o.get("name")
    )
    availability = variant.get("availability")
    stock = ""
    if isinstance(availability, dict) and "available" in availability:
        stock = "In stock" if availability.get("available") else "Unavailable"

    return [
        ("Price", price),
        ("Rating", _rating_text(product)),
        ("Fabric", extract_fabric(product)),
        ("Options", option_text),
        ("Availability", stock),
        ("Seller", str(variant.get("seller") or "")),
    ]


def _winner_reasons(best_pick: dict[str, Any] | None) -> list[tuple[str, str]]:
    """The Analyst's evidence, ordered by contribution, as detail rows."""
    winner = (best_pick or {}).get("winner") if best_pick else None
    if not winner:
        return []
    criteria = sorted(
        winner.get("criteria") or [],
        key=lambda c: float(c.get("score", 0)) * float(c.get("weight", 0)),
        reverse=True,
    )
    rows = [
        (str(c.get("name", "")).replace("_", " ").title(), str(c.get("evidence", "")))
        for c in criteria
    ]
    missing = winner.get("missing") or []
    if missing:
        rows.append(("Not scored", ", ".join(str(m) for m in missing)))
    return rows


def render_web_grid(results: list[dict[str, Any]]) -> str:
    """Renders external web listings as a grid of linked cards.

    A listing with no discoverable ``og:image`` falls back to its favicon
    (``backend/web_search.favicon_for``). Those are rendered compact rather than
    stretched into the square photo slot, so the difference between "here is the
    product" and "here is the site it is on" is visible at a glance (§5.1).

    No card here carries a cart control: web listings are not UCP products and
    cannot enter a Universal Cart. That is expressed by the affordance being
    absent, not by a disabled button.
    """
    cards = []
    for result in results:
        snippet = str(result.get("snippet") or "")
        photo = _safe_url(result.get("image_url"))
        cards.append(
            _card(
                image=photo or result.get("favicon_url"),
                title=str(result.get("title", "Listing")),
                subtitle=str(result.get("source", "")),
                badge="Web",
                link=result.get("url"),
                details=[
                    ("Site", str(result.get("source") or "")),
                    ("About", snippet[:220] + ("…" if len(snippet) > 220 else "")),
                ],
                compact=photo is None,
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
    best_pick: dict[str, Any] | None = None,
) -> str:
    """Combines catalog and web grids into the single results panel."""
    sections = [
        render_product_grid(products or [], best_pick),
        render_web_grid(web_results or []),
    ]
    body = "".join(s for s in sections if s)
    if not body:
        return (
            '<div class="ac-empty">Search for a product to see visual results here.</div>'
        )
    return body
