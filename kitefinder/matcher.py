"""Score listings against what the rider needs (the active set, or a typed query)."""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from . import pricing
from .assemble import match as fits
from .llm import normalize as nz
from .models import Listing, RecItem, Recommendation, ValidationError

MIN_MARKET_SAMPLES = 3  # below this, the price table's estimate is used instead


@dataclass
class Scored:
    listing: Listing
    item: RecItem
    score: float  # 0..1, higher is better
    why: str
    typical: int | None


def market_price(item: RecItem, listings: list[Listing], condition: str) -> int | None:
    """Median price of comparable collected listings (same kind, size in range, condition)."""
    prices = []
    for listing in listings:
        m = fits(item, listing)
        if m is None or not m.priced:
            continue
        is_new = listing.is_new is True
        if (condition == "new") != is_new:
            continue
        prices.append(listing.price_ils)
    if len(prices) < MIN_MARKET_SAMPLES:
        return None
    return int(statistics.median(prices))


def typical_price(item: RecItem, listings: list[Listing], condition: str) -> tuple[int | None, str]:
    """(price, where it comes from): market median when enough listings exist, else table."""
    market = market_price(item, listings, condition)
    if market is not None:
        return market, "market"
    try:
        return pricing.estimate_item(item, condition), "estimate"
    except ValueError:
        return None, ""


def _fit_score(item: RecItem, listing: Listing) -> float:
    if item.size is None or listing.size is None or item.size_min is None:
        return 0.7  # size not comparable (harness, bar without width…): neutral-ish
    span = max(1.0, (item.size_max - item.size_min) / 2)
    return max(0.0, 1 - abs(listing.size - item.size) / (span * 2))


def score_listing(
    item: RecItem,
    listing: Listing,
    listings: list[Listing],
    condition_score: float | None = None,
    condition_pref: str = "both",
    min_year: int | None = None,
    typical_cache: dict | None = None,
) -> Scored | None:
    m = fits(item, listing, condition_pref, min_year)
    if m is None:
        return None
    condition = "new" if listing.is_new else "used"
    # the market median scans every listing: once per (item, condition), not per listing
    key = (id(item), condition)
    if typical_cache is not None and key in typical_cache:
        typical, origin = typical_cache[key]
    else:
        typical, origin = typical_price(item, listings, condition)
        if typical_cache is not None:
            typical_cache[key] = (typical, origin)
    reasons = [_slot(item)]
    if m.priced and typical:
        ratio = listing.price_ils / typical
        price_score = max(0.0, min(1.0, 1.25 - 0.5 * ratio))
        diff = typical - listing.price_ils
        word = "market" if origin == "market" else "typical"
        if abs(diff) >= typical * 0.05:
            rel = "below" if diff > 0 else "above"
            reasons.append(f"₪{abs(diff):,} {rel} the {word} ₪{typical:,}")
        else:
            reasons.append(f"about the {word} ₪{typical:,}")
    else:
        price_score = 0.4
        reasons.append("no price stated — ask the seller")
    fit = _fit_score(item, listing)
    cond = condition_score / 10 if condition_score is not None else 0.6
    score = 0.5 * fit + 0.25 * cond + 0.25 * price_score  # your choice: size fit first
    if not m.verified_size:
        score -= 0.05
        reasons.append("size not stated")
    if not m.year_known:
        score -= 0.05
        reasons.append("year not stated")
    return Scored(listing, item, round(max(0.0, min(1.0, score)), 3), "; ".join(reasons), typical)


def _slot(item: RecItem) -> str:
    if item.type == "harness":
        return f"fits your harness size {item.subtype}"
    unit = item.unit or ""
    sep = "" if unit in ("m²", "") else " "
    if item.size_max is not None and item.size_max >= 1e8:
        return f"a {item.type} (any size)"
    if item.size_min is not None and item.size_max is not None:
        return f"fits your {item.type} {item.size_min:g}–{item.size_max:g}{sep}{unit}"
    return f"a {item.type} for your set"


def match_recommendation(rec: Recommendation, listings: list[Listing],
                         conditions: dict[int, float] | None = None, condition_pref: str = "both",
                         min_year: int | None = None) -> list[Scored]:  # fmt: skip
    """Every (listing, item) pair that fits, best score per listing, best first."""
    conditions = conditions or {}
    best: dict[int, Scored] = {}
    cache: dict = {}
    for listing in listings:
        for item in rec.items:
            s = score_listing(item, listing, listings, conditions.get(listing.id), condition_pref,
                              min_year, cache)  # fmt: skip
            if s is not None and (listing.id not in best or s.score > best[listing.id].score):
                best[listing.id] = s
    return sorted(best.values(), key=lambda s: (-s.score, s.listing.id or 0))


# --- typed searches ("kite 12m", "טרפז M", "board 138") ---------------------------------------

TOLERANCE = {"kite": 1.0, "board": 3.0, "bar": 5.0, "foil": 300.0}
UNITS = {"kite": "m²", "board": "cm", "bar": "cm", "foil": "cm²"}


def parse_query(text: str) -> RecItem:
    """A free-text search into the item it asks for ("kite" alone: any size)."""
    t, sub = nz.normalize_type(text)
    if t == "other":
        raise ValidationError(f"say what you're looking for, e.g. 'kite 12m' — not {text!r}")
    reason, unit = f"search: {text}", UNITS.get(t, "")
    if t == "harness":
        label = nz.normalize_size_label(text) or "XS/S/M/L/XL/XXL"
        return RecItem("harness", None, reason=reason, subtype=label)
    # "kite 12m 2021": drop the year — but not for foils, whose sizes are cm² like 2000
    cleaned = text if t == "foil" else re.sub(r"(?<!\d)(19|20)\d{2}(?!\d)", "", text)
    size = nz.parse_size(cleaned, t)
    if size is None:
        return RecItem(t, None, 0, 1e9, reason=reason, subtype=sub, unit=unit)
    tol = TOLERANCE.get(t, 1.0)
    return RecItem(t, size, size - tol, size + tol, reason=reason, subtype=sub, unit=unit)


def search(text: str, listings: list[Listing], conditions: dict[int, float] | None = None,
           condition_pref: str = "both", min_year: int | None = None) -> list[Scored]:  # fmt: skip
    rec = Recommendation(profile=None, items=[parse_query(text)], kind="single")  # type: ignore[arg-type]
    return match_recommendation(rec, listings, conditions, condition_pref, min_year)
