"""Tests for the product/web result card grid renderer.

Card content originates from web search, i.e. attacker-influenced text, so the
escaping tests here are security tests, not cosmetics.
"""

import re
from pathlib import Path

from agentic_commerce.ui.cards import (
    render_product_grid,
    render_results_panel,
    render_web_grid,
)
from agentic_commerce.ui.theme import CUSTOM_CSS

PRODUCT = {
    "id": "gid://shopify/p/1",
    "title": "Trail Runner",
    "price_cents": 12999,
    "media": [{"type": "image", "url": "https://cdn.example/shoe.jpg"}],
    "variants": [{"id": "v1", "url": "https://shop.example/p/1"}],
}

WEB = {
    "title": "Nike Pegasus 41",
    "url": "https://nike.com/pegasus",
    "snippet": "fast shoe",
    "source": "nike.com",
    "image_url": "https://cdn.nike.com/peg.jpg",
    "favicon_url": "https://www.google.com/s2/favicons?domain=nike.com&sz=128",
}


def test_product_grid_uses_grid_container_not_stacked_images():
    out = render_product_grid([PRODUCT])
    assert 'class="ac-grid"' in out
    assert out.count('class="ac-card"') == 1
    assert "https://cdn.example/shoe.jpg" in out
    assert "$129.99" in out


def test_web_cards_link_to_the_listing_and_open_safely():
    out = render_web_grid([WEB])
    assert 'href="https://nike.com/pegasus"' in out
    assert 'target="_blank"' in out
    # Prevents tab-nabbing / referrer leak to third-party retailers.
    assert 'rel="noopener noreferrer nofollow"' in out
    assert "https://cdn.nike.com/peg.jpg" in out


def test_web_card_falls_back_to_favicon_when_no_preview_image():
    out = render_web_grid([{**WEB, "image_url": None}])
    assert "s2/favicons?domain=nike.com" in out


def test_missing_image_entirely_renders_placeholder_not_broken_img():
    out = render_web_grid([{"title": "X", "url": "https://x.com", "source": "x.com"}])
    assert "data:image/svg+xml" in out


def test_html_in_titles_is_escaped():
    """A malicious result title must not become live markup."""
    out = render_web_grid([{**WEB, "title": "<script>alert(1)</script>"}])
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_javascript_urls_are_rejected():
    """javascript: hrefs would be an XSS sink once interpolated."""
    out = render_web_grid([{**WEB, "url": "javascript:alert(1)"}])
    assert "javascript:" not in out
    assert "<a " not in out, "unsafe link must not become an anchor at all"


def test_javascript_image_src_is_rejected():
    out = render_web_grid([{**WEB, "image_url": "javascript:alert(1)", "favicon_url": None}])
    assert "javascript:" not in out


def test_quotes_in_title_cannot_break_out_of_the_attribute():
    out = render_web_grid([{**WEB, "title": '" onmouseover="alert(1)'}])
    assert 'onmouseover="alert(1)"' not in out


def test_results_panel_shows_both_sections():
    out = render_results_panel([PRODUCT], [WEB])
    assert "Catalog matches" in out
    assert "Web results" in out


def test_results_panel_is_empty_state_when_nothing_found():
    out = render_results_panel([], [])
    assert "ac-empty" in out
    assert "ac-grid" not in out


def test_grid_css_lays_cards_out_in_columns_not_a_single_stack():
    """The "block, not vertical" requirement, asserted on the stylesheet itself.

    ``render_*_grid`` only emits ``class="ac-grid"``; whether that reads as a row
    of blocks or a vertical stack is decided entirely by this rule, so the rule
    is what the test has to pin down.
    """
    rule = CUSTOM_CSS.split(".ac-grid {", 1)[1].split("}", 1)[0]
    assert "display: grid" in rule
    columns = re.search(r"grid-template-columns:\s*([^;]+);", rule)
    assert columns, "no grid-template-columns: cards would stack one per row"
    # auto-fill + minmax is what makes cards flow into as many columns as fit.
    assert "auto-fill" in columns.group(1)
    assert "minmax(" in columns.group(1)


def test_many_results_share_one_grid_container():
    """Each card must be a sibling in a single grid, not its own block."""
    out = render_product_grid([{**PRODUCT, "id": f"gid://{i}"} for i in range(6)])
    assert out.count('class="ac-grid"') == 1
    assert out.count('class="ac-card"') == 6


def test_css_is_wired_into_the_launched_app():
    """A grid rule that never reaches the page is not a grid."""
    app_py = Path(__file__).resolve().parents[1] / "src/agentic_commerce/ui/app.py"
    source = app_py.read_text(encoding="utf-8")
    assert "css=CUSTOM_CSS" in source


RICH_PRODUCT = {
    "id": "gid://shopify/p/9",
    "title": "Merino Base Layer",
    "price_range": {"min": {"amount": 6500, "currency": "USD"}},
    "rating": {"value": 4.8, "scale_max": 5, "count": 42},
    "metadata": {"tech_specs": "Fabric: Merino Wool\nSleeve Length: Long"},
    "options": [{"name": "Size", "values": ["S", "M", "L"]}],
    "media": [{"type": "image", "url": "https://cdn.example/base.jpg"}],
    "variants": [
        {
            "id": "v1",
            "url": "https://shop.example/p/9",
            "availability": {"available": True},
            "seller": "Trailworks",
        }
    ],
}


def test_card_has_a_click_to_expand_details_layer():
    out = render_product_grid([RICH_PRODUCT])
    assert "<details class=\"ac-card-more\">" in out
    assert "<summary>Details</summary>" in out
    # The facts a shopper actually needs before clicking through.
    assert "Merino Wool" in out
    assert "4.8/5 (42 reviews)" in out
    assert "Trailworks" in out
    assert "In stock" in out
    assert "Size (3)" in out


def test_details_disclosure_is_a_sibling_of_the_link_not_a_child():
    """An interactive <details> inside an <a> is invalid and would navigate away."""
    out = render_product_grid([RICH_PRODUCT])
    anchor_start = out.index("<a class=\"ac-card-link\"")
    anchor_end = out.index("</a>", anchor_start)
    details_start = out.index("<details")
    assert details_start > anchor_end


def test_card_with_nothing_to_show_omits_the_disclosure_entirely():
    """An expander that opens onto blank space is worse than no expander."""
    bare = {"id": "x", "title": "Bare", "variants": []}
    out = render_product_grid([bare])
    assert "ac-card" in out
    assert "<details" not in out


def test_detail_values_are_escaped():
    """Detail rows carry catalog copy, so they are an injection sink too."""
    hostile = {
        "id": "x",
        "title": "Hostile",
        "metadata": {"tech_specs": "Fabric: <script>alert(1)</script>"},
        "variants": [{"id": "v", "seller": '"><img src=x onerror=alert(1)>'}],
    }
    out = render_product_grid([hostile])
    # Scoped to the disclosure: the card legitimately contains its own <img>.
    # The property that matters is that the hostile copy forms no live tag and
    # survives only as inert escaped text.
    disclosure = out[out.index("<details") :]
    assert "<script" not in disclosure
    assert "<img" not in disclosure
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in disclosure
    assert "&lt;img src=x onerror=alert(1)&gt;" in disclosure


def test_best_pick_ribbons_only_the_winning_card():
    other = {**RICH_PRODUCT, "id": "gid://shopify/p/10", "title": "Runner Up"}
    verdict = {
        "winner": {
            "product_id": "gid://shopify/p/9",
            "criteria": [
                {"name": "fabric_fit", "score": 1.0, "weight": 0.4, "evidence": "Merino — warmth"},
                {"name": "rating", "score": 0.95, "weight": 0.3, "evidence": "4.8/5 rated"},
            ],
            "missing": ["price_value"],
        }
    }

    out = render_product_grid([RICH_PRODUCT, other], verdict)

    assert out.count("ac-card-ribbon") == 1
    assert out.count("ac-card-best") == 1
    # The reasoning rides on the product, not just in the transcript.
    assert "Merino — warmth" in out
    assert "Not scored" in out and "price_value" in out
    # The ribbon must sit on the winner's card, not the runner-up's.
    assert out.index("Best pick") < out.index("Runner Up")


def test_results_panel_passes_the_verdict_through():
    verdict = {"winner": {"product_id": PRODUCT["id"], "criteria": [], "missing": []}}
    out = render_results_panel([PRODUCT], [], verdict)
    assert "ac-card-best" in out


def test_web_cards_expand_to_show_the_snippet():
    out = render_web_grid([{**WEB, "snippet": "Breathable knit upper, 4.5 stars"}])
    assert "<details" in out
    assert "Breathable knit upper" in out


def test_a_favicon_only_web_card_gets_a_different_silhouette():
    """A 128px favicon stretched into a square photo slot made the grid ragged."""
    html_out = render_web_grid([
        {"title": "Category", "url": "https://a.example/c", "source": "a.example",
         "image_url": None, "favicon_url": "https://icons.example/a.png"},
    ])
    assert "ac-card-compact" in html_out


def test_a_web_card_with_a_real_photo_keeps_the_full_slot():
    html_out = render_web_grid([
        {"title": "Shoe", "url": "https://a.example/p", "source": "a.example",
         "image_url": "https://a.example/p.jpg"},
    ])
    assert "ac-card-compact" not in html_out


def test_web_cards_carry_no_cart_control():
    """Non-purchasable is structural: the affordance is absent, not disabled."""
    html_out = render_web_grid([
        {"title": "Shoe", "url": "https://a.example/p", "source": "a.example",
         "image_url": "https://a.example/p.jpg"},
    ])
    assert "add-to-cart" not in html_out and "<button" not in html_out
