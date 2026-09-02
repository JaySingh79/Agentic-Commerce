"""Session state and conversation history management for continuous multi-turn commerce."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Context variable to track active session ID per request context
_ACTIVE_SESSION_ID: ContextVar[str] = ContextVar(
    "active_session_id", default="default_user_session"
)


@dataclass
class CommerceSession:
    """Maintains stateful memory across multiple conversation turns."""

    history: list[dict[str, str]] = field(default_factory=list)
    last_searched_products: list[dict[str, Any]] = field(default_factory=list)
    active_product: dict[str, Any] | None = None
    active_cart: dict[str, Any] | None = None
    active_mandate: dict[str, Any] | None = None
    active_checkout: dict[str, Any] | None = None

    def add_message(self, role: str, content: str) -> None:
        """Appends a message to the session history."""
        self.history.append({"role": role, "content": content})

    def update_search_results(self, products: list[dict[str, Any]]) -> None:
        """Stores the latest searched products."""
        self.last_searched_products = products

    def update_cart(self, cart: dict[str, Any]) -> None:
        """Stores the active Universal Cart."""
        self.active_cart = cart

    def update_mandate(self, mandate: dict[str, Any]) -> None:
        """Stores the active AP2 Payment Mandate."""
        self.active_mandate = mandate

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


def set_active_session_id(session_id: str) -> None:
    """Sets the active session ID for the current context."""
    _ACTIVE_SESSION_ID.set(session_id)


def get_or_create_session(session_id: str | None = None) -> CommerceSession:
    """Retrieves or creates a stateful session."""
    sid = session_id or _ACTIVE_SESSION_ID.get()
    if sid not in _SESSION_STORE:
        _SESSION_STORE[sid] = CommerceSession()
    return _SESSION_STORE[sid]
