import pytest

from kitefinder.sizing import engine


@pytest.mark.parametrize(
    "raw, expected",
    [(0.5, 3), (3.4, 3), (9.4, 9), (9.5, 10), (15.9, 15), (16, 17), (16.2, 17), (30, 17)],
)
def test_round_kite_size(raw, expected):
    assert engine.round_kite_size(raw) == expected


def test_kite_size_formula():
    assert engine.ideal_kite_size(80, 20) == pytest.approx(8.8)
    assert engine.kite_size_for(80, 20) == 9


def test_zero_wind_rejected():
    with pytest.raises(ValueError):
        engine.ideal_kite_size(80, 0)


@pytest.mark.parametrize("weight", [50, 65, 80, 95, 110])
@pytest.mark.parametrize("style", ["twintip", "surfboard", "foil"])
@pytest.mark.parametrize("skill", ["beginner", "intermediate", "advanced"])
def test_more_wind_never_means_bigger_kite(weight, style, skill):
    sizes = [engine.kite_size_for(weight, w, style, skill) for w in range(8, 41)]
    assert sizes == sorted(sizes, reverse=True)


@pytest.mark.parametrize("wind", [10, 15, 20, 25, 30])
def test_heavier_rider_never_gets_smaller_kite(wind):
    sizes = [engine.kite_size_for(w, wind) for w in range(45, 121, 5)]
    assert sizes == sorted(sizes)


def test_style_and_skill_ordering():
    assert (
        engine.ideal_kite_size(80, 18, "foil")
        < engine.ideal_kite_size(80, 18, "surfboard")
        < engine.ideal_kite_size(80, 18, "twintip")
    )
    assert (
        engine.ideal_kite_size(80, 18, skill="beginner")
        < engine.ideal_kite_size(80, 18, skill="intermediate")
        < engine.ideal_kite_size(80, 18, skill="advanced")
    )


@pytest.mark.parametrize("size", engine.STANDARD_KITE_SIZES)
def test_wind_range_contains_ideal_wind(size):
    lo, hi = engine.kite_wind_range(size, 80)
    ideal = 80 * engine.K_BASE / size
    assert lo < ideal < hi


def test_bigger_kite_works_in_lighter_wind():
    ranges = [engine.kite_wind_range(s, 80) for s in engine.STANDARD_KITE_SIZES]
    lows = [lo for lo, _ in ranges]
    assert lows == sorted(lows, reverse=True)


def test_known_wind_ranges_80kg():
    assert engine.kite_wind_range(12, 80) == (12.5, 19.0)
    assert engine.kite_wind_range(9, 80) == (16.5, 25.5)


# --- boards -----------------------------------------------------------------------------------


def test_twintip_adjustments():
    base = engine.twintip_length(80)
    assert base == (138, 141, 140)
    assert engine.twintip_length(80, "beginner") == (140, 143, 142)
    assert engine.twintip_length(80, wind_min_kn=10) == (141, 144, 142)
    assert engine.twintip_length(80, "beginner", 10) == (143, 146, 144)
    assert engine.twintip_length(80, wind_min_kn=12) == base  # 12 kn is not light wind


@pytest.mark.parametrize("weight, expected", [(40, (128, 132)), (55, (128, 132)), (56, (132, 135))])
def test_twintip_boundaries(weight, expected):
    assert engine.twintip_length(weight)[:2] == expected


def test_surfboard_length():
    assert engine.surfboard_length(80) == (157, 168, 162)
    assert engine.surfboard_length(120)[:2] == (172, 185)


def test_foilboard_volume_by_skill():
    b = engine.foilboard_volume(80, "beginner")
    i = engine.foilboard_volume(80, "intermediate")
    a = engine.foilboard_volume(80, "advanced")
    assert b[2] > i[2] > a[2]
    assert b == (90, 110, 100)
    assert engine.foilboard_volume(35, "advanced")[:2] == (20, 25)  # floor for light riders


def test_front_wing_area():
    lo, hi, rec = engine.front_wing_area(80, "intermediate")
    assert (lo, hi, rec) == (1280, 1760, 1520)
    assert (
        engine.front_wing_area(80, "beginner")[2] > rec > engine.front_wing_area(80, "advanced")[2]
    )


# --- harness / bar ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "waist, expected",
    [(60, ["XS"]), (68, ["XS"]), (71, ["XS", "S"]), (75, ["S"]), (86, ["M", "L"]), (120, ["XXL"])],
)
def test_harness_sizes(waist, expected):
    assert engine.harness_sizes(waist) == expected


@pytest.mark.parametrize(
    "kite, expected", [(5, (38, 45, 42)), (9, (45, 50, 48)), (12, (50, 55, 52)), (17, (55, 60, 58))]
)
def test_bar_width(kite, expected):
    assert engine.bar_width(kite) == expected
