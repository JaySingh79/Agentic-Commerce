"""Internet product discovery used to supplement the Shopify UCP catalog.

The UCP Global Catalog only covers participating Shopify merchants, so a shopper
asking for something outside it gets "no products found". This module adds a
general web lookup for those cases.

Providers are tried in order of result quality:

1. **Tavily** — used when ``TAVILY_API_KEY`` is set. Purpose-built for agents and
   returns clean JSON with snippets.
2. **DuckDuckGo Lite** — keyless default, so the feature works with no extra
   configuration.

Both are spoken to over ``httpx``, which is already a project dependency; no new
package is introduced.
"""

from __future__ import annotations

import asyncio
import html
import os
import re
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import urljoin

import httpx

_DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"
_TAVILY_URL = "https://api.tavily.com/search"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AgenticCommerce/0.1"

# Preview-image discovery. Kept deliberately tight: a listing page is only read
# far enough to reach <head>, and slow hosts are abandoned quickly.
_IMAGE_TIMEOUT = 6.0
_MAX_HTML_BYTES = 300_000

_META_IMAGE_RE = re.compile(
    r"""<meta[^>]+(?:property|name)=["']"""
    r"""(?:og:image(?::secure_url)?|twitter:image(?::src)?)["'][^>]*>""",
    re.IGNORECASE,
)
_CONTENT_RE = re.compile(r"""content=["']([^"']+)["']""", re.IGNORECASE)
_LINK_IMAGE_RE = re.compile(r"""<link[^>]+rel=["']image_src["'][^>]*>""", re.IGNORECASE)
# Preview links may be relative, so this one is deliberately scheme-agnostic; the
# result-anchor regex below is not (see _RESULT_HREF_RE).
_LINK_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)
# Logos and icons render as obviously-broken product cards.
_ICON_RE = re.compile(
    r"(favicon|android-icon|apple-touch|-logo|/logo|sprite|placeholder|\.svg($|\?))",
    re.IGNORECASE,
)

# lite.duckduckgo.com renders each hit as
#   <a rel="nofollow" href='URL' class='result-link'>TITLE</a>
# followed by <td class='result-snippet'>SNIPPET</td>.
# Attribute order and quote style both vary, so anchors are matched loosely and
# filtered on the class afterwards rather than with one rigid pattern.
_ANCHOR_RE = re.compile(r"<a\s+(?P<attrs>[^>]*)>(?P<title>.*?)</a>", re.DOTALL | re.IGNORECASE)
_RESULT_HREF_RE = re.compile(r"""href=["'](?P<url>https?://[^"']+)["']""", re.IGNORECASE)
_SNIPPET_RE = re.compile(
    r"""<td[^>]*class=["']result-snippet["'][^>]*>(?P<snippet>.*?)</td>""",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class WebResult:
    """A single web search hit for a product query."""

    title: str
    url: str
    snippet: str
    source: str
    image_url: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        """Serializable form for session state and UI payloads."""
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "image_url": self.image_url,
            "favicon_url": favicon_for(self.url),
        }


def favicon_for(url: str) -> str:
    """Always-available visual fallback for listings with no discoverable image."""
    return f"https://www.google.com/s2/favicons?domain={_domain(url)}&sz=128"


def _clean(raw: str) -> str:
    """Strips tags and unescapes entities from a fragment of search-result HTML."""
    return html.unescape(re.sub(r"<[^>]+>", "", raw)).strip()


def _domain(url: str) -> str:
    """Best-effort registrable host for display."""
    match = re.match(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1) if match else url


def _parse_ddg_lite(body: str, limit: int) -> list[WebResult]:
    """Extracts result rows from a DuckDuckGo Lite HTML response."""
    snippets = [_clean(m.group("snippet")) for m in _SNIPPET_RE.finditer(body)]

    results: list[WebResult] = []
    idx = -1
    for anchor in _ANCHOR_RE.finditer(body):
        attrs = anchor.group("attrs")
        if "result-link" not in attrs:
            continue
        href = _RESULT_HREF_RE.search(attrs)
        title = _clean(anchor.group("title"))
        if not href or not title:
            continue
        idx += 1
        url = html.unescape(href.group("url"))
        results.append(
            WebResult(
                title=title,
                url=url,
                snippet=snippets[idx] if idx < len(snippets) else "",
                source=_domain(url),
            )
        )
        if len(results) >= limit:
            break
    return results


async def _search_tavily(query: str, limit: int, api_key: str) -> list[WebResult]:
    """Queries the Tavily search API."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        res = await client.post(
            _TAVILY_URL,
            json={
                "api_key": api_key,
                "query": query,
                "max_results": limit,
                "search_depth": "basic",
                "include_answer": False,
            },
        )
        res.raise_for_status()
        payload: dict[str, Any] = res.json()

    return [
        WebResult(
            title=str(item.get("title", "")).strip(),
            url=str(item.get("url", "")),
            snippet=str(item.get("content", "")).strip()[:300],
            source=_domain(str(item.get("url", ""))),
        )
        for item in payload.get("results", [])[:limit]
        if item.get("url")
    ]


async def _search_duckduckgo(query: str, limit: int) -> list[WebResult]:
    """Queries the keyless DuckDuckGo Lite endpoint."""
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        res = await client.post(
            _DDG_LITE_URL,
            data={"q": query},
            headers={"User-Agent": _UA},
        )
        res.raise_for_status()
    return _parse_ddg_lite(res.text, limit)


async def _fetch_preview_image(client: httpx.AsyncClient, url: str) -> str | None:
    """Reads a listing's social preview image (og:image / twitter:image).

    Retailer *category* pages frequently omit these tags, so a miss is normal and
    the caller falls back to the site favicon rather than showing a broken image.
    """
    try:
        res = await client.get(
            url,
            timeout=_IMAGE_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": _UA},
        )
        body = res.text[:_MAX_HTML_BYTES]
    except Exception:  # noqa: BLE001 - previews are best-effort decoration
        return None

    candidates: list[str] = []
    for match in _META_IMAGE_RE.finditer(body):
        content = _CONTENT_RE.search(match.group(0))
        if content:
            candidates.append(content.group(1))
    for match in _LINK_IMAGE_RE.finditer(body):
        href = _LINK_HREF_RE.search(match.group(0))
        if href:
            candidates.append(href.group(1))

    for candidate in candidates:
        resolved = urljoin(str(res.url), html.unescape(candidate.strip()))
        # Skip logos/icons/sprites: they look like broken cards in a product grid.
        if resolved.startswith("http") and not _ICON_RE.search(resolved):
            return resolved
    return None


async def enrich_with_images(results: list[WebResult]) -> list[WebResult]:
    """Attaches preview images to web results, fetched concurrently.

    Bounded by a short per-request timeout so a slow retailer cannot stall the
    chat response; results keep their original order.
    """
    if not results:
        return results

    async with httpx.AsyncClient() as client:
        images = await asyncio.gather(
            *(_fetch_preview_image(client, r.url) for r in results),
            return_exceptions=True,
        )

    return [
        replace(result, image_url=image if isinstance(image, str) else None)
        for result, image in zip(results, images, strict=True)
    ]


async def search_web(query: str, limit: int = 5, with_images: bool = True) -> list[WebResult]:
    """Runs a product-oriented web search using the best configured provider."""
    tavily_key = os.getenv("TAVILY_API_KEY")
    results: list[WebResult] = []
    if tavily_key:
        try:
            results = await _search_tavily(query, limit, tavily_key)
        except Exception:  # noqa: BLE001 - fall back to the keyless provider
            results = []
    if not results:
        results = await _search_duckduckgo(query, limit)

    return await enrich_with_images(results) if with_images else results


def format_results(query: str, results: list[WebResult]) -> str:
    """Renders web results as Markdown for the agent to cite."""
    if not results:
        return (
            f"No web results found for '{query}'. Try a broader description "
            f"(e.g. drop the price limit or brand)."
        )
    lines = [f"Found {len(results)} web results for '{query}':\n"]
    for idx, r in enumerate(results, 1):
        lines.append(f"{idx}. **[{r.title}]({r.url})** — `{r.source}`")
        if r.snippet:
            lines.append(f"   {r.snippet[:220]}")
    lines.append(
        "\n*These are external web listings, not Shopify UCP catalog items, so they "
        "cannot be added to a Universal Cart directly.*"
    )
    return "\n".join(lines)
