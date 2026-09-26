"""Deterministic equipment sizing rules.

Kite size follows the common rule of thumb  size_m2 ≈ weight_kg × k / wind_kn  (k ≈ 2.2 for an
intermediate twin-tip rider), adjusted for riding style and skill. Board and harness sizes come
from the reference charts in `sizing/reference/`.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import yaml

REFERENCE_DIR = Path(__file__).parent / "reference"

STANDARD_KITE_SIZES = (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 17)
MAX_KITE = STANDARD_KITE_SIZES[-1]
MIN_KITE = STANDARD_KITE_SIZES[0]

K_BASE = 2.2
STYLE_FACTOR = {"twintip": 1.0, "surfboard": 0.9, "foil": 0.65}
SKILL_FACTOR = {"beginner": 0.95, "intermediate": 1.0, "advanced": 1.05}

# A kite is usable from slightly under its ideal wind (underpowered) to well above it
# (depowered with the bar); riders are more comfortable overpowered than underpowered.
USABLE_LOW = 0.85
USABLE_HIGH = 1.3


@lru_cache
def load_reference(name: str) -> dict:
    return yaml.safe_load((REFERENCE_DIR / f"{name}.yaml").read_text(encoding="utf-8"))


def power_factor(style: str = "twintip", skill: str = "intermediate") -> float:
    return K_BASE * STYLE_FACTOR[style] * SKILL_FACTOR[skill]


def ideal_kite_size(weight_kg: float, wind_kn: float, style="twintip", skill="intermediate"):
    """Unrounded ideal size in m²."""
    if wind_kn <= 0:
        raise ValueError("wind must be positive")
    return weight_kg * power_factor(style, skill) / wind_kn


def round_kite_size(raw: float) -> int:
    """Nearest size a kite is actually sold in, clamped to the standard range."""
    return min(STANDARD_KITE_SIZES, key=lambda s: (abs(s - raw), -s))


def kite_size_for(weight_kg: float, wind_kn: float, style="twintip", skill="intermediate") -> int:
    return round_kite_size(ideal_kite_size(weight_kg, wind_kn, style, skill))


def _round_half(x: float) -> float:
    return math.floor(x * 2 + 0.5) / 2


def kite_wind_range(size_m2: float, weight_kg: float, style="twintip", skill="intermediate"):
    """(low_kn, high_kn) this rider can use a kite of this size in."""
    ideal_wind = weight_kg * power_factor(style, skill) / size_m2
    return _round_half(ideal_wind * USABLE_LOW), _round_half(ideal_wind * USABLE_HIGH)


# --- boards -----------------------------------------------------------------------------------

SURFBOARD_ROWS = ((65, 150, 160), (80, 157, 168), (95, 165, 178), (999, 172, 185))


def twintip_length(weight_kg: float, skill="intermediate", wind_min_kn: float | None = None):
    """(min_cm, max_cm, recommended_cm). Beginners and light-wind riders go longer."""
    rows = load_reference("twintip_chart_generic")["rows"]
    lo, hi = next((lo, hi) for max_w, lo, hi in rows if weight_kg <= max_w)
    bump = 0
    if skill == "beginner":
        bump += 2
    if wind_min_kn is not None and wind_min_kn < 12:
        bump += 3
    lo, hi = lo + bump, hi + bump
    return lo, hi, round((lo + hi) / 2)


def surfboard_length(weight_kg: float):
    lo, hi = next((lo, hi) for max_w, lo, hi in SURFBOARD_ROWS if weight_kg <= max_w)
    return lo, hi, round((lo + hi) / 2)


def foilboard_volume(weight_kg: float, skill="intermediate"):
    """Litres. Beginners want a floaty board; experts go well under body weight."""
    offset = {"beginner": (10, 30), "intermediate": (-10, 10), "advanced": (-30, -10)}[skill]
    lo, hi = max(20, weight_kg + offset[0]), max(25, weight_kg + offset[1])
    return round(lo), round(hi), round((lo + hi) / 2)


def front_wing_area(weight_kg: float, skill="intermediate"):
    """cm². Bigger wings lift earlier and are more stable; smaller are faster."""
    per_kg = {"beginner": (22, 28), "intermediate": (16, 22), "advanced": (11, 16)}[skill]
    lo, hi = weight_kg * per_kg[0], weight_kg * per_kg[1]
    return round(lo, -1), round(hi, -1), round((lo + hi) / 2, -1)


# --- harness / bar ----------------------------------------------------------------------------


def harness_sizes(waist_cm: float) -> list[str]:
    """Matching size labels; two labels when the waist sits on a boundary."""
    sizes = load_reference("harness_chart_generic")["sizes"]
    matches = [label for label, lo, hi in sizes if lo <= waist_cm <= hi]
    if not matches:
        matches = [sizes[0][0]] if waist_cm < sizes[0][1] else [sizes[-1][0]]
    return matches


BAR_ROWS = ((7, 38, 45), (11, 45, 50), (14, 50, 55), (99, 55, 60))


def bar_width(kite_m2: float):
    """(min_cm, max_cm, recommended_cm) for the bar width to fly a kite of this size."""
    lo, hi = next((lo, hi) for max_k, lo, hi in BAR_ROWS if kite_m2 <= max_k)
    return lo, hi, round((lo + hi) / 2)
