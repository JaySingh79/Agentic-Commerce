"""Tests for the multi-agent crew: parallel scouts and specialist delegation."""

import asyncio
import time
from typing import Any

import pytest

from agentic_commerce.backend.crew import (
    CATALOG_SCOUT,
    WEB_SCOUT,
    CommerceCrew,
)
from agentic_commerce.backend.web_search import WebResult

CATALOG_HIT = {"id": "gid://1", "title": "Trail Runner", "price_range": {"min": {"amount": 9900}}}
WEB_HIT = WebResult("Nike Pegasus", "https://nike.com/p", "fast", "nike.com")


class _StubClient:
    """Catalog client whose latency we control."""

    def __init__(self, delay: float = 0.0, hits: int = 1, boom: bool = False):
        self.delay, self.hits, self.boom = delay, hits, boom

    async def search_catalog(self, **_: Any) -> list[dict[str, Any]]:
        await asyncio.sleep(self.delay)
        if self.boom:
            raise RuntimeError("catalog offline")
        return [dict(CATALOG_HIT, id=f"gid://{i}") for i in range(self.hits)]


@pytest.fixture
def stub_web(monkeypatch: pytest.MonkeyPatch):
    """Replaces the web scout's search with a controllable stub."""

    def _install(delay: float = 0.0, hits: int = 1, boom: bool = False):
        async def _search(query: str, limit: int = 5, with_images: bool = True):
            await asyncio.sleep(delay)
            if boom:
                raise RuntimeError("web offline")
            return [WEB_HIT] * hits

        monkeypatch.setattr("agentic_commerce.backend.crew.search_web", _search)

    return _install


def test_scouts_run_concurrently_not_sequentially(stub_web):
    """Total time must track the slower scout, not the sum of both."""
    stub_web(delay=0.4, hits=1)
    crew = CommerceCrew(client=_StubClient(delay=0.4, hits=1), thin_result_threshold=99)

    start = time.perf_counter()
    report = crew.run_discovery("shoes")
    elapsed = time.perf_counter() - start

    assert report.catalog_products, "catalog scout returned nothing"
    assert report.web_results, "web scout returned nothing"
    # Sequential would be ~0.8s; concurrent should be well under that.
    assert elapsed < 0.7, f"scouts appear to run sequentially ({elapsed:.2f}s)"


def test_web_results_held_back_when_catalog_is_healthy(stub_web):
    """A good catalog result shouldn't be buried under external links."""
    stub_web(hits=5)
    crew = CommerceCrew(client=_StubClient(hits=5), thin_result_threshold=3)

    report = crew.run_discovery("shoes")

    assert report.has_catalog_hits
    assert report.web_results == []


def test_web_results_surface_when_catalog_is_thin(stub_web):
    stub_web(hits=4)
    crew = CommerceCrew(client=_StubClient(hits=1), thin_result_threshold=3)

    report = crew.run_discovery("obscure item")

    assert len(report.web_results) == 4


def test_one_scout_failing_does_not_kill_the_other(stub_web):
    """return_exceptions=True keeps a partial result usable."""
    stub_web(hits=2)
    crew = CommerceCrew(client=_StubClient(boom=True), thin_result_threshold=3)

    report = crew.run_discovery("shoes")

    assert report.catalog_products == []
    assert len(report.web_results) == 2
    errors = [e for e in report.events if e.status == "error"]
    assert len(errors) == 1 and errors[0].agent == CATALOG_SCOUT


def test_web_failure_leaves_catalog_intact(stub_web):
    stub_web(boom=True)
    crew = CommerceCrew(client=_StubClient(hits=2), thin_result_threshold=99)

    report = crew.run_discovery("shoes")

    assert len(report.catalog_products) == 2
    assert [e.agent for e in report.events if e.status == "error"] == [WEB_SCOUT]


def test_events_name_each_specialist(stub_web):
    stub_web(hits=1)
    crew = CommerceCrew(client=_StubClient(hits=1), thin_result_threshold=99)

    agents = {e.agent for e in crew.run_discovery("shoes").events}

    assert CATALOG_SCOUT in agents
    assert WEB_SCOUT in agents


def test_negotiation_delegates_and_respects_budget():
    crew = CommerceCrew(client=_StubClient())
    result = crew.run_negotiation(
        item="Trail Runner", list_price_cents=15000, budget_cents=12000
    )
    if result.agreed:
        assert result.final_price_cents <= 12000


def test_payment_delegates_to_the_cashier(monkeypatch: pytest.MonkeyPatch):
    for name in ("RAZORPAY_KEY_ID", "RAZORPAY_KEY_SECRET", "STRIPE_SECRET_KEY", "STRIPE_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    result = CommerceCrew(client=_StubClient()).run_payment(2400, "USD")

    assert result.provider == "simulated"
    assert result.amount_cents == 2400
