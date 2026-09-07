"""Multi-agent crew: a concierge converses while specialists work in parallel.

The single-agent loop was strictly sequential — catalog search, then web search,
then negotiation — so the shopper waited on each step in turn and saw nothing
until the model spoke again.

Here the **concierge** owns the conversation, and specialist agents run
concurrently on the shared background event loop:

===================  =======================================================
Specialist           Responsibility
===================  =======================================================
``CatalogScout``     Shopify UCP Global Catalog search
``WebScout``         Open-web listings (used to widen a thin catalog result)
``Negotiator``       A2A price negotiation against the merchant agent
``Cashier``          Test-mode payment capture (Razorpay/Stripe/simulated)
``Analyst``          Ranks a result set and names the best fit for the shopper
===================  =======================================================

``run_discovery`` fans the two scouts out with :func:`asyncio.gather`, so total
latency is the slower scout rather than their sum. Each specialist reports
progress as a :class:`CrewEvent` that the UI renders live, which is what makes
the collaboration visible instead of a black box.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from agentic_commerce.a2a.buyer_agent import BuyerAgent
from agentic_commerce.a2a.merchant_agent import MerchantAgent
from agentic_commerce.a2a.negotiation import NegotiationResult, negotiate
from agentic_commerce.backend.analyst import BestPick, pick_best
from agentic_commerce.backend.payments import (
    PaymentResult,
    authorize_payment,
    process_payment,
)
from agentic_commerce.backend.web_search import WebResult, search_web
from agentic_commerce.core.runtime import run_async
from agentic_commerce.core.telemetry import trace_tool_execution
from agentic_commerce.ucp.client import ShopifyUcpClient

#: Display names surfaced in the UI trace.
CONCIERGE = "Concierge"
CATALOG_SCOUT = "CatalogScout"
WEB_SCOUT = "WebScout"
NEGOTIATOR = "Negotiator"
CASHIER = "Cashier"
ANALYST = "Analyst"


@dataclass(frozen=True)
class CrewEvent:
    """A progress report from one specialist agent."""

    agent: str
    status: str
    detail: str
    payload: dict[str, Any] = field(default_factory=dict)
    #: Seconds this specialist took, on terminal events. The UI shows elapsed time
    #: rather than a progress bar because completion percentage is unknowable.
    duration: float | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serializable form for the UI event stream."""
        return {
            "agent": self.agent,
            "status": self.status,
            "detail": self.detail,
            "duration": self.duration,
            **({"payload": self.payload} if self.payload else {}),
        }


@dataclass
class DiscoveryReport:
    """Combined result of the two scouts running concurrently."""

    catalog_products: list[dict[str, Any]] = field(default_factory=list)
    web_results: list[WebResult] = field(default_factory=list)
    events: list[CrewEvent] = field(default_factory=list)

    @property
    def has_catalog_hits(self) -> bool:
        """Whether the UCP catalog returned anything purchasable."""
        return bool(self.catalog_products)


class CommerceCrew:
    """Coordinates the specialist agents on behalf of the concierge."""

    def __init__(self, client: ShopifyUcpClient | None = None, thin_result_threshold: int = 3):
        self.client = client or ShopifyUcpClient()
        # Below this many catalog hits the web scout's findings are worth showing.
        self.thin_result_threshold = thin_result_threshold

    # ------------------------------------------------------------- scouts

    async def _catalog_scout(
        self, query: str, price_max_cents: int | None, limit: int
    ) -> tuple[list[dict[str, Any]], float]:
        """Searches the Shopify Global Catalog, reporting its own elapsed time."""
        started = time.perf_counter()
        products = await self.client.search_catalog(
            query=query, price_max=price_max_cents, limit=limit
        )
        return products, time.perf_counter() - started

    async def _web_scout(
        self, query: str, max_price: float | None, limit: int
    ) -> tuple[list[WebResult], float]:
        """Searches the open web for comparable listings, reporting elapsed time."""
        started = time.perf_counter()
        phrase = f"{query} under ${max_price:.0f}" if max_price else query
        results = await search_web(phrase, limit=limit)
        return results, time.perf_counter() - started

    async def _gather(
        self, query: str, max_price: float | None, limit: int
    ) -> tuple[Any, Any]:
        """Runs both scouts concurrently; neither failure cancels the other."""
        price_max_cents = int(max_price * 100) if max_price else None
        return await asyncio.gather(
            self._catalog_scout(query, price_max_cents, limit),
            self._web_scout(query, max_price, limit),
            return_exceptions=True,
        )

    def run_discovery(
        self, query: str, max_price: float | None = None, limit: int = 5
    ) -> DiscoveryReport:
        """Fans out both scouts in parallel and merges their findings."""
        report = DiscoveryReport()
        report.events.append(
            CrewEvent(CONCIERGE, "dispatch", f"Briefing scouts on “{query}”")
        )

        with trace_tool_execution("crew_discovery", {"query": query, "max_price": max_price}):
            catalog, web = run_async(self._gather(query, max_price, limit))

        if isinstance(catalog, Exception):
            report.events.append(
                CrewEvent(CATALOG_SCOUT, "error", f"Catalog unavailable: {catalog}")
            )
        else:
            products, elapsed = catalog
            report.catalog_products = products
            report.events.append(
                CrewEvent(
                    CATALOG_SCOUT,
                    "done",
                    f"{len(products)} catalog match(es)",
                    {"count": len(products)},
                    duration=elapsed,
                )
            )

        if isinstance(web, Exception):
            report.events.append(CrewEvent(WEB_SCOUT, "error", f"Web search failed: {web}"))
        else:
            listings, elapsed = web
            report.web_results = listings
            report.events.append(
                CrewEvent(
                    WEB_SCOUT,
                    "done",
                    f"{len(listings)} web listing(s)",
                    {"count": len(listings)},
                    duration=elapsed,
                )
            )

        # The web scout's findings are only surfaced when the catalog is thin,
        # so a healthy catalog result isn't buried under external links.
        if len(report.catalog_products) >= self.thin_result_threshold:
            report.web_results = []
            report.events.append(
                CrewEvent(CONCIERGE, "filter", "Catalog covered it; holding web results back")
            )

        return report

    # -------------------------------------------------------- negotiation

    def run_negotiation(
        self,
        item: str,
        list_price_cents: int,
        budget_cents: int,
        target_cents: int | None = None,
        merchant_name: str = "MerchantAgent",
        tier: str = "standard",
        floor_ratio: float = 0.72,
    ) -> NegotiationResult:
        """Negotiates a price via A2A on the shopper's behalf.

        The merchant's true cost is private in a real deployment; here the floor
        is modelled as a fixed ratio of list price so the demo is deterministic.
        """
        buyer = BuyerAgent(
            name="BuyerAgent",
            max_price_cents=budget_cents,
            target_price_cents=target_cents or int(budget_cents * 0.85),
        )
        merchant = MerchantAgent(
            name=merchant_name,
            list_price_cents=list_price_cents,
            floor_cents=int(list_price_cents * floor_ratio),
            tier=tier,
        )
        return negotiate(buyer, merchant, item)

    # ------------------------------------------------------------ analyst

    def run_analysis(self, products: list[dict[str, Any]], need: str = "") -> BestPick:
        """Delegates ranking to the Analyst and names the best fit for *need*.

        Scoring is deterministic (see :mod:`agentic_commerce.backend.analyst`), so
        the same result set and need always produce the same verdict — the
        shopper can be told *why* something won.
        """
        with trace_tool_execution(
            "crew_analysis", {"candidates": len(products), "need": need}
        ):
            return pick_best(products, need)

    # ------------------------------------------------------------ cashier

    def run_payment(
        self, amount_cents: int, currency: str = "USD", receipt: str | None = None
    ) -> PaymentResult:
        """Authorizes a test-mode payment through the configured provider.

        Unmandated: this is the raw provider path, kept for the ``/api/payments/test``
        endpoint. Anything driven by the agent goes through :meth:`run_authorization`
        so an AP2 mandate actually authorizes the charge.
        """
        with trace_tool_execution(
            "crew_payment", {"amount_cents": amount_cents, "currency": currency}
        ):
            return run_async(process_payment(amount_cents, currency, receipt))

    def run_authorization(
        self,
        mandate: dict[str, Any],
        session_id: str = "",
        amount_cents: int | None = None,
        currency: str | None = None,
        expected_cart_id: str | None = None,
        expected_merchant: str | None = None,
        engine: Any = None,
    ) -> PaymentResult:
        """Authorizes a charge against a signed, unspent, in-scope AP2 mandate.

        ``engine`` must be the AP2 engine that signed the mandate; verification is
        meaningless against a different key.
        """
        with trace_tool_execution(
            "crew_authorization",
            {"mandate_id": str(mandate.get("mandate_id", "")), "currency": currency or ""},
            session_id=session_id or None,
        ):
            return run_async(
                authorize_payment(
                    mandate,
                    session_id=session_id,
                    amount_cents=amount_cents,
                    currency=currency,
                    expected_cart_id=expected_cart_id,
                    expected_merchant=expected_merchant,
                    engine=engine,
                )
            )


def stream_events(events: list[CrewEvent]) -> Iterator[dict[str, Any]]:
    """Adapts crew events to the UI's event dictionaries."""
    for event in events:
        yield {"type": "crew", **event.as_dict()}
