"""Tests for the Analyst specialist that picks the best product in a result set.

The scoring rules that matter most here are the honesty rules: a missing signal
must not read as a bad score, and no field may be invented (there is no
purchase-volume data in the UCP catalog).
"""

from typing import Any

from agentic_commerce.backend.analyst import (
    POPULARITY_CAVEAT,
    extract_fabric,
    pick_best,
)


def product(
    pid: str,
    title: str,
    *,
    rating: float | None = None,
    count: int = 0,
    specs: str = "",
    amount: int | None = None,
    currency: str = "USD",
    available: bool = True,
) -> dict[str, Any]:
    """Builds a product shaped like a real UCP catalog hit."""
    p: dict[str, Any] = {
        "id": pid,
        "title": title,
        "variants": [
            {
                "id": f"{pid}/v1",
                "availability": {"available": available},
                "eligible": True,
            }
        ],
    }
    if rating is not None:
        p["rating"] = {"value": rating, "scale_min": 1, "scale_max": 5, "count": count}
    if specs:
        p["metadata"] = {"tech_specs": specs}
    if amount is not None:
        p["price_range"] = {"min": {"amount": amount, "currency": currency}}
    return p


COTTON = "Fabric: 100% Cotton\nSleeve Length: Short"
POLY = "Fabric: 88% Polyester, 12% Elastane"
WOOL = "Fabric: Merino Wool"


def test_no_products_yields_no_winner():
    pick = pick_best([], need="anything")
    assert pick.winner is None
    assert "run a catalog search" in pick.format_display()


def test_higher_rating_wins_all_else_equal():
    pick = pick_best(
        [
            product("a", "Weak", rating=3.0, count=40, amount=4000),
            product("b", "Strong", rating=4.8, count=40, amount=4000),
        ]
    )
    assert pick.winner.title == "Strong"
    assert pick.product_id == "b"


def test_review_count_breaks_a_rating_tie():
    """A 5.0 from one buyer is weaker evidence than a 5.0 from many."""
    pick = pick_best(
        [
            product("a", "One review", rating=5.0, count=1, amount=4000),
            product("b", "Many reviews", rating=5.0, count=400, amount=4000),
        ]
    )
    assert pick.winner.title == "Many reviews"


def test_fabric_is_matched_against_the_stated_need():
    """Polyester/elastane should win a sweat-wicking, stretchy need over cotton."""
    pick = pick_best(
        [
            product("a", "Cotton tee", rating=4.5, count=50, specs=COTTON, amount=4000),
            product("b", "Tech tee", rating=4.5, count=50, specs=POLY, amount=4000),
        ],
        need="stretchy top for sweaty gym training",
    )
    assert pick.winner.title == "Tech tee"
    assert "fabric_fit" in [c.name for c in pick.winner.criteria]


def test_the_same_products_flip_when_the_need_flips():
    """Fabric fit must follow the shopper, not a fixed preference for one fibre."""
    catalog = [
        product("a", "Cotton tee", rating=4.5, count=50, specs=COTTON, amount=4000),
        product("b", "Tech tee", rating=4.5, count=50, specs=POLY, amount=4000),
    ]
    assert pick_best(catalog, need="soft breathable shirt for hot weather").winner.title == (
        "Cotton tee"
    )
    assert pick_best(catalog, need="moisture wicking running shirt").winner.title == "Tech tee"


def test_warmth_need_selects_wool():
    pick = pick_best(
        [
            product("a", "Cotton tee", rating=4.6, count=60, specs=COTTON, amount=4000),
            product("b", "Merino base layer", rating=4.6, count=60, specs=WOOL, amount=4000),
        ],
        need="warm thermal layer for cold winter runs",
    )
    assert pick.winner.title == "Merino base layer"


def test_missing_rating_is_reported_not_scored_as_zero():
    """An undescribed product must not be ranked as though it scored badly."""
    unrated = product("a", "Unrated", specs=COTTON, amount=4000)
    pick = pick_best([unrated], need="soft cotton shirt")

    winner = pick.winner
    assert "rating" in winner.missing
    assert "popularity" in winner.missing
    assert "rating" not in [c.name for c in winner.criteria]
    # Perfect on every signal it *does* report, so the total stays high.
    assert winner.total > 0.9


def test_weights_are_renormalized_when_signals_are_absent():
    pick = pick_best([product("a", "Sparse", rating=4.0, count=10)], need="")
    assert abs(sum(c.weight for c in pick.winner.criteria) - 1.0) < 1e-9


def test_cheaper_wins_within_one_currency():
    pick = pick_best(
        [
            product("a", "Pricey", rating=4.5, count=50, amount=9000),
            product("b", "Value", rating=4.5, count=50, amount=3000),
        ]
    )
    assert pick.winner.title == "Value"


def test_prices_in_different_currencies_are_not_compared():
    """Ranking £18 against $65 by raw amount would be a scoring bug."""
    pick = pick_best(
        [
            product("a", "US item", rating=4.5, count=50, amount=6500, currency="USD"),
            product("b", "US item 2", rating=4.5, count=50, amount=4000, currency="USD"),
            product("c", "UK item", rating=4.5, count=50, amount=1833, currency="GBP"),
        ]
    )
    uk = next(s for s in pick.ranked if s.title == "UK item")
    assert any("price_value" in m and "GBP" in m for m in uk.missing)
    assert "price_value" not in [c.name for c in uk.criteria]


def test_out_of_stock_product_loses_to_an_available_one():
    pick = pick_best(
        [
            product("a", "Sold out", rating=5.0, count=50, amount=4000, available=False),
            product("b", "In stock", rating=4.6, count=50, amount=4000),
        ]
    )
    assert pick.winner.title == "In stock"


def test_verdict_names_the_evidence_and_the_runners_up():
    pick = pick_best(
        [
            product("a", "Alpha", rating=4.9, count=120, specs=POLY, amount=4000),
            product("b", "Beta", rating=3.2, count=8, specs=COTTON, amount=5200),
        ],
        need="moisture wicking running shirt",
    )
    text = pick.format_display()

    assert "Best pick: Alpha" in text
    assert "Beta" in text  # the comparison against the runner-up
    assert "4.9/5 rated" in text
    assert pick.comparison


def test_popularity_is_labelled_as_a_proxy_not_sales():
    """The catalog has no purchase-volume field; the UI must not imply otherwise."""
    pick = pick_best([product("a", "Alpha", rating=4.5, count=30, amount=4000)])
    popularity = next(c for c in pick.winner.criteria if c.name == "popularity")

    assert POPULARITY_CAVEAT in popularity.evidence
    assert pick.as_dict()["caveat"] == POPULARITY_CAVEAT
    assert "review count" in POPULARITY_CAVEAT


def test_fabric_fit_is_dropped_when_the_need_states_no_preference():
    pick = pick_best([product("a", "Alpha", rating=4.5, count=30, specs=COTTON)], need="a shirt")
    assert any("no material preference" in m for m in pick.winner.missing)


def test_extract_fabric_reads_the_spec_line():
    assert extract_fabric({"metadata": {"tech_specs": COTTON}}) == "100% Cotton"
    assert extract_fabric({"metadata": {"tech_specs": POLY}}) == "88% Polyester, 12% Elastane"


def test_extract_fabric_falls_back_to_prose_then_gives_up():
    prose = {"description": {"plain": "A supersoft merino wool base layer."}}
    assert "merino" in extract_fabric(prose).lower()
    assert extract_fabric({"title": "Mystery item"}) == ""


def test_serialization_round_trips_for_the_ui():
    pick = pick_best([product("a", "Alpha", rating=4.5, count=30, amount=4000)], need="soft shirt")
    payload = pick.as_dict()

    assert payload["winner"]["product_id"] == "a"
    assert payload["winner"]["criteria"]
    assert isinstance(payload["ranked"], list)
