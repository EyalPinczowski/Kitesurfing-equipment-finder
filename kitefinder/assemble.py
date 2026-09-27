"""Build a recommended set from real listings, mixing sellers and sites for the lowest price.

Brand modes:
  mixed      any brand for any item (cheapest)
  same       every item from one brand
  kites_bar  kites and bar from one brand (a bar usually only flies its own brand's kites);
             board and harness can be any brand
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from . import pricing
from .models import (
    EQUIPMENT_TYPES,
    QUIVER_VARIANTS,
    Listing,
    RecItem,
    Recommendation,
    ValidationError,
)

BRAND_MODES = ("mixed", "same", "kites_bar")
CANDIDATES_PER_SLOT = 6  # cheapest few per item keep the search small on a phone
EPS = 0.01
# An unstated price is ranked as the typical price plus this margin: a listing with a real
# price near typical wins, but one listed far above typical loses to "ask the seller".
UNPRICED_MARGIN = 1.15

# Canonical brand -> other spellings (Hebrew posts often write brands in Hebrew).
BRAND_ALIASES = {
    "Duotone": ["duotone", "דואוטון", "דואטון"],
    "North": ["north", "north kiteboarding", "נורת", "נורט'"],
    "Cabrinha": ["cabrinha", "קברינה"],
    "F-One": ["f-one", "fone", "f one", "אף וואן", "אףוואן"],
    "Core": ["core", "core kiteboarding", "קור"],
    "Ozone": ["ozone", "אוזון"],
    "Naish": ["naish", "נאיש"],
    "Slingshot": ["slingshot", "סלינגשוט"],
    "Airush": ["airush", "איירוש"],
    "Eleveight": ["eleveight", "אלבייט"],
    "Reedin": ["reedin", "רידין"],
    "Flysurfer": ["flysurfer", "פליסרפר"],
    "Crazyfly": ["crazyfly", "crazy fly", "קרייזי פליי"],
    "Ocean Rodeo": ["ocean rodeo"],
    "Liquid Force": ["liquid force", "lf"],
    "Nobile": ["nobile", "נוביל"],
    "Mystic": ["mystic", "מיסטיק"],
    "ION": ["ion", "איון"],
    "Manera": ["manera", "מנרה"],
    "Dakine": ["dakine", "דקיין"],
    "Ride Engine": ["ride engine", "רייד אנג'ין"],
}
_ALIAS_INDEX = {
    re.sub(r"[\s\-']+", "", alias.lower()): canon
    for canon, aliases in BRAND_ALIASES.items()
    for alias in (canon, *aliases)
}


def normalize_brand(brand: str) -> str:
    """Canonical brand name; unknown brands keep their own (title-cased) name; '' if none."""
    key = re.sub(r"[\s\-']+", "", (brand or "").strip().lower())
    if not key:
        return ""
    return _ALIAS_INDEX.get(key, brand.strip().title())


# --- matching ---------------------------------------------------------------------------------


@dataclass
class Match:
    listing: Listing
    verified_size: bool  # False when the listing did not state a size we could check
    cost: int = 0  # what the search ranks by: the listed price, or typical + margin
    priced: bool = True
    year_known: bool = True
    typical: int = 0  # typical price shown for an unpriced listing


def _mk(item: RecItem, listing: Listing, verified_size: bool) -> Match:
    """A match costed at its listed price (unpriced ones are costed in `match`)."""
    priced = listing.price_ils is not None
    return Match(listing, verified_size, listing.price_ils if priced else 0, priced)


def typical_price(item: RecItem, listing: Listing, condition_pref: str) -> int:
    """Typical price for an unpriced listing: new prices for new items (or new-only riders)."""
    new = listing.is_new is True or condition_pref == "new"
    try:
        return pricing.estimate_item(item, "new" if new else "used")
    except ValueError:
        return 0


def match(
    item: RecItem, listing: Listing, condition_pref: str = "both", min_year: int | None = None
) -> Match | None:
    """Does this listing fill this recommended item? None if not.

    Listings without a price or year are kept (flagged), since many posts leave them out;
    a stated year older than `min_year` rules the listing out.
    """
    if listing.type != item.type or listing.sold:
        return None
    if min_year is not None and listing.year is not None and listing.year < min_year:
        return None
    if condition_pref == "new" and listing.is_new is not True:
        return None
    if condition_pref == "used" and listing.is_new is True:
        return None
    m = _match_size(item, listing)
    if m is not None:
        # an unknown year only matters when the rider asked for a minimum year
        m.year_known = min_year is None or listing.year is not None
        if not m.priced:
            m.typical = typical_price(item, listing, condition_pref)
            m.cost = round(m.typical * UNPRICED_MARGIN)
    return m


def _match_size(item: RecItem, listing: Listing) -> Match | None:
    lo, hi, size = item.size_min, item.size_max, listing.size
    if item.type == "kite":
        if size is None or not (lo - EPS <= size <= hi + EPS):
            return None
        return _mk(item, listing, True)
    if item.type == "board":
        if listing.subtype and item.subtype and listing.subtype != item.subtype:
            return None
        if size is None or not (lo - 2 - EPS <= size <= hi + 2 + EPS):
            return None
        return _mk(item, listing, True)
    if item.type == "bar":
        if size is None:
            return _mk(item, listing, False)  # bar widths are often not stated
        return _mk(item, listing, True) if lo - 3 <= size <= hi + 3 else None
    if item.type == "harness":
        wanted = {s.upper() for s in item.subtype.split("/") if s}
        if not listing.size_label:
            return _mk(item, listing, False)
        got = {s.upper() for s in re.split(r"[/,\s\-–]+", listing.size_label) if s}
        return _mk(item, listing, True) if wanted & got else None
    if item.type == "foil":
        if listing.subtype and item.subtype and listing.subtype != item.subtype:
            return None
        if size is None or lo is None:
            return _mk(item, listing, False)
        return _mk(item, listing, True) if lo - EPS <= size <= hi + EPS else None
    return None


# --- assembling -------------------------------------------------------------------------------


@dataclass
class Assembly:
    rec: Recommendation
    picks: list[tuple[RecItem, Match | None]]
    brand_mode: str
    brand: str = ""  # the single brand, in same / kites_bar modes
    warnings: list[str] = field(default_factory=list)
    brand_conflict: bool = False  # kites (or kites and bar) from different brands

    @property
    def missing(self) -> list[RecItem]:
        return [item for item, m in self.picks if m is None]

    @property
    def complete(self) -> bool:
        return not self.missing

    @property
    def total(self) -> int:
        """Known prices plus the typical price of items listed without one."""
        return sum(
            m.listing.price_ils if m.priced else m.typical for _, m in self.picks if m is not None
        )

    @property
    def unpriced(self) -> list[tuple[RecItem, Match]]:
        return [(item, m) for item, m in self.picks if m is not None and not m.priced]

    @property
    def sellers(self) -> set[str]:
        return {_seller(m.listing) for _, m in self.picks if m is not None}

    def score(self) -> tuple:
        return score_picks([m for _, m in self.picks], [item for item, _ in self.picks])


def _seller(listing: Listing) -> str:
    return listing.seller or listing.url or f"#{listing.id}"


def _missing_value(item: RecItem) -> int:
    try:
        return pricing.estimate_item(item, "used")
    except ValueError:
        return 0


def score_picks(picks: list[Match | None], slots: list[RecItem]) -> tuple:
    """Lower is better: fewest missing items, then the least valuable ones missing (a missing
    kite is worse than a missing harness), then cheapest, fewest pickups, checked sizes."""
    chosen = [m for m in picks if m is not None]
    return (
        len(picks) - len(chosen),
        sum(_missing_value(item) for item, m in zip(slots, picks, strict=True) if m is None),
        sum(m.cost for m in chosen),
        sum(1 for m in chosen if not m.priced),  # a real price beats a typical one
        len({_seller(m.listing) for m in chosen}),
        sum(1 for m in chosen if not m.verified_size) + sum(1 for m in chosen if not m.year_known),
    )


def _solve(slots: list[RecItem], options: list[list[Match]]) -> list[Match | None]:
    """Cheapest assignment with no listing used twice (small exhaustive search with pruning)."""
    best: list = [None, None]  # [score, picks]

    values = [_missing_value(item) for item in slots]

    def dfs(i: int, picks: list[Match | None], used: set[int], state: tuple) -> None:
        # state = (missing, missing value, cost) so far; each part only grows deeper in the
        # search, so a state already worse than the best complete answer can be dropped.
        if best[0] is not None and state > best[0][:3]:
            return
        if i == len(slots):
            k = score_picks(picks, slots)
            if best[0] is None or k < best[0]:
                best[0], best[1] = k, list(picks)
            return
        missing, mval, cost = state
        for m in options[i]:
            lid = id(m.listing) if m.listing.id is None else m.listing.id
            if lid in used:
                continue
            used.add(lid)
            picks.append(m)
            dfs(i + 1, picks, used, (missing, mval, cost + m.cost))
            picks.pop()
            used.discard(lid)
        picks.append(None)  # leave this item unfilled
        dfs(i + 1, picks, used, (missing + 1, mval + values[i], cost))
        picks.pop()

    dfs(0, [], set(), (0, 0, 0))
    return best[1]


def _options(
    items: list[RecItem],
    listings: list[Listing],
    condition_pref: str,
    brand_for: Callable[[RecItem], str | None],
    min_year: int | None = None,
) -> list[list[Match]]:
    out = []
    for item in items:
        want = brand_for(item)
        found = [
            m
            for listing in listings
            if (m := match(item, listing, condition_pref, min_year)) is not None
            and (want is None or normalize_brand(listing.brand) == want)
        ]
        found.sort(key=lambda m: (m.cost, not m.priced, not m.verified_size, m.listing.id or 0))
        out.append(found[:CANDIDATES_PER_SLOT])
    return out


def assemble(
    rec: Recommendation,
    listings: list[Listing],
    brand_mode: str = "mixed",
    condition_pref: str = "both",
    min_year: int | None = None,
) -> Assembly:
    """The cheapest way to buy this recommendation from the given listings."""
    if brand_mode not in BRAND_MODES:
        raise ValidationError(f"brands must be one of {', '.join(BRAND_MODES)}")
    items = list(rec.items)
    if brand_mode == "mixed":
        options = _options(items, listings, condition_pref, lambda _: None, min_year)
        result = Assembly(rec, list(zip(items, _solve(items, options), strict=True)), brand_mode)
    else:
        constrained = {"same": set(EQUIPMENT_TYPES), "kites_bar": {"kite", "bar"}}[brand_mode]
        # Brands that have at least one listing for an item the brand rule applies to.
        brands = sorted(
            {
                normalize_brand(listing.brand)
                for listing in listings
                if normalize_brand(listing.brand) and listing.type in constrained
            }
        )
        if not any(item.type in constrained for item in items):
            brands = [""]  # e.g. kites_bar when you already own kites and bar: no brand rule
        elif not brands:
            brands = [None]  # nothing branded: brand-bound items stay missing, others free
        result = None
        for brand in brands:

            def brand_for(it: RecItem, b=brand) -> str | None:
                if it.type not in constrained or b == "":
                    return None
                return b if b is not None else "\0no-brand"  # matches no listing

            options = _options(items, listings, condition_pref, brand_for, min_year)
            cand = Assembly(
                rec, list(zip(items, _solve(items, options), strict=True)), brand_mode, brand or ""
            )
            if result is None or cand.score() < result.score():
                result = cand
    _add_warnings(result)
    return result


def _add_warnings(a: Assembly) -> None:
    kite_brands = {
        normalize_brand(m.listing.brand) or "unknown brand"
        for item, m in a.picks
        if m is not None and item.type == "kite"
    }
    bar_brands = {
        normalize_brand(m.listing.brand) or "unknown brand"
        for item, m in a.picks
        if m is not None and item.type == "bar"
    }
    a.brand_conflict = len(kite_brands) > 1 or bool(
        kite_brands and bar_brands and kite_brands != bar_brands
    )
    if len(kite_brands) > 1:
        a.warnings.append(
            f"Kites from different brands ({', '.join(sorted(kite_brands))}): one bar may not "
            "fly them all — check compatibility or try --brands kites_bar."
        )
    elif kite_brands and bar_brands and kite_brands != bar_brands:
        a.warnings.append(
            f"Bar ({', '.join(sorted(bar_brands))}) and kite ({', '.join(sorted(kite_brands))}) "
            "brands differ — check they are compatible."
        )
    unverified = [item.type for item, m in a.picks if m is not None and not m.verified_size]
    if unverified:
        a.warnings.append(f"Size not stated for: {', '.join(unverified)} — ask the seller.")
    no_year = [item.type for item, m in a.picks if m is not None and not m.year_known]
    if no_year:
        a.warnings.append(f"Year not stated for: {', '.join(no_year)} — ask the seller.")
    if a.unpriced:
        kinds = ", ".join(item.type for item, _ in a.unpriced)
        a.warnings.append(
            f"No price stated for: {kinds} — the total uses typical prices for them; "
            "ask the seller."
        )


def newness(a: Assembly) -> tuple[float, float]:
    """(share of items bought new, mean known model year) — higher is newer."""
    chosen = [m.listing for _, m in a.picks if m is not None]
    if not chosen:
        return (0.0, 0.0)
    years = [x.year for x in chosen if x.year]
    share_new = sum(1 for x in chosen if x.is_new) / len(chosen)
    return (share_new, sum(years) / len(years) if years else 0.0)


def best_assembly_under(
    build: Callable[[str], Recommendation],
    listings: list[Listing],
    budget: int | None,
    brand_mode: str = "mixed",
    condition_pref: str = "both",
    min_year: int | None = None,
) -> Assembly:
    """The quiver with the newest gear (your choice, #5) that can be bought complete within the
    budget from real listings — more items new, then newer model years; ties go to the fuller
    quiver. Otherwise the cheapest complete one; otherwise the fullest."""
    results = [
        assemble(build(v), listings, brand_mode, condition_pref, min_year) for v in QUIVER_VARIANTS
    ]
    complete = [r for r in results if r.complete]
    if budget is not None:
        fitting = [r for r in complete if r.total <= budget]
        if fitting:  # results are in fullest-quiver-first order: max() keeps the first on ties
            return max(fitting, key=newness)
    if complete:
        return min(complete, key=lambda r: r.total)
    # the fullest: the most items actually found in listings, then the usual ranking
    return min(results, key=lambda r: (-sum(m is not None for _, m in r.picks), r.score()))
