"""A session rendered as a knowledge graph, for cheap context transfer.

The problem this solves: handing a session to another agent — a fresh turn, a
different model, a support tool — currently means shipping either the raw
transcript (long, repetitive, mostly prose) or the raw session snapshot (long,
deeply nested JSON with fields nobody downstream reads). Both spend a lot of
tokens restating structure the reader has to re-derive anyway.

A commerce session is naturally a graph: queries found products, products belong
to merchants, a cart holds a variant, a mandate authorizes a cart, a payment
settles a mandate. Written as nodes and edges with short stable ids, the same
facts survive in a fraction of the characters, and the relationships that
actually matter are stated once instead of being implied by nesting.

Two renderings are produced from one build:

* :func:`build_graph` — JSON, for programmatic consumers.
* :func:`to_compact_text` — a line-based form for pasting into a prompt. It
  carries a legend so a model that has never seen the format can still read it.

Honesty rules carried over from the rest of the stack: an unknown rating is
absent rather than zero, a payment always states whether it was live, and a web
result is marked as non-purchasable so a downstream agent cannot mistake it for
catalog stock.
"""

from __future__ import annotations

from typing import Any

from agentic_commerce.backend.session import CommerceSession

# Rough tokens-per-character for English + identifiers. Used only to report the
# saving; it is an estimate and is labelled as one wherever it surfaces.
_CHARS_PER_TOKEN = 4


def _money(cents: Any, currency: str = "USD") -> str | None:
    """Minor units to a short display string, or None when there is no price."""
    try:
        return f"{float(cents) / 100:.2f}{currency or 'USD'}"
    except (TypeError, ValueError):
        return None


def _product_price(product: dict[str, Any]) -> str | None:
    variants = product.get("variants") or []
    first = variants[0] if variants and isinstance(variants[0], dict) else {}
    price = first.get("price") or {}
    if price.get("amount") is not None:
        return _money(price["amount"], str(price.get("currency") or "USD"))

    minimum = (product.get("price_range") or {}).get("min") or {}
    if minimum.get("amount") is not None:
        return _money(minimum["amount"], str(minimum.get("currency") or "USD"))
    return None


def _product_rating(product: dict[str, Any]) -> dict[str, Any] | None:
    """The rating sits on the product for some merchants, the variant for others."""
    rating = product.get("rating")
    if isinstance(rating, dict) and rating.get("value") is not None:
        return {"value": rating.get("value"), "count": rating.get("count")}
    for variant in product.get("variants") or []:
        candidate = variant.get("rating") if isinstance(variant, dict) else None
        if isinstance(candidate, dict) and candidate.get("value") is not None:
            return {"value": candidate.get("value"), "count": candidate.get("count")}
    return None


def _merchant_of(product: dict[str, Any]) -> str | None:
    for variant in product.get("variants") or []:
        seller = variant.get("seller") if isinstance(variant, dict) else None
        if isinstance(seller, dict) and seller.get("domain"):
            return str(seller["domain"])
    return None


def _fabric_of(product: dict[str, Any]) -> str | None:
    from agentic_commerce.backend.analyst import extract_fabric

    fabric = extract_fabric(product)
    return fabric or None


def build_graph(session: CommerceSession) -> dict[str, Any]:
    """Builds the node/edge graph for one session.

    Nodes carry short ids (``P1``, ``M1``, ``C1``) that the edges refer to, so a
    fact stated once is referenced by two characters rather than repeated.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    merchants: dict[str, str] = {}

    best_id = ""
    if session.best_pick:
        best_id = str((session.best_pick.get("winner") or {}).get("product_id") or "")

    product_ids: dict[str, str] = {}
    for index, product in enumerate(session.last_searched_products, 1):
        node_id = f"P{index}"
        product_ids[str(product.get("id") or "")] = node_id

        node: dict[str, Any] = {
            "id": node_id,
            "type": "product",
            "title": str(product.get("title") or "Product"),
            "ref": str(product.get("id") or ""),
        }
        price = _product_price(product)
        if price:
            node["price"] = price
        rating = _product_rating(product)
        if rating:
            node["rating"] = rating
        fabric = _fabric_of(product)
        if fabric:
            node["fabric"] = fabric
        variants = product.get("variants") or []
        first = variants[0] if variants and isinstance(variants[0], dict) else {}
        availability = first.get("availability") or {}
        if "available" in availability:
            node["available"] = bool(availability["available"])
        if best_id and str(product.get("id")) == best_id:
            node["best_pick"] = True
        nodes.append(node)

        domain = _merchant_of(product)
        if domain:
            if domain not in merchants:
                merchants[domain] = f"M{len(merchants) + 1}"
                nodes.append({"id": merchants[domain], "type": "merchant", "domain": domain})
            edges.append({"from": node_id, "rel": "sold_by", "to": merchants[domain]})

    for index, result in enumerate(session.last_web_results, 1):
        nodes.append({
            "id": f"W{index}",
            "type": "web_result",
            "title": str(result.get("title") or "Listing"),
            "source": str(result.get("source") or ""),
            "url": str(result.get("url") or ""),
            # Stated, not implied: a downstream agent must not try to cart this.
            "purchasable": False,
        })

    if session.best_pick:
        winner = session.best_pick.get("winner") or {}
        verdict: dict[str, Any] = {
            "id": "B1",
            "type": "verdict",
            "winner": product_ids.get(best_id, best_id),
            "score": winner.get("score"),
        }
        if winner.get("missing"):
            verdict["not_scored"] = winner["missing"]
        if session.best_pick.get("caveat"):
            verdict["caveat"] = session.best_pick["caveat"]
        nodes.append(verdict)
        if best_id in product_ids:
            edges.append({"from": "B1", "rel": "recommends", "to": product_ids[best_id]})

    if session.active_cart:
        cart = session.active_cart
        total = next(
            (t for t in cart.get("totals") or [] if t.get("type") == "total"),
            None,
        )
        node = {"id": "C1", "type": "cart", "ref": str(cart.get("id") or "")}
        if total:
            node["total"] = _money(total.get("amount"), str(total.get("currency") or "USD"))
        domain = str(cart.get("merchant_domain") or "")
        nodes.append(node)
        if domain:
            if domain not in merchants:
                merchants[domain] = f"M{len(merchants) + 1}"
                nodes.append({"id": merchants[domain], "type": "merchant", "domain": domain})
            edges.append({"from": "C1", "rel": "at", "to": merchants[domain]})

    if session.active_mandate:
        mandate = session.active_mandate
        nodes.append({
            "id": "A1",
            "type": "mandate",
            "ref": str(mandate.get("mandate_id") or ""),
            "amount": _money(mandate.get("amount_cents"), str(mandate.get("currency") or "USD")),
            "expires_at": mandate.get("expires_at"),
            "status": str(mandate.get("status") or ""),
        })
        if session.active_cart:
            edges.append({"from": "A1", "rel": "authorizes", "to": "C1"})

    if session.active_payment:
        payment = session.active_payment
        nodes.append({
            "id": "X1",
            "type": "payment",
            "provider": str(payment.get("provider") or ""),
            "status": str(payment.get("status") or ""),
            "amount": _money(payment.get("amount_cents"), str(payment.get("currency") or "USD")),
            # Required, never inferred: whether real money moved.
            "live": bool(payment.get("live")),
        })
        if session.active_mandate:
            edges.append({"from": "X1", "rel": "settles", "to": "A1"})

    if session.active_negotiation:
        negotiation = session.active_negotiation
        nodes.append({
            "id": "N1",
            "type": "negotiation",
            "agreed": bool(negotiation.get("agreed")),
            "final": _money(negotiation.get("final_price_cents")),
        })

    turns = [m for m in session.history if m.get("role") == "user"]
    return {
        "session_id": session.session_id,
        "nodes": nodes,
        "edges": edges,
        "turns": len(turns),
        "last_question": turns[-1]["content"] if turns else None,
    }


def to_compact_text(graph: dict[str, Any]) -> str:
    """Renders the graph as the line form meant for pasting into a prompt.

    The legend is included deliberately: a receiving model has not seen this
    format, and three lines of key spend fewer tokens than the JSON braces and
    repeated field names they replace.
    """
    if not graph["nodes"]:
        return f"session {graph['session_id']}: no commerce state yet"

    lines = [
        "# commerce session graph",
        "# legend: P=product M=merchant W=web(not purchasable) B=verdict "
        "C=cart A=mandate X=payment N=negotiation",
        f"# session {graph['session_id']} turns={graph['turns']}",
    ]
    if graph.get("last_question"):
        lines.append(f'Q "{graph["last_question"]}"')

    for node in graph["nodes"]:
        kind = node["type"]
        if kind == "product":
            parts = [f'{node["id"]} "{node["title"]}"']
            if node.get("price"):
                parts.append(node["price"])
            rating = node.get("rating")
            if rating:
                count = rating.get("count")
                parts.append(f"r{rating['value']}" + (f"/{count}" if count else ""))
            if node.get("fabric"):
                parts.append(f"fabric:{node['fabric']}")
            if node.get("available") is False:
                parts.append("oos")
            if node.get("best_pick"):
                parts.append("*best")
            lines.append(" ".join(parts))
        elif kind == "merchant":
            lines.append(f"{node['id']} {node['domain']}")
        elif kind == "web_result":
            lines.append(f'{node["id"]} "{node["title"]}" {node["source"]} !nocart')
        elif kind == "verdict":
            row = f"{node['id']} winner:{node['winner']}"
            if node.get("score") is not None:
                row += f" score:{node['score']}"
            if node.get("not_scored"):
                row += f" unscored:{','.join(str(m) for m in node['not_scored'])}"
            lines.append(row)
        elif kind == "cart":
            lines.append(f"{node['id']} cart {node.get('total') or ''} ref:{node['ref']}".strip())
        elif kind == "mandate":
            lines.append(
                f"{node['id']} mandate {node.get('amount') or ''} "
                f"status:{node.get('status')} exp:{node.get('expires_at')}"
            )
        elif kind == "payment":
            lines.append(
                f"{node['id']} payment {node.get('amount') or ''} "
                f"{node.get('provider')} {node.get('status')} "
                f"live:{'yes' if node.get('live') else 'no'}"
            )
        elif kind == "negotiation":
            lines.append(
                f"{node['id']} negotiation agreed:{'yes' if node['agreed'] else 'no'} "
                f"{node.get('final') or ''}".strip()
            )

    if graph["edges"]:
        lines.append(
            "E " + " ".join(f"{e['from']}-{e['rel']}->{e['to']}" for e in graph["edges"])
        )

    caveats = [n["caveat"] for n in graph["nodes"] if n.get("caveat")]
    lines.extend(f"# {c}" for c in caveats)
    return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """A characters-based estimate. Not a tokenizer; only used to report scale."""
    return max(len(text) // _CHARS_PER_TOKEN, 1)
