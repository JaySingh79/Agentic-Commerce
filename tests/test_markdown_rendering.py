"""Tests for the frontend's Markdown renderer.

The model answers in Markdown. Rendering it as plain text left `**bold**` and
`* ` bullets on screen; rendering it with innerHTML would hand web-search text —
which is attacker-influenced — a route onto the page as markup. These run the
real `renderMarkdown` from ``web/app.js`` in Node against a DOM shim, so both
properties are checked against the shipped code rather than a copy of it.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parent / "js" / "markdown_harness.mjs"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def render(markdown: str) -> dict[str, str]:
    """Renders one Markdown string through the shipped frontend code."""
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["node", str(HARNESS)],
        input=json.dumps({"markdown": markdown}) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip())


def test_bold_becomes_an_element_not_asterisks():
    out = render("I see the **4192 - Washed Tailored Tees** in your cart.")
    assert "<strong>4192 - Washed Tailored Tees</strong>" in out["html"]
    assert "**" not in out["text"]


def test_a_bullet_list_renders_as_a_list():
    out = render("Cart:\n\n* **Price:** $40.00\n* **Merchant:** youngla.myshopify.com")
    assert out["html"].count("<li>") == 2
    assert "<ul>" in out["html"]
    assert not out["text"].lstrip().startswith("*")


def test_a_numbered_list_keeps_its_ordering_semantics():
    out = render("1. Brooks Hyperion - $149.95\n2. ON Cloudsurfer - $119.95")
    assert "<ol>" in out["html"]
    assert out["html"].count("<li>") == 2


def test_headings_and_code_and_quotes_render():
    out = render("## Cart\n\n> held on the merchant\n\nCall `process_test_payment`.")
    assert "<h4" in out["html"]
    assert "<blockquote>" in out["html"]
    assert "<code>process_test_payment</code>" in out["html"]


def test_a_javascript_link_never_becomes_an_anchor():
    """Answer text quotes web results; a scheme allowlist is the whole defence."""
    out = render("[click me](javascript:alert(1))")
    assert "<a" not in out["html"]
    assert "javascript:" not in out["html"].replace("javascript:alert(1)", "")
    # The text is shown rather than silently dropped.
    assert "click me" in out["text"]


def test_an_http_link_is_an_anchor_that_cannot_reach_back():
    out = render("[shop](https://shop.example/p)")
    assert 'href="https://shop.example/p"' in out["html"]
    assert 'rel="noopener noreferrer nofollow"' in out["html"]


def test_markup_in_the_answer_is_text_not_html():
    """A product title carrying a tag must not become a tag."""
    out = render("Found **<img src=x onerror=alert(1)>** in the catalog.")
    assert "<img" not in out["html"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in out["html"]


def test_a_half_streamed_code_fence_still_renders():
    """Blocks arrive a token at a time; an unterminated fence must not blank the answer."""
    out = render("Here:\n\n```\nuv run pytest")
    assert "uv run pytest" in out["text"]


def test_plain_prose_is_left_alone():
    out = render("Would you like to proceed to checkout with this item?")
    assert out["html"] == "<p>Would you like to proceed to checkout with this item?</p>"
