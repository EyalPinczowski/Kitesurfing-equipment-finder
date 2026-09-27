"""Which riding each kite model is made for, so matches and recommendations fit your style.

A wave kite (Duotone Neo, North Carve) is built for riding a surfboard in waves; a big air
kite (North Orbit, Duotone Rebel) for jumping on a twin tip; a foil kite for hydrofoils. The
reference is `sizing/reference/kite_models.yaml` (verified: false).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from .models import DISCIPLINES
from .sizing.engine import load_reference

USES = ("freeride", "bigair", "freestyle", "wave", "foil")
USE_LABELS = {
    "freeride": "freeride",
    "bigair": "big air",
    "freestyle": "freestyle",
    "wave": "wave",
    "foil": "foil",
}
STYLE_USES = {"twintip": set(DISCIPLINES), "surfboard": {"wave"}, "foil": {"foil"}}
STYLE_LABELS = {"twintip": "twin tip", "surfboard": "surfboard (wave)", "foil": "foil"}


@dataclass(frozen=True)
class KiteModel:
    brand: str
    name: str
    uses: tuple[str, ...]

    @property
    def title(self) -> str:
        return f"{self.brand} {self.name}"

    @property
    def kind(self) -> str:
        """'big air / freeride'"""
        return " / ".join(USE_LABELS[u] for u in self.uses)


def _key(text: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", (text or "").lower()).split())


@lru_cache(maxsize=1)
def catalog() -> dict[str, tuple[KiteModel, ...]]:
    """Brand → its models, in the reference file's order."""
    data = load_reference("kite_models")["models"]
    out = {}
    for brand, models in data.items():
        for name, uses in models.items():
            unknown = set(uses) - set(USES)
            if unknown:
                raise ValueError(f"kite_models.yaml: {brand} {name}: unknown use {unknown}")
        out[brand] = tuple(KiteModel(brand, name, tuple(uses)) for name, uses in models.items())
    return out


def _longest_first(models) -> list[KiteModel]:
    return sorted(models, key=lambda m: -len(m.name))  # 'Moto X' is tried before 'Moto'


def _starts_with(text: str, name: str) -> bool:
    return bool(re.match(rf"{re.escape(_key(name))}(?:\s|$)", text))


def lookup(brand: str, model: str) -> KiteModel | None:
    """The catalog model for a listing's brand and model text ('Evo SLS 2023' → Evo).
    Without a brand, a model name only one brand uses still counts."""
    from .assemble import normalize_brand

    text = _key(model)
    if not text:
        return None
    b = normalize_brand(brand)
    if b:
        models = _longest_first(catalog().get(b, ()))
        return next((m for m in models if _starts_with(text, m.name)), None)
    found = [
        m for ms in catalog().values() for m in _longest_first(ms) if _starts_with(text, m.name)
    ]
    return found[0] if len({m.brand for m in found}) == 1 else None


def suits(model: KiteModel, style: str, discipline: str = "") -> str:
    """'best' (made for exactly this), 'ok' (made for this style) or 'no' (other riding)."""
    uses = set(model.uses)
    if not uses & STYLE_USES[style]:
        return "no"
    if style == "twintip" and discipline and discipline not in uses:
        return "ok"
    return "best"


def style_note(model: KiteModel, style: str, discipline: str = "") -> str:
    verdict = suits(model, style, discipline)
    if verdict == "no":
        return f"⚠ {model.title} is a {model.kind} kite — not made for {STYLE_LABELS[style]} riding"
    if verdict == "ok":
        return f"{model.title} is a {model.kind} kite (you asked for {USE_LABELS[discipline]})"
    return f"{model.title} suits your riding ({model.kind})"


def models_for(style: str, discipline: str = "", limit: int = 6) -> list[str]:
    """Models made for this riding, one per brand in turn (so the list isn't all one brand).
    Within a brand, models whose main use is this riding come first ('Drifter' before the
    all-round 'Moto' for wave)."""
    wanted = {discipline} if style == "twintip" and discipline else STYLE_USES[style]
    per_brand = [
        sorted(
            (m for m in models if suits(m, style, discipline) == "best"),
            key=lambda m: (m.uses[0] not in wanted, models.index(m)),
        )
        for models in catalog().values()
    ]
    out: list[str] = []
    for rank in range(max((len(b) for b in per_brand), default=0)):
        for picks in per_brand:
            if rank < len(picks) and len(out) < limit:
                out.append(picks[rank].title)
    return out
