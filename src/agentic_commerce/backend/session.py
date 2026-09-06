"""Session state and conversation history management for continuous multi-turn commerce."""

import secrets
from contextvars import ContextVar
from dataclasses import dataclass, field, fields
from typing import Any

from agentic_commerce.backend.session_store import get_store

DEFAULT_SESSION_ID = "default_user_session"

# Fallback-only session routing. This is NOT reliable across Gradio's per-step
# threadpool (a var set inside a generator is invisible to a later step running
# on another worker), so anything correctness-critical must pass session_id
# explicitly — see agentic_commerce.backend.tools.make_commerce_tools.
_ACTIVE_SESSION_ID: ContextVar[str] = ContextVar("active_session_id", default=DEFAULT_SESSION_ID)


@dataclass
class CommerceSession:
    """Maintains stateful memory across multiple conversation turns."""

    history: list[dict[str, str]] = field(default_factory=list)
    last_searched_products: list[dict[str, Any]] = field(default_factory=list)
    last_web_results: list[dict[str, Any]] = field(default_factory=list)
    active_product: dict[str, Any] | None = None
    active_cart: dict[str, Any] | None = None
    active_mandate: dict[str, Any] | None = None
    active_checkout: dict[str, Any] | None = None
    active_negotiation: dict[str, Any] | None = None
    active_payment: dict[str, Any] | None = None
    best_pick: dict[str, Any] | None = None
    last_crew_events: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = DEFAULT_SESSION_ID

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot of every field, for the durable store."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CommerceSession":
        """Rebuilds a session from a snapshot, ignoring keys this version dropped."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in payload.items() if k in known})

    def save(self) -> None:
        """Persists the session so a refresh or restart does not lose the cart.

        Storage failures are reported and swallowed *here only*: a snapshot write is
        a side effect of a commerce action, and failing the shopper's add-to-cart
        because the disk is full would be worse than losing durability.
        """
        try:
            get_store().save(self.session_id, self.to_dict())
        except Exception as exc:  # noqa: BLE001 - see docstring
            import sys

            print(f"[session] snapshot not saved for {self.session_id}: {exc}", file=sys.stderr)

    def add_message(self, role: str, content: str) -> None:
        """Appends a message to the session history."""
        self.history.append({"role": role, "content": content})
        self.save()

    def update_search_results(self, products: list[dict[str, Any]]) -> None:
        """Stores the latest searched products.

        The Analyst's verdict is dropped here: it refers to the *previous* result
        set, and a stale "best pick" badge on a fresh search is worse than none.
        """
        self.last_searched_products = products
        self.best_pick = None
        self.save()

    def update_negotiation(self, negotiation: dict[str, Any]) -> None:
        """Stores the latest A2A negotiation outcome and transcript."""
        self.active_negotiation = negotiation
        self.save()

    def update_payment(self, payment: dict[str, Any]) -> None:
        """Stores the latest test-mode payment receipt."""
        self.active_payment = payment
        self.save()

    def update_web_results(self, results: list[dict[str, Any]]) -> None:
        """Stores the latest internet search results (non-UCP listings)."""
        self.last_web_results = results
        self.save()

    def update_best_pick(self, best_pick: dict[str, Any] | None) -> None:
        """Stores the Analyst's verdict so the UI can badge the winning card."""
        self.best_pick = best_pick
        self.save()

    def update_crew_events(self, events: list[dict[str, Any]]) -> None:
        """Stores the specialists' progress reports for the agent-activity surface.

        Without this the crew's own record of who ran, how long they took, and who
        failed is produced and then discarded, leaving the UI unable to tell an empty
        result from a broken provider.
        """
        self.last_crew_events = events
        self.save()

    def clear_search_results(self) -> None:
        """Clears every product source the gallery reads from.

        ``active_product`` must be cleared too: ``get_gallery_items`` prefers it
        over search results, so leaving it set made the gallery repopulate on the
        next render after the user pressed "Clear Gallery".
        """
        self.last_searched_products = []
        self.last_web_results = []
        self.active_product = None
        self.best_pick = None
        self.last_crew_events = []
        self.save()

    def update_cart(self, cart: dict[str, Any]) -> None:
        """Stores the active Universal Cart."""
        self.active_cart = cart
        self.save()

    def update_mandate(self, mandate: dict[str, Any]) -> None:
        """Stores the active AP2 Payment Mandate."""
        self.active_mandate = mandate
        self.save()

    def update_active_product(self, product: dict[str, Any] | None) -> None:
        """Stores the product the shopper is currently looking at."""
        self.active_product = product
        self.save()

    def update_checkout(self, checkout: dict[str, Any]) -> None:
        """Stores the active UCP checkout session."""
        self.active_checkout = checkout
        self.save()

    def get_context_summary(self) -> str:
        """Renders an informative context block of the current session state for the LLM."""
        lines = []
        if self.last_searched_products:
            lines.append("### Active Catalog Search Results in Context:")
            for idx, p in enumerate(self.last_searched_products[:5], 1):
                title = p.get("title", "Product")
                pid = p.get("id", "")
                price = p.get("price_range", {}).get("min", {}).get("amount", 0)
                lines.append(f"  [{idx}] {title} (${(price / 100):.2f}) | ID: {pid}")

        if self.active_cart:
            cid = self.active_cart.get("id", "Unknown")
            merchant = self.active_cart.get("merchant_domain", "Unknown")
            totals = self.active_cart.get("totals", [])
            total_amt = next((t.get("amount") for t in totals if t.get("type") == "total"), 0)
            lines.append(
                f"\n### Active Universal Cart:\n"
                f"  - Cart ID: {cid}\n"
                f"  - Merchant: {merchant}\n"
                f"  - Total: ${(total_amt / 100):.2f}\n"
                f"  - Continue URL: {self.active_cart.get('continue_url', 'N/A')}"
            )

        if self.active_mandate:
            mid = self.active_mandate.get("mandate_id", "Unknown")
            cents = self.active_mandate.get("amount_cents", 0)
            currency = self.active_mandate.get("currency", "USD")
            lines.append(
                f"\n### Active AP2 Payment Mandate:\n"
                f"  - Mandate ID: {mid}\n"
                f"  - Authorized Amount: ${(cents / 100):.2f} {currency}\n"
                f"  - Status: {self.active_mandate.get('status', 'PENDING')}"
            )

        return "\n".join(lines) if lines else "No prior commerce actions yet."


# Global session registry for managing active user sessions
_SESSION_STORE: dict[str, CommerceSession] = {}


def new_session_id() -> str:
    """Mints an unguessable session id.

    The id is a capability token — anyone holding it can read the associated cart,
    mandate and payment history, and it now also unlocks a *durable* snapshot — so it
    is random rather than derived from anything guessable.
    """
    return f"s_{secrets.token_urlsafe(18)}"


def set_active_session_id(session_id: str) -> None:
    """Sets the active session ID for the current context."""
    _ACTIVE_SESSION_ID.set(session_id)


def get_or_create_session(session_id: str | None = None) -> CommerceSession:
    """Retrieves or creates a stateful session.

    On a miss the durable store is consulted before a blank session is minted, so a
    reconnecting client that presents a known id gets its cart, mandate and
    negotiation back instead of starting over.
    """
    sid = session_id or _ACTIVE_SESSION_ID.get()
    if sid not in _SESSION_STORE:
        snapshot = None
        try:
            snapshot = get_store().load(sid)
        except Exception as exc:  # noqa: BLE001 - an unreadable store must not block a turn
            import sys

            print(f"[session] snapshot not restored for {sid}: {exc}", file=sys.stderr)
        session = CommerceSession.from_dict(snapshot) if snapshot else CommerceSession()
        session.session_id = sid
        _SESSION_STORE[sid] = session
    return _SESSION_STORE[sid]


def forget_session(session_id: str) -> None:
    """Drops a session from memory and from the durable store."""
    _SESSION_STORE.pop(session_id, None)
    get_store().delete(session_id)
