import pytest

from kitefinder.models import OwnedItem, Profile, ValidationError
from kitefinder.sizing import engine, quiver


def prof(weight=80, lo=12, hi=25, **kw):
    return Profile(
        weight_kg=weight, waist_cm=kw.pop("waist", 86), wind_min_kn=lo, wind_max_kn=hi, **kw
    )


def coverage_gaps(plan, lo, hi):
    """Wind values in [lo, hi] (0.5 kn steps) that no slot covers and aren't declared uncovered."""
    stretched = quiver.OWNED_STRETCH_KN
    gaps = []
    w = lo
    while w <= hi:
        covered = any(
            s.wind_min_kn - (stretched if s.owned else 0) <= w <= s.wind_max_kn for s in plan.slots
        )
        declared = any(a <= w <= b for a, b in plan.uncovered)
        if not covered and not declared:
            gaps.append(w)
        w += 0.5
    return gaps


# --- coverage properties over a grid of riders ------------------------------------------------

GRID = [
    (weight, lo, hi, style, skill)
    for weight in (50, 65, 80, 95, 110)
    for lo, hi in ((8, 15), (12, 25), (15, 35), (20, 30), (10, 40))
    for style in ("twintip", "surfboard", "foil")
    for skill in ("beginner", "advanced")
]


@pytest.mark.parametrize("weight, lo, hi, style, skill", GRID)
def test_quiver_covers_whole_wind_range(weight, lo, hi, style, skill):
    p = prof(weight, lo, hi, style=style, skill=skill)
    plan = quiver.plan_kites(p, [])
    assert coverage_gaps(plan, lo, hi) == []
    sizes = [s.size for s in plan.slots]
    assert len(sizes) == len(set(sizes)), "no duplicate sizes"
    assert all(s in engine.STANDARD_KITE_SIZES for s in sizes)
    assert len(sizes) <= 5


@pytest.mark.parametrize("weight, lo, hi, style, skill", GRID[::7])
def test_quiver_with_owned_kites_still_covers(weight, lo, hi, style, skill):
    owned = [OwnedItem("kite", size=12), OwnedItem("kite", size=8)]
    p = prof(weight, lo, hi, style=style, skill=skill)
    plan = quiver.plan_kites(p, owned)
    assert coverage_gaps(plan, lo, hi) == []
    new_sizes = {s.size for s in plan.new_kites}
    assert not new_sizes & {12, 8}, "never recommend a size the rider already owns"


def test_typical_80kg_quiver():
    plan = quiver.plan_kites(prof(), [])
    assert [s.size for s in plan.slots] == [13, 9]
    assert plan.uncovered == [] and plan.notes == []


def test_owned_kites_fully_cover_range():
    owned = [OwnedItem("kite", size=13), OwnedItem("kite", size=9)]
    plan = quiver.plan_kites(prof(), owned)
    assert plan.new_kites == []
    assert {s.size for s in plan.slots} == {13, 9}


def test_owned_kite_fills_gap_and_recommends_the_rest():
    plan = quiver.plan_kites(prof(), [OwnedItem("kite", "North", "Orbit", 12)])
    assert [(s.size, s.owned is not None) for s in plan.slots] == [(12, True), (8, False)]
    assert "slightly underpowered between 12 and 12.5 kn" in plan.notes[0]


def test_owned_kite_outside_range_is_reported_not_used():
    plan = quiver.plan_kites(prof(80, 20, 30), [OwnedItem("kite", size=17)])
    assert all(s.owned is None for s in plan.slots)
    assert any("17 m² kite" in n and "overlaps" in n for n in plan.notes)


def test_too_light_wind_declared_uncovered():
    plan = quiver.plan_kites(prof(100, 5, 15), [])
    assert plan.uncovered and plan.uncovered[0][0] == 5
    assert "bigger than 17 m²" in plan.notes[0]
    assert plan.slots[0].size == 17


def test_too_strong_wind_declared_uncovered():
    plan = quiver.plan_kites(prof(35, 30, 60), [])
    assert plan.uncovered and plan.uncovered[-1][1] == 60
    assert any("smallest standard kite" in n for n in plan.notes)


def test_non_kite_and_sizeless_owned_items_ignored():
    owned = [OwnedItem("board", size=138), OwnedItem("kite")]  # kite without size
    assert [s.size for s in quiver.plan_kites(prof(), owned).slots] == [13, 9]


# --- full set ---------------------------------------------------------------------------------


def by_type(rec):
    out = {}
    for i in rec.items:
        out.setdefault(i.type, []).append(i)
    return out


def test_full_set_for_new_rider():
    rec = quiver.recommend_set(prof(), [])
    t = by_type(rec)
    assert [k.size for k in t["kite"]] == [13, 9]
    assert t["kite"][0].wind_min_kn == 11.5 and t["kite"][0].wind_max_kn == 17.5
    assert t["board"][0].size == 140 and t["board"][0].subtype == "twintip"
    assert t["bar"][0].unit == "cm" and "adjustable" in t["bar"][0].reason
    assert t["harness"][0].subtype == "M/L"
    assert "foil" not in t
    assert rec.kind == "set"


def test_owned_gear_removes_items():
    owned = [
        OwnedItem("kite", size=13),
        OwnedItem("kite", size=9),
        OwnedItem("board", size=139, subtype="twintip"),
        OwnedItem("bar"),
        OwnedItem("harness"),
    ]
    rec = quiver.recommend_set(prof(), owned)
    assert rec.items == []
    assert "already cover the whole wind range" in rec.explanation
    assert "Your 139 cm board fits" in rec.explanation
    assert "already own a bar" in rec.explanation


def test_wrong_size_board_is_flagged():
    rec = quiver.recommend_set(prof(), [OwnedItem("board", size=128)])
    board = by_type(rec)["board"][0]
    assert "your 128 cm board is on the small side" in board.reason


def test_surfboard_does_not_count_for_twintip_rider():
    rec = quiver.recommend_set(prof(), [OwnedItem("board", size=140, subtype="surfboard")])
    assert by_type(rec)["board"][0].subtype == "twintip"


def test_foil_rider_gets_foil_board_and_wing():
    rec = quiver.recommend_set(prof(80, 10, 20, style="foil"), [])
    t = by_type(rec)
    assert t["board"][0].subtype == "foilboard" and t["board"][0].unit == "L"
    assert t["foil"][0].subtype == "complete" and t["foil"][0].unit == "cm²"
    assert t["foil"][0].reason.startswith("complete foil (mast, fuselage, wings)")
    assert max(k.size for k in t["kite"]) <= 12  # foil kites are much smaller


def test_surf_rider_board():
    rec = quiver.recommend_set(prof(style="surfboard"), [])
    assert by_type(rec)["board"][0].subtype == "surfboard"


def test_set_explanation_full_text():
    rec = quiver.recommend_set(prof(), [OwnedItem("kite", "North", "Orbit", 12)])
    assert rec.explanation == (
        "Set for 80 kg, 12–25 kn, twintip, intermediate.\n"
        "Option: minimum — fewest kites.\n"
        "Kite quiver: 12 m² (12.5–19 kn) [owned], 8 m² (18.5–28.5 kn).\n"
        "Your 12 m² kite is slightly underpowered between 12 and 12.5 kn.\n"
        "Using your North Orbit 12 m² kite for 12.5–19 kn."
    )


def test_invalid_profile_rejected():
    with pytest.raises(ValidationError):
        quiver.recommend_set(prof(80, 25, 12), [])


# --- single item ------------------------------------------------------------------------------


def test_single_kite_for_given_wind():
    rec = quiver.recommend_single(prof(), "kite", (18, 24))
    (item,) = rec.items
    assert item.size == 9 and rec.kind == "single"
    assert item.wind_min_kn <= 18 and item.wind_max_kn >= 24  # covers the whole request
    assert "can't cover" not in item.reason


@pytest.mark.parametrize("weight", [55, 70, 85, 100])
@pytest.mark.parametrize("lo, hi", [(12, 16), (15, 20), (18, 24), (22, 30)])
def test_single_kite_covers_narrow_range_fully(weight, lo, hi):
    (item,) = quiver.recommend_single(prof(weight, 10, 35), "kite", (lo, hi)).items
    possible = any(
        a <= lo and b >= hi
        for a, b in (engine.kite_wind_range(sz, weight) for sz in engine.STANDARD_KITE_SIZES)
    )
    if possible:
        assert item.wind_min_kn <= lo and item.wind_max_kn >= hi
        assert "can't cover" not in item.reason
    else:
        assert "can't cover" in item.reason


def test_single_kite_wide_range_says_so():
    (item,) = quiver.recommend_single(prof(), "kite", (10, 35)).items
    assert "one kite can't cover the whole range" in item.reason


def test_single_items_each_type():
    p = prof()
    assert quiver.recommend_single(p, "board").items[0].size == 140
    assert quiver.recommend_single(p, "harness").items[0].subtype == "M/L"
    assert quiver.recommend_single(p, "bar").items[0].unit == "cm"
    assert quiver.recommend_single(p, "foil").items[0].unit == "cm²"
    assert quiver.recommend_single(p, "kite").items[0].size == 9  # most of 12–25 kn


def test_single_unknown_type():
    with pytest.raises(ValueError):
        quiver.recommend_single(prof(), "wetsuit")


# --- regressions from the step-2 self-review ---------------------------------------------------


def test_no_kite_possible_is_not_called_covered():
    rec = quiver.recommend_set(prof(100, 6, 9), [])
    assert "Kite quiver: none." in rec.explanation
    assert "No standard kite works in this wind range." in rec.explanation
    assert "already cover" not in rec.explanation
    assert quiver.plan_kites(prof(100, 6, 9), []).uncovered == [(6, 9)]


def test_partly_uncovered_range_is_not_called_covered():
    rec = quiver.recommend_set(prof(100, 5, 15), [OwnedItem("kite", size=17)])
    assert "already cover" not in rec.explanation
    assert "bigger than 17 m²" in rec.explanation


def test_owned_kite_bigger_than_standard_extends_low_end():
    plan = quiver.plan_kites(prof(80, 8, 20), [OwnedItem("kite", size=21)])
    assert plan.slots[0].owned is not None and plan.slots[0].size == 21
    assert not any("bigger than" in n for n in plan.notes)
    assert coverage_gaps(plan, 8, 20) == []


@pytest.mark.parametrize("wind", [(0, 0), (24, 18), (3, 10), (20, 70)])
def test_single_rejects_bad_wind_override(wind):
    with pytest.raises(ValidationError, match="wind range"):
        quiver.recommend_single(prof(), "kite", wind)


# --- step 2b: gusty bias and quiver variants ---------------------------------------------------


@pytest.mark.parametrize("weight, lo, hi, style, skill", GRID)
def test_gusty_never_bigger_kites(weight, lo, hi, style, skill):
    calm = prof(weight, lo, hi, style=style, skill=skill)
    gusty = prof(weight, lo, hi, style=style, skill=skill, gusty=True)
    for w in range(int(lo), int(hi) + 1):
        assert engine.kite_size_for(weight, w, style, skill, True) <= engine.kite_size_for(
            weight, w, style, skill
        )
    assert coverage_gaps(quiver.plan_kites(gusty, []), lo, hi) == []
    # the biggest kite of a gusty quiver is never bigger than the calm one
    assert max(s.size for s in quiver.plan_kites(gusty, []).slots) <= max(
        s.size for s in quiver.plan_kites(calm, []).slots
    )


def test_gusty_note_and_smaller_sizes():
    rec = quiver.recommend_set(prof(gusty=True), [])
    assert "Gusty spots: kite sizes biased ~7% smaller." in rec.explanation
    assert engine.ideal_kite_size(80, 20, gusty=True) == pytest.approx(8.8 * 0.93)


def comfort_margin(plan, p):
    """Worst case over the wind range of how far inside its best kite's normal range it sits."""
    worst, w = 99.0, p.wind_min_kn
    while w <= p.wind_max_kn:
        if not any(a <= w <= b for a, b in plan.uncovered):
            best = max(
                min(w - lo, hi - w)
                for lo, hi in (
                    engine.kite_wind_range(s.size, p.weight_kg, p.style, p.skill, p.gusty)
                    for s in plan.slots
                )
            )
            worst = min(worst, best)
        w += 0.5
    return worst


@pytest.mark.parametrize("weight, lo, hi, style, skill", GRID)
def test_comfortable_is_one_more_kite_with_more_margin(weight, lo, hi, style, skill):
    p = prof(weight, lo, hi, style=style, skill=skill)
    minimum = quiver.plan_kites(p, [], "minimum")
    comfy = quiver.plan_kites(p, [], "comfortable")
    assert coverage_gaps(comfy, lo, hi) == []
    assert comfy.uncovered == minimum.uncovered
    if [s.size for s in comfy.slots] == [s.size for s in minimum.slots]:
        return  # no better quiver exists; the CLI then says the options are the same
    assert len(comfy.slots) == len(minimum.slots) + 1
    assert comfort_margin(comfy, p) >= comfort_margin(minimum, p)


def test_comfortable_falls_back_when_nothing_better():
    p = prof(110, 8, 15, skill="beginner")
    same = [s.size for s in quiver.plan_kites(p, [], "comfortable").slots]
    assert same == [s.size for s in quiver.plan_kites(p, [], "minimum").slots]


def test_variant_recorded_and_explained():
    rec = quiver.recommend_set(prof(), [], "comfortable")
    assert rec.variant == "comfortable"
    assert "Option: comfortable — more overlap between kites." in rec.explanation
    assert "Kite quiver (sweet spots): 13 m²" in rec.explanation
    assert all(i.reason.startswith("sweet spot ") for i in rec.items if i.type == "kite")
    assert [i.size for i in rec.items if i.type == "kite"] == [13, 10, 8]


def test_unknown_variant_rejected():
    with pytest.raises(ValidationError, match="option"):
        quiver.plan_kites(prof(), [], "luxury")


# --- one-kite quiver ----------------------------------------------------------------------------


@pytest.mark.parametrize("weight, lo, hi, style, skill", GRID)
def test_one_kite_leans_to_light_wind(weight, lo, hi, style, skill):
    """Your choice (#6): sized for the lower part of the range, never smaller than the kite
    that covers the most of it."""
    p = prof(weight, lo, hi, style=style, skill=skill)
    plan = quiver.plan_kites(p, [], "one_kite")
    assert len(plan.slots) == 1
    (slot,) = plan.slots

    def overlap(size):
        a, b = engine.kite_wind_range(size, weight, style, skill)
        return min(b, hi) - max(a, lo)

    widest = max(engine.STANDARD_KITE_SIZES, key=lambda s: (overlap(s), s))
    light = engine.kite_size_for(weight, lo + quiver.LIGHT_WIND_POINT * (hi - lo), style, skill)
    assert slot.size == max(light, quiver.best_single_kite(weight, lo, hi, style, skill))
    assert slot.size >= min(widest, quiver.best_single_kite(weight, lo, hi, style, skill))
    assert coverage_gaps(plan, lo, hi) == []  # every wind is covered or declared uncovered


def test_one_kite_notes_under_and_overpowered():
    plan = quiver.plan_kites(prof(80, 10, 35), [], "one_kite")
    assert plan.slots[0].size == 11  # sized for the light part of 10–35 kn
    assert plan.notes == [
        "One kite: underpowered below 13.5 kn.",
        "One kite: overpowered above 21 kn.",
    ]
    assert plan.uncovered == [(10, 13.5), (21.0, 35)]


def test_one_kite_narrow_range_fully_covered():
    plan = quiver.plan_kites(prof(80, 15, 20), [], "one_kite")
    assert (plan.slots[0].size, plan.uncovered, plan.notes) == (11, [], [])


def test_one_kite_prefers_owned_kite_when_almost_as_good():
    plan = quiver.plan_kites(prof(), [OwnedItem("kite", size=10)], "one_kite")
    assert plan.slots[0].owned is not None and plan.slots[0].size == 10
    rec = quiver.recommend_set(prof(), [OwnedItem("kite", size=10)], "one_kite")
    assert not any(i.type == "kite" for i in rec.items)  # nothing to buy kite-wise


def test_one_kite_ignores_poor_owned_kite():
    plan = quiver.plan_kites(prof(80, 20, 30), [OwnedItem("kite", size=17)], "one_kite")
    assert plan.slots[0].owned is None and plan.slots[0].size == 8


def test_one_kite_explanation():
    rec = quiver.recommend_set(prof(), [], "one_kite")
    assert "Option: one kite — simplest and cheapest." in rec.explanation
    assert [i.size for i in rec.items if i.type == "kite"] == [12]
    assert "bar for 12 m²" in next(i.reason for i in rec.items if i.type == "bar")


@pytest.mark.parametrize(
    "owned, expected",
    [
        ([], ["complete"]),
        ([OwnedItem("foil", subtype="complete")], []),
        ([OwnedItem("foil")], []),  # a foil without subtype counts as complete
        ([OwnedItem("foil", subtype="mast")], ["front_wing"]),
        ([OwnedItem("foil", subtype="front_wing")], ["mast"]),
        ([OwnedItem("foil", subtype="mast"), OwnedItem("foil", subtype="front_wing")], []),
    ],
)
def test_foil_parts_needed(owned, expected):
    rec = quiver.recommend_set(prof(80, 10, 20, style="foil"), owned)
    assert [i.subtype for i in rec.items if i.type == "foil"] == expected
