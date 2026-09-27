"""Turn surfing areas ("Bat Galim", "בת גלים", "north") into an expected wind range."""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field

from ..models import ValidationError
from .engine import load_reference

SEASONS = ("all", "summer", "winter")
REGION_LABELS = {
    "north": "North (צפון)",
    "center": "Center (מרכז)",
    "south": "South (דרום)",
    "eilat": "Eilat (אילת)",
    "kinneret": "Kinneret (כנרת)",
}

_NIQQUD = re.compile(r"[֑-ׇ]")
_PUNCT = re.compile(r"[\"'׳״`\-–—_.,/()]+")


def normalize_name(text: str) -> str:
    """Lower-case; drop Hebrew niqqud, quotes, hyphens and extra spaces."""
    text = unicodedata.normalize("NFC", text)
    text = _NIQQUD.sub("", text).lower()
    text = _PUNCT.sub(" ", text)
    return " ".join(text.split())


@dataclass
class Spot:
    name: str
    region: str
    aliases: list[str]
    summer: tuple[float, float]
    winter: tuple[float, float]
    gusty: bool
    water: str

    def wind(self, season: str = "all") -> tuple[float, float]:
        if season == "summer":
            return self.summer
        if season == "winter":
            return self.winter
        return min(self.summer[0], self.winter[0]), max(self.summer[1], self.winter[1])


@dataclass
class AreaWind:
    wind_min_kn: float
    wind_max_kn: float
    gusty: bool
    spots: list[Spot] = field(default_factory=list)
    season: str = "all"

    @property
    def spot_names(self) -> list[str]:
        return [s.name for s in self.spots]


def load_spots() -> tuple[list[Spot], dict[str, list[str]]]:
    data = load_reference("spots_il")
    spots = [
        Spot(
            name=s["name"],
            region=s["region"],
            aliases=list(s["aliases"]),
            summer=tuple(s["summer"]),
            winter=tuple(s["winter"]),
            gusty=bool(s["gusty"]),
            water=s["water"],
        )
        for s in data["spots"]
    ]
    return spots, data["regions"]


def _index() -> tuple[dict[str, list[Spot]], list[Spot]]:
    """normalized name -> spots it stands for (a region stands for several)."""
    spots, regions = load_spots()
    index: dict[str, list[Spot]] = {}
    for spot in spots:
        for key in (spot.name, *spot.aliases):
            index[normalize_name(key)] = [spot]
    for region, names in regions.items():
        members = [s for s in spots if s.region == region]
        for key in names:
            index.setdefault(normalize_name(key), members)
    return index, spots


def split_areas(text: str) -> list[str]:
    """Several spots in one line: 'Bat Galim, Sdot Yam; אילת', 'Bat Galim and Herzliya',
    'בת גלים ושדות ים' → one name each. Commas, semicolons, new lines, '&', '+' and the word
    'and' separate names; a Hebrew 'ו' ("and") in front of a known spot does too."""
    parts = re.split(r"[,;\n&+]+|\s+and\s+", text, flags=re.IGNORECASE)
    out = []
    for part in (p.strip() for p in parts):
        if part:
            out.extend(_split_hebrew_and(part))
    return out


def _split_hebrew_and(part: str) -> list[str]:
    """'בת גלים ושדות ים' → ['בת גלים', 'שדות ים'] — only where both halves are known names,
    so a name that itself contains 'ו' is never cut."""
    index, _ = _index()
    if normalize_name(part) in index:
        return [part]
    words = part.split()
    for i in range(1, len(words)):
        if words[i].startswith("ו") and len(words[i]) > 1:
            left = " ".join(words[:i])
            right = " ".join([words[i][1:], *words[i + 1 :]])
            if normalize_name(left) in index:
                rest = _split_hebrew_and(right)
                if all(normalize_name(r) in index for r in rest):
                    return [left, *rest]
    return [part]


def resolve_areas(names: list[str], season: str = "all") -> AreaWind:
    if season not in SEASONS:
        raise ValidationError(f"season must be one of {', '.join(SEASONS)}")
    if not names:
        raise ValidationError("name at least one area, e.g. 'Bat Galim' or 'north'")
    index, _ = _index()
    chosen: list[Spot] = []
    problems = []
    for raw in names:
        key = normalize_name(raw)
        found = index.get(key)
        if found is None:
            hint = ""
            close = difflib.get_close_matches(key, index, n=1, cutoff=0.6)
            if close:
                target = index[close[0]]
                # a spot suggests its proper name; a region suggests the region word itself
                hint = f" — did you mean {target[0].name if len(target) == 1 else close[0]}?"
            problems.append(f"Unknown area '{raw}'{hint}")
            continue
        chosen.extend(s for s in found if s not in chosen)
    if problems:
        raise ValidationError("; ".join(problems) + " (see: kitefinder areas)")
    ranges = [s.wind(season) for s in chosen]
    return AreaWind(
        wind_min_kn=min(lo for lo, _ in ranges),
        wind_max_kn=max(hi for _, hi in ranges),
        gusty=any(s.gusty for s in chosen),
        spots=chosen,
        season=season,
    )


def format_areas() -> str:
    """Human-readable list of every known spot, grouped by region."""
    spots, _ = load_spots()
    lines = []
    for region, label in REGION_LABELS.items():
        members = [s for s in spots if s.region == region]
        if not members:
            continue
        lines.append(label + ":")
        for s in members:
            hebrew = next((a for a in s.aliases if re.search(r"[א-ת]", a)), "")
            extra = ", gusty" if s.gusty else ""
            lines.append(
                f"  {s.name}{f' ({hebrew})' if hebrew else ''} — summer {s.summer[0]:g}–"
                f"{s.summer[1]:g} kn, winter {s.winter[0]:g}–{s.winter[1]:g} kn, {s.water}{extra}"
            )
    lines.append('Type spot names or a region, e.g. --areas "בת גלים, Sdot Yam" or --areas north')
    return "\n".join(lines)
