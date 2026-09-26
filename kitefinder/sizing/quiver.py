"""Quiver planning: cover the rider's wind range with the fewest kites, reusing owned gear."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import (
    QUIVER_VARIANTS,
    OwnedItem,
    Profile,
    RecItem,
    Recommendation,
    ValidationError,
)
from . import engine

EPS = 0.01
# An owned kite that starts working up to this many knots above an uncovered wind is used
# (slightly underpowered) rather than buying a new kite for a sliver of the range.
OWNED_STRETCH_KN = 1.5


@dataclass
class KiteSlot:
    size: float
    wind_min_kn: float
    wind_max_kn: float
    owned: OwnedItem | None = None


@dataclass
class QuiverPlan:
    slots: list[KiteSlot]
    uncovered: list[tuple[float, float]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def new_kites(self) -> list[KiteSlot]:
        return [s for s in self.slots if s.owned is None]


# Comfortable quivers narrow each kite's usable band step by step: low edge 0.85 -> 0.95,
# high edge 1.3 -> 1.1 (t = 0 is the normal band, t = 1 the narrowest).
COMFORT_STEPS = [i / 20 for i in range(21)]


def band_at(t: float) -> tuple[float, float]:
    return engine.USABLE_LOW + 0.10 * t, engine.USABLE_HIGH - 0.20 * t


def plan_kites(profile: Profile, owned: list[OwnedItem], variant: str = "minimum") -> QuiverPlan:
    """Plan the kite quiver.

    "minimum": fewest kites. "comfortable": one kite more than minimum, spread so each kite is
    used well inside its range (the narrowest band that still needs only that many kites).
    """
    if variant not in QUIVER_VARIANTS:
        raise ValidationError(f"option must be one of {', '.join(QUIVER_VARIANTS)}")
    if variant == "one_kite":
        return _one_kite(profile, owned)
    minimum = _cover(profile, owned, band_at(0))
    if variant == "minimum":
        return minimum
    target = len(minimum.slots) + 1
    # Only plans that cover exactly what the minimum plan covers: a very narrow band can leave
    # holes between neighbouring standard sizes (e.g. 3 m² and 4 m²).
    plans = [_cover(profile, owned, band_at(t)) for t in COMFORT_STEPS]
    valid = [p for p in plans if p.uncovered == minimum.uncovered]
    exact = [p for p in valid if len(p.slots) == target]
    # narrowest band (most margin) with exactly one extra kite; else nothing better exists
    return exact[-1] if exact else minimum


def _one_kite(profile: Profile, owned: list[OwnedItem]) -> QuiverPlan:
    """A single kite for the whole range: the size covering most of it (an owned kite if it
    covers almost as much). The parts it can't reach are reported, not hidden."""
    style, skill, weight, gusty = profile.style, profile.skill, profile.weight_kg, profile.gusty
    lo_t, hi_t = profile.wind_min_kn, profile.wind_max_kn

    def overlap(size: float) -> float:
        lo, hi = engine.kite_wind_range(size, weight, style, skill, gusty)
        return min(hi, hi_t) - max(lo, lo_t)

    best = best_single_kite(weight, lo_t, hi_t, style, skill, gusty)
    owned_kites = [k for k in owned if k.type == "kite" and k.size]
    choice: OwnedItem | None = None
    if owned_kites:
        top = max(owned_kites, key=lambda k: overlap(k.size))
        if overlap(top.size) >= overlap(best) - OWNED_STRETCH_KN:
            choice = top
    size = choice.size if choice else best
    lo, hi = engine.kite_wind_range(size, weight, style, skill, gusty)
    plan = QuiverPlan(slots=[KiteSlot(size, lo, hi, owned=choice)])
    if lo > lo_t + EPS:
        plan.uncovered.append((lo_t, min(lo, hi_t)))
        plan.notes.append(f"One kite: underpowered below {lo:g} kn.")
    if hi < hi_t - EPS:
        plan.uncovered.append((max(hi, lo_t), hi_t))
        plan.notes.append(f"One kite: overpowered above {hi:g} kn.")
    return plan


def _cover(profile: Profile, owned: list[OwnedItem], band: tuple[float, float]) -> QuiverPlan:
    """Greedy interval cover from the lowest wind upwards.

    At each uncovered wind w: use an owned kite that covers w (the one reaching highest),
    otherwise add the smallest standard kite that still works at w — that reaches furthest
    up, so the greedy choice gives the fewest kites for this usable band.
    """
    style, skill, weight = profile.style, profile.skill, profile.weight_kg
    lo_target, hi_target = profile.wind_min_kn, profile.wind_max_kn

    def rng(size: float) -> tuple[float, float]:
        return engine.kite_wind_range(size, weight, style, skill, profile.gusty, band)

    owned_kites = [k for k in owned if k.type == "kite" and k.size]
    owned_ranges = [(k, *rng(k.size)) for k in owned_kites]
    plan = QuiverPlan(slots=[])
    used_owned: set[int] = set()
    w = lo_target

    # The lightest wind anything can cover: the biggest standard kite, or a bigger owned one.
    biggest_low = min([rng(engine.MAX_KITE)[0], *(lo for _, lo, _ in owned_ranges)])
    if w < biggest_low - EPS:
        cutoff = min(biggest_low, hi_target)
        plan.uncovered.append((w, cutoff))
        plan.notes.append(
            f"Below {biggest_low:g} kn you would need a kite bigger than "
            f"{max([engine.MAX_KITE, *(k.size for k in owned_kites)]):g} m². "
            "Consider a light-wind board, a foil, or accept fewer riding days."
        )
        w = biggest_low

    while w < hi_target - EPS:
        covering = [(k, lo, hi) for k, lo, hi in owned_ranges if lo <= w + EPS and hi > w + EPS]
        if covering:
            k, lo, hi = max(covering, key=lambda t: t[2])
            if id(k) not in used_owned:
                plan.slots.append(KiteSlot(k.size, lo, hi, owned=k))
                used_owned.add(id(k))
            w = hi
            continue
        near = [
            (k, lo, hi)
            for k, lo, hi in owned_ranges
            if id(k) not in used_owned and lo <= w + OWNED_STRETCH_KN and hi > w + EPS
        ]
        if near:
            k, lo, hi = min(near, key=lambda t: t[1])
            plan.slots.append(KiteSlot(k.size, lo, hi, owned=k))
            used_owned.add(id(k))
            plan.notes.append(
                f"Your {k.size:g} m² kite is slightly underpowered between {w:g} and {lo:g} kn."
            )
            w = hi
            continue
        candidates = [s for s in engine.STANDARD_KITE_SIZES if rng(s)[0] <= w + EPS]
        size = min(candidates)  # never empty: w >= low end of the biggest kite
        lo, hi = rng(size)
        if hi <= w + EPS:
            plan.uncovered.append((w, hi_target))
            plan.notes.append(
                f"Above {w:g} kn is beyond the smallest standard kite ({engine.MIN_KITE} m²) "
                "for your weight."
            )
            break
        plan.slots.append(KiteSlot(size, lo, hi))
        w = hi

    unused = [k for k in owned_kites if id(k) not in used_owned]
    for k in unused:
        lo, hi = rng(k.size)
        plan.notes.append(
            f"Your {k.size:g} m² kite ({lo:g}–{hi:g} kn) overlaps the others for this wind range."
        )
    plan.slots.sort(key=lambda s: s.size, reverse=True)
    return plan


def _owned_name(item: OwnedItem) -> str:
    name = " ".join(x for x in (item.brand, item.model) if x)
    return f"your {name} " if name else "your "


def recommend_set(
    profile: Profile, owned: list[OwnedItem], variant: str = "minimum"
) -> Recommendation:
    """Full set: kites for the wind range, then board, bar and harness if missing."""
    profile.validate()
    items: list[RecItem] = []
    notes: list[str] = []

    plan = plan_kites(profile, owned, variant)
    if profile.gusty:
        notes.append("Gusty spots: kite sizes biased ~7% smaller.")
    notes.extend(plan.notes)
    for slot in plan.new_kites:
        items.append(
            RecItem(
                "kite",
                slot.size,
                max(engine.MIN_KITE, slot.size - 1),
                min(engine.MAX_KITE, slot.size + 1),
                slot.wind_min_kn,
                slot.wind_max_kn,
                f"{'sweet spot' if variant == 'comfortable' else 'covers'} "
                f"{slot.wind_min_kn:g}–{slot.wind_max_kn:g} kn",
                unit="m²",
            )
        )
    for slot in plan.slots:
        if slot.owned is not None:
            notes.append(
                f"Using {_owned_name(slot.owned)}{slot.size:g} m² kite for "
                f"{slot.wind_min_kn:g}–{slot.wind_max_kn:g} kn."
            )

    items.extend(_board_items(profile, owned, notes))

    kite_sizes = [s.size for s in plan.slots]
    if not any(o.type == "bar" for o in owned) and kite_sizes:
        mid = sorted(kite_sizes)[len(kite_sizes) // 2]
        lo, hi, rec = engine.bar_width(mid)
        spread = {engine.bar_width(s)[2] for s in kite_sizes}
        reason = f"bar for {mid:g} m²"
        if len(spread) > 1:
            reason += "; an adjustable-length bar can fly your whole quiver"
        items.append(RecItem("bar", rec, lo, hi, reason=reason, unit="cm"))
    elif kite_sizes:
        notes.append("You already own a bar — check it is compatible with any new kite brand.")

    if not any(o.type == "harness" for o in owned):
        labels = engine.harness_sizes(profile.waist_cm)
        reason = f"waist {profile.waist_cm:g} cm → size {' or '.join(labels)}"
        if len(labels) > 1:
            reason += " (between sizes: try both on)"
        items.append(RecItem("harness", None, reason=reason, subtype="/".join(labels)))

    rec = Recommendation(profile=profile, items=items, kind="set", variant=variant)
    rec.explanation = explain(rec, plan, notes)
    return rec


def _board_items(profile: Profile, owned: list[OwnedItem], notes: list[str]) -> list[RecItem]:
    style, weight, skill = profile.style, profile.weight_kg, profile.skill
    if style == "twintip":
        subtype, unit = "twintip", "cm"
        lo, hi, rec = engine.twintip_length(weight, skill, profile.wind_min_kn)
        what = f"twin tip for {weight:g} kg"
    elif style == "surfboard":
        subtype, unit = "surfboard", "cm"
        lo, hi, rec = engine.surfboard_length(weight)
        what = f"directional surfboard for {weight:g} kg"
    else:
        subtype, unit = "foilboard", "L"
        lo, hi, rec = engine.foilboard_volume(weight, skill)
        what = f"foil board for {weight:g} kg, {skill}"

    items: list[RecItem] = []
    boards = [o for o in owned if o.type == "board" and o.subtype in ("", subtype)]
    fitting = [b for b in boards if b.size is not None and lo - 2 <= b.size <= hi + 2]
    if fitting:
        b = fitting[0]
        notes.append(f"Your {b.size:g} {unit} board fits ({lo}–{hi} {unit} recommended).")
    else:
        reason = what
        sized = [b for b in boards if b.size is not None]
        if sized:
            b = sized[0]
            direction = "small" if b.size < lo else "big"
            reason += f"; your {b.size:g} {unit} board is on the {direction} side"
        items.append(RecItem("board", rec, lo, hi, reason=reason, subtype=subtype, unit=unit))

    if style == "foil":
        items.extend(_foil_items(owned, weight, skill))
    return items


def _foil_items(owned: list[OwnedItem], weight: float, skill: str) -> list[RecItem]:
    """What's missing to ride a foil: a complete foil, or the front wing / mast you lack."""
    have = {o.subtype or "complete" for o in owned if o.type == "foil"}
    if "complete" in have or {"front_wing", "mast"} <= have:
        return []
    flo, fhi, frec = engine.front_wing_area(weight, skill)
    wing = f"front wing ~{frec:g} cm² for {weight:g} kg, {skill}"
    if "mast" in have:
        return [RecItem("foil", frec, flo, fhi, reason=wing, subtype="front_wing", unit="cm²")]
    if "front_wing" in have:
        return [RecItem("foil", None, reason="mast and fuselage for your wing", subtype="mast")]
    return [
        RecItem(
            "foil",
            frec,
            flo,
            fhi,
            reason=f"complete foil (mast, fuselage, wings); {wing}",
            subtype="complete",
            unit="cm²",
        )
    ]


def explain(rec: Recommendation, plan: QuiverPlan, notes: list[str]) -> str:
    p = rec.profile
    kites = sorted(plan.slots, key=lambda s: s.size, reverse=True)
    covered = ", ".join(
        f"{s.size:g} m² ({s.wind_min_kn:g}–{s.wind_max_kn:g} kn){' [owned]' if s.owned else ''}"
        for s in kites
    )
    option = {
        "minimum": "Option: minimum — fewest kites.",
        "comfortable": "Option: comfortable — more overlap between kites.",
        "one_kite": "Option: one kite — simplest and cheapest.",
    }[rec.variant]
    lines = [
        f"Set for {p.weight_kg:g} kg, {p.wind_min_kn:g}–{p.wind_max_kn:g} kn, "
        f"{p.style}, {p.skill}.",
        option,
        f"Kite quiver{' (sweet spots)' if rec.variant == 'comfortable' else ''}: "
        f"{covered or 'none'}.",
    ]
    if not plan.slots:
        lines.append("No standard kite works in this wind range.")
    elif not plan.new_kites and not plan.uncovered:
        lines.append("Your kites already cover the whole wind range.")
    lines.extend(notes)
    return "\n".join(lines)


def best_single_kite(
    weight: float, lo_w: float, hi_w: float, style: str, skill: str, gusty: bool = False
) -> int:
    """The standard size whose usable range overlaps [lo_w, hi_w] the most.

    Ties go to the size closest to the ideal at the middle of the range.
    """
    ideal = engine.ideal_kite_size(weight, (lo_w + hi_w) / 2, style, skill, gusty)

    def score(size: int) -> tuple[float, float]:
        rlo, rhi = engine.kite_wind_range(size, weight, style, skill, gusty)
        return (min(hi_w, rhi) - max(lo_w, rlo), -abs(size - ideal))

    return max(engine.STANDARD_KITE_SIZES, key=score)


def recommend_single(
    profile: Profile,
    item_type: str,
    wind: tuple[float, float] | None = None,
) -> Recommendation:
    """One item for a specific request, e.g. 'which kite for 18–24 kn'."""
    profile.validate()
    style, skill, weight = profile.style, profile.skill, profile.weight_kg
    lo_w, hi_w = wind or (profile.wind_min_kn, profile.wind_max_kn)
    if not (4 <= lo_w < hi_w <= 60):
        raise ValidationError("wind range must be low-high knots between 4 and 60, e.g. 15-22")
    if item_type == "kite":
        size = best_single_kite(weight, lo_w, hi_w, style, skill, profile.gusty)
        rlo, rhi = engine.kite_wind_range(size, weight, style, skill, profile.gusty)
        reason = f"best single kite for {lo_w:g}–{hi_w:g} kn (usable {rlo:g}–{rhi:g} kn)"
        if rlo > lo_w + EPS or rhi < hi_w - EPS:
            reason += "; one kite can't cover the whole range — ask for a set"
        item = RecItem(
            "kite",
            size,
            max(engine.MIN_KITE, size - 1),
            min(engine.MAX_KITE, size + 1),
            rlo,
            rhi,
            reason,
            unit="m²",
        )
        items = [item]
    elif item_type == "board":
        items = _board_items(profile, [], [])
    elif item_type == "harness":
        labels = engine.harness_sizes(profile.waist_cm)
        items = [
            RecItem(
                "harness",
                None,
                reason=f"waist {profile.waist_cm:g} cm → size {' or '.join(labels)}",
                subtype="/".join(labels),
            )
        ]
    elif item_type == "bar":
        size = engine.kite_size_for(weight, (lo_w + hi_w) / 2, style, skill, profile.gusty)
        lo, hi, rec = engine.bar_width(size)
        items = [RecItem("bar", rec, lo, hi, reason=f"bar for a {size} m² kite", unit="cm")]
    elif item_type == "foil":
        lo, hi, rec = engine.front_wing_area(weight, skill)
        items = [
            RecItem(
                "foil",
                rec,
                lo,
                hi,
                reason=f"front wing area for {weight:g} kg, {skill}",
                subtype="front_wing",
                unit="cm²",
            )
        ]
    else:
        raise ValueError(f"no sizing rule for {item_type!r}")
    rec = Recommendation(profile=profile, items=items, kind="single")
    rec.explanation = "; ".join(i.reason for i in items)
    return rec
