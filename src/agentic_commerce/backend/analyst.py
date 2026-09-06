"""The Analyst specialist: picks the best product out of a search result set.

Scoring is **deterministic and evidence-bound**. Every criterion is derived from a
field the UCP catalog actually returns:

===================  ====================================================
Criterion            Source
===================  ====================================================
``rating``           ``product.rating.value`` (or a variant's), normalized
``popularity``       ``product.rating.count`` — see the caveat below
``fabric_fit``       ``metadata.tech_specs`` "Fabric: ..." vs the shopper's need
``price_value``      ``price_range.min.amount`` against the cohort
``availability``     ``variants[].availability.available`` / ``eligible``
===================  ====================================================

Two rules keep the verdict honest:

1. **Unknown is not zero.** A product with no rating is not "rated badly". A
   missing signal is dropped and the remaining weights are renormalized, so a
   sparsely-described product is neither punished nor flattered. Every dropped
   signal is reported in :attr:`ProductScore.missing`.
2. **No invented sales data.** The shopper asked for "past bought numbers"; the
   UCP catalog exposes no purchase-volume field. Review *count* is used as an
   openly-labelled popularity proxy (see :data:`POPULARITY_CAVEAT`) rather than
   fabricating a figure that would read as fact.

Prices are only compared within a single currency — ranking a £18 shirt against a
$65 one by raw amount would be a scoring bug, not a bargain.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

#: Surfaced next to the popularity score so the proxy is never mistaken for sales.
POPULARITY_CAVEAT = (
    "popularity is inferred from review count; the UCP catalog exposes no "
    "purchase-volume field"
)

#: Base weights. Missing criteria are dropped and the rest renormalized.
WEIGHTS: dict[str, float] = {
    "rating": 0.28,
    "popularity": 0.17,
    "fabric_fit": 0.30,
    "price_value": 0.15,
    "availability": 0.10,
}

#: Review count at which popularity confidence saturates.
_CONFIDENCE_SATURATION = 50

_FABRIC_LINE_RE = re.compile(r"fabric\s*:\s*([^\n\r]+)", re.IGNORECASE)
_MATERIAL_LINE_RE = re.compile(r"material\s*:\s*([^\n\r]+)", re.IGNORECASE)

#: Fibres recognised in a spec string, longest first so "organic cotton" wins.
_FIBRES = (
    "organic cotton",
    "recycled polyester",
    "merino wool",
    "merino",
    "cotton",
    "polyester",
    "nylon",
    "elastane",
    "spandex",
    "lycra",
    "wool",
    "cashmere",
    "linen",
    "bamboo",
    "modal",
    "silk",
    "hemp",
    "fleece",
    "down",
    "leather",
    "rayon",
    "viscose",
    "acrylic",
    "gore-tex",
    "ripstop",
)

#: What a stated need implies about fibre choice. Order is irrelevant; a need may
#: match several aspects and each is scored independently.
_NEED_ASPECTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # aspect: (need keywords, fibres that satisfy it)
    "breathability": (
        ("breathable", "summer", "hot weather", "hot", "airy", "lightweight"),
        ("cotton", "organic cotton", "linen", "bamboo", "merino", "merino wool", "modal"),
    ),
    "moisture_wicking": (
        (
            "sweat", "moisture", "wicking", "running", "run",
            "gym", "workout", "athletic", "training",
        ),
        ("polyester", "recycled polyester", "nylon", "elastane", "spandex", "lycra", "merino"),
    ),
    "warmth": (
        ("warm", "winter", "cold", "thermal", "insulated"),
        ("wool", "merino", "merino wool", "fleece", "down", "cashmere"),
    ),
    "softness": (
        ("soft", "sensitive skin", "comfortable", "cosy", "cozy"),
        ("cotton", "organic cotton", "bamboo", "modal", "silk", "cashmere", "fleece"),
    ),
    "durability": (
        ("durable", "rugged", "trail", "hiking", "outdoor", "hard-wearing"),
        ("nylon", "polyester", "ripstop", "leather", "gore-tex"),
    ),
    "weather_resistance": (
        ("waterproof", "rain", "wet", "windproof", "weatherproof"),
        ("nylon", "polyester", "gore-tex", "ripstop"),
    ),
    "sustainability": (
        ("sustainable", "eco", "organic", "natural fibre", "natural fiber", "recycled"),
        ("organic cotton", "recycled polyester", "linen", "hemp", "bamboo", "wool"),
    ),
    "stretch": (
        ("stretch", "stretchy", "flexible", "yoga"),
        ("elastane", "spandex", "lycra"),
    ),
}


@dataclass(frozen=True)
class Criterion:
    """One scored dimension, kept with the evidence that produced it."""

    name: str
    score: float
    weight: float
    evidence: str


@dataclass
class ProductScore:
    """A single product's evaluation."""

    product_id: str
    title: str
    total: float = 0.0
    criteria: list[Criterion] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    fabric: str = ""
    price_display: str = ""

    def reason_lines(self) -> list[str]:
        """Human-readable evidence, strongest contribution first."""
        ranked = sorted(self.criteria, key=lambda c: c.score * c.weight, reverse=True)
        return [f"{c.name.replace('_', ' ')}: {c.evidence}" for c in ranked]

    def as_dict(self) -> dict[str, Any]:
        """Serializable form for session state and the UI."""
        return {
            "product_id": self.product_id,
            "title": self.title,
            "total": round(self.total, 4),
            "fabric": self.fabric,
            "price_display": self.price_display,
            "criteria": [
                {
                    "name": c.name,
                    "score": round(c.score, 4),
                    "weight": round(c.weight, 4),
                    "evidence": c.evidence,
                }
                for c in self.criteria
            ],
            "missing": list(self.missing),
        }


@dataclass
class BestPick:
    """The Analyst's verdict for one search result set."""

    winner: ProductScore | None
    ranked: list[ProductScore] = field(default_factory=list)
    comparison: list[str] = field(default_factory=list)
    need: str = ""

    @property
    def product_id(self) -> str:
        """Convenience accessor used by the UI to badge the winning card."""
        return self.winner.product_id if self.winner else ""

    def as_dict(self) -> dict[str, Any]:
        """Serializable form for session state and the UI."""
        return {
            "need": self.need,
            "winner": self.winner.as_dict() if self.winner else None,
            "ranked": [s.as_dict() for s in self.ranked],
            "comparison": list(self.comparison),
            "caveat": POPULARITY_CAVEAT,
        }

    def format_display(self) -> str:
        """Markdown verdict for the chat transcript."""
        if not self.winner:
            return "No products to compare — run a catalog search first."

        lines = [
            f"**Best pick: {self.winner.title}** "
            f"(score {self.winner.total:.2f}/1.00"
            + (f", {self.winner.price_display}" if self.winner.price_display else "")
            + ")",
        ]
        if self.need:
            lines.append(f"*Judged against: {self.need}*")
        lines.append("")
        lines.extend(f"- {line}" for line in self.winner.reason_lines())
        if self.winner.missing:
            lines.append(
                f"- not scored (no data): {', '.join(self.winner.missing)}"
            )
        if self.comparison:
            lines.append("")
            lines.append("**Versus the runners-up**")
            lines.extend(f"- {line}" for line in self.comparison)
        lines.append("")
        lines.append(f"*Note: {POPULARITY_CAVEAT}.*")
        return "\n".join(lines)


# --------------------------------------------------------------- extraction


def _rating_of(product: dict[str, Any]) -> dict[str, Any] | None:
    """Reads a rating from the product, falling back to its variants.

    The catalog places ``rating`` on the product for some merchants and only on
    the variant for others, so both are checked before giving up.
    """
    rating = product.get("rating")
    if isinstance(rating, dict) and rating.get("value") is not None:
        return rating
    for variant in product.get("variants") or []:
        if isinstance(variant, dict):
            candidate = variant.get("rating")
            if isinstance(candidate, dict) and candidate.get("value") is not None:
                return candidate
    return None


def _spec_text(product: dict[str, Any]) -> str:
    """Concatenates the fields that can carry a fabric statement."""
    metadata = product.get("metadata") or {}
    description = product.get("description")
    if isinstance(description, dict):
        description = description.get("plain") or description.get("html") or ""
    parts = [
        str(metadata.get("tech_specs") or ""),
        str(metadata.get("top_features") or ""),
        str(description or ""),
    ]
    return "\n".join(p for p in parts if p)


def extract_fabric(product: dict[str, Any]) -> str:
    """Returns the product's stated fabric, or '' when it declares none.

    Prefers an explicit ``Fabric:``/``Material:`` spec line; otherwise falls back
    to naming any recognised fibre mentioned in the copy.
    """
    text = _spec_text(product)
    if not text:
        return ""
    for pattern in (_FABRIC_LINE_RE, _MATERIAL_LINE_RE):
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    lowered = text.lower()
    found = [fibre for fibre in _FIBRES if fibre in lowered]
    return found[0] if found else ""


def _fibres_in(fabric: str) -> set[str]:
    """Recognised fibres named in a fabric string."""
    lowered = fabric.lower()
    return {fibre for fibre in _FIBRES if fibre in lowered}


def _needed_aspects(need: str) -> list[str]:
    """Aspects implied by the shopper's stated need."""
    lowered = (need or "").lower()
    return [
        aspect
        for aspect, (keywords, _) in _NEED_ASPECTS.items()
        if any(keyword in lowered for keyword in keywords)
    ]


def _price_of(product: dict[str, Any]) -> tuple[int | None, str]:
    """Minor-unit price and currency from ``price_range.min``."""
    price_range = product.get("price_range") or {}
    minimum = price_range.get("min") if isinstance(price_range, dict) else None
    if isinstance(minimum, dict) and minimum.get("amount") is not None:
        try:
            return int(minimum["amount"]), str(minimum.get("currency") or "")
        except (TypeError, ValueError):
            return None, ""
    return None, ""


def _is_available(product: dict[str, Any]) -> bool | None:
    """Whether any variant is both available and eligible; None when unstated."""
    stated = False
    for variant in product.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        availability = variant.get("availability")
        if not isinstance(availability, dict) or "available" in availability:
            stated = True
        if isinstance(availability, dict) and availability.get("available"):
            if variant.get("eligible") is False:
                continue
            return True
    return False if stated else None


# ----------------------------------------------------------------- scoring


def _score_one(
    product: dict[str, Any],
    need: str,
    aspects: list[str],
    price_band: tuple[int, int] | None,
    band_currency: str,
) -> ProductScore:
    """Scores one product, dropping criteria it has no data for."""
    result = ProductScore(
        product_id=str(product.get("id", "")),
        title=str(product.get("title") or "Untitled product"),
    )
    scored: list[Criterion] = []

    rating = _rating_of(product)
    if rating:
        scale_max = float(rating.get("scale_max") or 5) or 5.0
        scale_min = float(rating.get("scale_min") or 0)
        span = scale_max - scale_min or 1.0
        value = float(rating["value"])
        normalized = max(0.0, min(1.0, (value - scale_min) / span))
        scored.append(
            Criterion(
                "rating",
                normalized,
                WEIGHTS["rating"],
                f"{value:g}/{scale_max:g} rated",
            )
        )

        count = int(rating.get("count") or 0)
        if count > 0:
            confidence = min(
                1.0, math.log10(count + 1) / math.log10(_CONFIDENCE_SATURATION + 1)
            )
            scored.append(
                Criterion(
                    "popularity",
                    confidence,
                    WEIGHTS["popularity"],
                    f"{count} review(s) — {POPULARITY_CAVEAT}",
                )
            )
        else:
            result.missing.append("popularity")
    else:
        result.missing.extend(["rating", "popularity"])

    fabric = extract_fabric(product)
    result.fabric = fabric
    if aspects and fabric:
        fibres = _fibres_in(fabric)
        met = [
            aspect
            for aspect in aspects
            if fibres & set(_NEED_ASPECTS[aspect][1])
        ]
        fit = len(met) / len(aspects)
        detail = (
            ", ".join(a.replace("_", " ") for a in met)
            if met
            else "none of the asked-for traits"
        )
        scored.append(
            Criterion("fabric_fit", fit, WEIGHTS["fabric_fit"], f"{fabric} — matches {detail}")
        )
    elif not aspects:
        result.missing.append("fabric_fit (no material preference stated)")
    else:
        result.missing.append("fabric_fit (product states no fabric)")

    amount, currency = _price_of(product)
    if amount is not None:
        result.price_display = f"{amount / 100:.2f} {currency}".strip()
    if amount is not None and price_band and currency == band_currency:
        low, high = price_band
        spread = high - low
        value_score = 1.0 if spread <= 0 else 1.0 - (amount - low) / spread
        scored.append(
            Criterion(
                "price_value",
                max(0.0, min(1.0, value_score)),
                WEIGHTS["price_value"],
                f"{result.price_display} against {low / 100:.2f}–{high / 100:.2f} {band_currency}",
            )
        )
    elif amount is not None and price_band:
        # Ranking across currencies by raw amount would be meaningless.
        result.missing.append(f"price_value (priced in {currency}, cohort in {band_currency})")
    else:
        result.missing.append("price_value")

    available = _is_available(product)
    if available is None:
        result.missing.append("availability")
    else:
        scored.append(
            Criterion(
                "availability",
                1.0 if available else 0.0,
                WEIGHTS["availability"],
                "in stock" if available else "no purchasable variant",
            )
        )

    total_weight = sum(c.weight for c in scored)
    if total_weight > 0:
        # Renormalize so a product missing a signal is neither punished nor
        # flattered relative to one that reports everything.
        result.criteria = [
            Criterion(c.name, c.score, c.weight / total_weight, c.evidence) for c in scored
        ]
        result.total = sum(c.score * c.weight for c in result.criteria)
    return result


def _price_band(products: list[dict[str, Any]]) -> tuple[tuple[int, int] | None, str]:
    """Price range of the majority currency in the cohort."""
    by_currency: dict[str, list[int]] = {}
    for product in products:
        amount, currency = _price_of(product)
        if amount is not None:
            by_currency.setdefault(currency, []).append(amount)
    if not by_currency:
        return None, ""
    currency = max(by_currency, key=lambda c: len(by_currency[c]))
    amounts = by_currency[currency]
    return (min(amounts), max(amounts)), currency


def _compare(winner: ProductScore, runner: ProductScore) -> str:
    """One line explaining why *winner* beat *runner*."""
    runner_by_name = {c.name: c for c in runner.criteria}
    gaps = [
        (c.score - runner_by_name[c.name].score, c.name)
        for c in winner.criteria
        if c.name in runner_by_name
    ]
    positive = [g for g in gaps if g[0] > 0.001]
    if positive:
        _, name = max(positive)
        edge = name.replace("_", " ")
        return (
            f"beats **{runner.title}** ({runner.total:.2f}) on {edge} — "
            f"{next(c.evidence for c in winner.criteria if c.name == name)}"
        )
    if not runner_by_name:
        return f"**{runner.title}** ({runner.total:.2f}) reports no comparable data"
    return (
        f"edges out **{runner.title}** ({runner.total:.2f}) on the weighted total; "
        f"no single criterion separates them"
    )


def pick_best(products: list[dict[str, Any]], need: str = "") -> BestPick:
    """Ranks *products* against the shopper's stated *need* and names a winner.

    *need* is free text ("something breathable for hot-weather running"). When it
    names no material-relevant trait, fabric fit is dropped rather than guessed —
    see the module docstring.
    """
    if not products:
        return BestPick(winner=None, need=need)

    aspects = _needed_aspects(need)
    band, band_currency = _price_band(products)
    scores = [_score_one(p, need, aspects, band, band_currency) for p in products]
    ranked = sorted(scores, key=lambda s: s.total, reverse=True)
    winner = ranked[0]
    comparison = [_compare(winner, runner) for runner in ranked[1:4]]
    return BestPick(winner=winner, ranked=ranked, comparison=comparison, need=need)
