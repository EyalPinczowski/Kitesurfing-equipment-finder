"""Core data types shared across the agent."""

from __future__ import annotations

from dataclasses import KW_ONLY, asdict, dataclass, field
from typing import Any

EQUIPMENT_TYPES = ("kite", "bar", "board", "harness", "foil", "wetsuit", "other")
SKILL_LEVELS = ("beginner", "intermediate", "advanced")
STYLES = ("twintip", "surfboard", "foil")
# optional twin tip riding focus: which kite models suit you best ("" = any twin tip riding)
DISCIPLINES = ("freeride", "bigair", "freestyle")
# Boards and foils come in kinds that need different sizing (and units).
SUBTYPES = {
    "board": ("twintip", "surfboard", "foilboard"),
    "foil": ("front_wing", "mast", "complete"),
}
CONDITION_PREFS = ("new", "used", "both")
WIND_SOURCES = ("manual", "areas")
SEASONS = ("all", "summer", "winter")
# Best first: used to pick "the best set that fits the budget".
QUIVER_VARIANTS = ("comfortable", "minimum", "one_kite")
MARK_STATUSES = ("favorite", "dismissed")
MARK_KINDS = ("listing", "rec_item")


class ValidationError(ValueError):
    """Raised when user-supplied data is out of range or malformed."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValidationError(msg)


@dataclass
class Profile:
    weight_kg: float
    waist_cm: float
    wind_min_kn: float
    wind_max_kn: float
    skill: str = "intermediate"
    style: str = "twintip"
    spots: list[str] = field(default_factory=list)
    budget_ils: int | None = None
    condition_pref: str = "both"
    travel_km: int | None = None
    home_location: str = ""
    gusty: bool = False
    wind_source: str = "manual"  # "areas" when the wind range was derived from spots
    season: str = "all"
    min_year: int | None = None  # skip listings older than this (unknown years are kept)
    discipline: str = ""  # twin tip focus: freeride / bigair / freestyle ("" = any)

    def validate(self) -> Profile:
        _require(30 <= self.weight_kg <= 150, "weight must be between 30 and 150 kg")
        _require(50 <= self.waist_cm <= 150, "hip/waist must be between 50 and 150 cm")
        _require(4 <= self.wind_min_kn <= 50, "minimum wind must be between 4 and 50 knots")
        _require(4 <= self.wind_max_kn <= 60, "maximum wind must be between 4 and 60 knots")
        _require(self.wind_min_kn < self.wind_max_kn, "minimum wind must be below maximum wind")
        _require(self.skill in SKILL_LEVELS, f"skill must be one of {', '.join(SKILL_LEVELS)}")
        _require(self.style in STYLES, f"style must be one of {', '.join(STYLES)}")
        _require(
            self.discipline in ("", *DISCIPLINES),
            f"riding focus must be one of {', '.join(DISCIPLINES)} (or empty)",
        )
        _require(
            not self.discipline or self.style == "twintip",
            "a riding focus (freeride / big air / freestyle) is for twin tip riders",
        )
        _require(
            self.condition_pref in CONDITION_PREFS,
            f"condition must be one of {', '.join(CONDITION_PREFS)}",
        )
        _require(self.wind_source in WIND_SOURCES, "wind source must be manual or areas")
        _require(self.season in SEASONS, f"season must be one of {', '.join(SEASONS)}")
        _require(self.budget_ils is None or self.budget_ils >= 0, "budget cannot be negative")
        _require(
            self.min_year is None or 1995 <= self.min_year <= 2100,
            "minimum year must be between 1995 and 2100",
        )
        _require(
            self.travel_km is None or self.travel_km >= 0, "travel distance cannot be negative"
        )
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Profile:
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class OwnedItem:
    type: str
    brand: str = ""
    model: str = ""
    # kite m², twintip/surfboard length cm, foilboard volume L, front wing cm², bar width cm
    size: float | None = None
    year: int | None = None
    notes: str = ""
    subtype: str = ""
    id: int | None = None

    def validate(self) -> OwnedItem:
        _require(self.type in EQUIPMENT_TYPES, f"type must be one of {', '.join(EQUIPMENT_TYPES)}")
        allowed = SUBTYPES.get(self.type, ())
        _require(
            not self.subtype or self.subtype in allowed,
            f"subtype for {self.type} must be one of {', '.join(allowed) or '(none)'}",
        )
        _require(self.size is None or self.size > 0, "size must be positive")
        _require(self.year is None or 1995 <= self.year <= 2100, "year looks wrong")
        return self


@dataclass
class RecItem:
    type: str
    size: float | None
    size_min: float | None = None
    size_max: float | None = None
    wind_min_kn: float | None = None
    wind_max_kn: float | None = None
    reason: str = ""
    subtype: str = ""
    unit: str = ""
    est_price_ils: int | None = None  # estimated price in the rec's price_condition
    id: int | None = None
    recommendation_id: int | None = None


@dataclass
class Recommendation:
    profile: Profile
    items: list[RecItem]
    explanation: str = ""
    kind: str = "set"  # "set" or "single"
    variant: str = "minimum"  # quiver variant for sets: see QUIVER_VARIANTS
    price_condition: str = "used"  # which prices est_price_ils uses: "new" or "used"
    budget_ils: int | None = None  # the budget this set was chosen for, if any
    id: int | None = None
    created_at: str | None = None


@dataclass
class Listing:
    """One item for sale, extracted from a post or product page."""

    type: str
    price_ils: int | None = None
    brand: str = ""
    model: str = ""
    size: float | None = None  # same units as RecItem for that type
    _: KW_ONLY  # everything below must be named: Listing(..., year=2021, is_new=False)
    subtype: str = ""  # board/foil kind
    size_label: str = ""  # harness S/M/L…
    year: int | None = None
    is_new: bool | None = None  # None = not stated
    location: str = ""
    description: str = ""
    sold: bool = False
    source: str = ""  # facebook / yad2 / site host
    url: str = ""
    seller: str = ""
    flags: list[str] = field(default_factory=list)  # extraction notes, e.g. "size_not_in_text"
    extracted_by: str = ""  # "gemini" | "rules" | "" (entered by hand)
    bundle_price_ils: int | None = None  # the post's one price for several items together
    id: int | None = None
