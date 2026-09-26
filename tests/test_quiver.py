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
    assert t["foil"][0].subtype == "front_wing" and t["foil"][0].unit == "cm²"
    assert max(k.size for k in t["kite"]) <= 12  # foil kites are much smaller


def test_surf_rider_board():
    rec = quiver.recommend_set(prof(style="surfboard"), [])
    assert by_type(rec)["board"][0].subtype == "surfboard"


def test_set_explanation_full_text():
    rec = quiver.recommend_set(prof(), [OwnedItem("kite", "North", "Orbit", 12)])
    assert rec.explanation == (
        "Set for 80 kg, 12–25 kn, twintip, intermediate.\n"
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
