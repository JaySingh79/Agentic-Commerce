"""Static checks on the hand-written frontend in ``web/``.

These exist because of a live defect: ``.lightbox { display: flex }`` beat the
user-agent's ``[hidden] { display: none }`` on specificity, so the page loaded
with its own modal scrim over the whole UI, swallowing every click. Nothing in
the Python suite could have caught it, but the shape of the bug is mechanical
and cheap to assert against.
"""

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"


@pytest.fixture(scope="module")
def html() -> str:
    return (WEB / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css() -> str:
    return (WEB / "styles.css").read_text(encoding="utf-8")


def selectors_setting_display(css: str) -> set[str]:
    """Every simple selector whose rule block sets ``display``."""
    found: set[str] = set()
    for block in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        if not re.search(r"display\s*:", block.group(2)):
            continue
        for selector in block.group(1).split(","):
            found.add(selector.strip())
    return found


def elements_starting_hidden(html: str) -> list[tuple[str | None, str | None]]:
    """(id, class) for every element rendered with the ``hidden`` attribute."""
    out = []
    for tag in re.finditer(r"<\w+([^>]*\bhidden\b[^>]*)>", html):
        attrs = tag.group(1)
        ident = re.search(r'id="([^"]+)"', attrs)
        classes = re.search(r'class="([^"]+)"', attrs)
        out.append((ident.group(1) if ident else None, classes.group(1) if classes else None))
    return out


def test_the_hidden_attribute_beats_every_class_that_sets_display(css: str):
    """The one-line guard that keeps a hidden modal actually hidden."""
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", css)


def test_the_page_does_not_open_with_a_modal_over_it(html: str, css: str):
    """Any element that starts hidden but is styled ``display: …`` needs the guard."""
    styled = selectors_setting_display(css)
    conflicts = []
    for ident, classes in elements_starting_hidden(html):
        names = [f".{c}" for c in (classes or "").split() if c]
        if ident:
            names.append(f"#{ident}")
        conflicts.extend(name for name in names if name in styled)

    # Conflicts are allowed to exist — the global [hidden] rule neutralises them —
    # but only while that rule is present. Assert both facts together.
    assert re.search(r"\[hidden\]\s*\{\s*display:\s*none\s*!important", css), (
        f"selectors force display on hidden elements: {sorted(set(conflicts))}"
    )


def test_the_lightbox_and_drawer_start_closed(html: str):
    for element_id in ("lightbox", "drawer", "drawer-scrim"):
        pattern = rf'id="{element_id}"[^>]*>'
        match = re.search(pattern, html)
        assert match, f"#{element_id} is missing"
        assert "hidden" in match.group(0), f"#{element_id} does not start hidden"


def test_every_id_the_script_reaches_for_exists(html: str):
    """A typo'd getElementById fails silently at runtime; it fails loudly here."""
    script = (WEB / "app.js").read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([^"]+)"', html))
    wanted = set(re.findall(r'\bel\("([^"]+)"\)', script))
    assert not (wanted - ids)
