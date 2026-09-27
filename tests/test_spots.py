import pytest

from kitefinder.models import ValidationError
from kitefinder.sizing import spots

SPOTS, REGIONS = spots.load_spots()


# --- reference file is well formed ------------------------------------------------------------


def test_spots_file_well_formed():
    assert len(SPOTS) >= 15
    names = [s.name for s in SPOTS]
    assert len(names) == len(set(names))
    for s in SPOTS:
        assert s.region in REGIONS, s.name
        assert s.water in ("flat", "waves"), s.name
        for lo, hi in (s.summer, s.winter):
            assert 4 <= lo < hi <= 45, s.name
        assert any("א" <= ch <= "ת" for a in s.aliases for ch in a), f"{s.name}: Hebrew"


def test_no_alias_points_to_two_spots():
    seen = {}
    for s in SPOTS:
        for key in (s.name, *s.aliases):
            k = spots.normalize_name(key)
            assert seen.setdefault(k, s.name) == s.name, f"'{key}' used by {seen[k]} and {s.name}"


def test_region_words_do_not_shadow_spot_aliases():
    """A word may be both only when it means the same thing (a one-spot region like Kinneret)."""
    owner = {spots.normalize_name(k): s for s in SPOTS for k in (s.name, *s.aliases)}
    for region, words in REGIONS.items():
        members = [s for s in SPOTS if s.region == region]
        for w in words:
            spot = owner.get(spots.normalize_name(w))
            assert spot is None or members == [spot], f"'{w}' is region {region} and {spot.name}"


def test_every_region_has_spots():
    for region in REGIONS:
        assert any(s.region == region for s in SPOTS), region
    assert set(REGIONS) == set(spots.REGION_LABELS)


# --- resolving names --------------------------------------------------------------------------


@pytest.mark.parametrize("spot", SPOTS, ids=lambda s: s.name)
def test_every_name_and_alias_resolves(spot):
    for key in (spot.name, *spot.aliases):
        assert spots.resolve_areas([key]).spots == [spot], key


@pytest.mark.parametrize(
    "typed, expected",
    [
        ("בת גלים", "Bat Galim"),
        ("  בת   גלים ", "Bat Galim"),
        ("בַּת גַּלִּים", "Bat Galim"),  # with niqqud
        ("BAT-GALIM", "Bat Galim"),
        ("קרית ים", "Kiryat Yam"),
        ("קריית-ים", "Kiryat Yam"),
        ('ת"א', "Tel Aviv"),
        ("ת״א", "Tel Aviv"),  # gershayim
        ("Caesarea", "Sdot Yam / Caesarea"),
        ("tlv", "Tel Aviv"),
    ],
)
def test_hebrew_and_spelling_variants(typed, expected):
    assert spots.resolve_areas([typed]).spot_names == [expected]


def test_region_expands_to_all_its_spots():
    north = spots.resolve_areas(["צפון"])
    assert {s.region for s in north.spots} == {"north"}
    assert len(north.spots) == sum(1 for s in SPOTS if s.region == "north")
    assert spots.resolve_areas(["eilat"]).spot_names == ["Eilat North Beach"]
    assert spots.resolve_areas(["כנרת"]).spot_names == ["Kinneret"]


def test_union_of_ranges_and_no_duplicates():
    got = spots.resolve_areas(["Bat Galim", "בת גלים", "Eilat", "north"], season="summer")
    assert got.spot_names.count("Bat Galim") == 1
    ranges = [s.summer for s in got.spots]
    assert got.wind_min_kn == min(lo for lo, _ in ranges)
    assert got.wind_max_kn == max(hi for _, hi in ranges)


@pytest.mark.parametrize(
    "season, expected", [("summer", (10, 18)), ("winter", (18, 35)), ("all", (10, 35))]
)
def test_seasons(season, expected):
    got = spots.resolve_areas(["Bat Galim"], season)
    assert (got.wind_min_kn, got.wind_max_kn) == expected
    assert got.season == season


def test_gusty_if_any_spot_is_gusty():
    assert spots.resolve_areas(["Bat Galim"]).gusty is False
    assert spots.resolve_areas(["Bat Galim", "Tel Aviv"]).gusty is True
    assert spots.resolve_areas(["Kinneret"]).gusty is True


@pytest.mark.parametrize(
    "typed, hint",
    [("bat galm", "did you mean Bat Galim?"), ("kinnert", "did you mean Kinneret?")],
)
def test_unknown_area_suggests(typed, hint):
    with pytest.raises(ValidationError, match=hint):
        spots.resolve_areas([typed])


def test_unknown_area_without_suggestion_and_all_problems_listed():
    with pytest.raises(ValidationError) as e:
        spots.resolve_areas(["zzzz", "qqqq", "Bat Galim"])
    msg = str(e.value)
    assert "Unknown area 'zzzz'" in msg and "Unknown area 'qqqq'" in msg
    assert "did you mean" not in msg and "kitefinder areas" in msg


def test_bad_season_and_empty():
    with pytest.raises(ValidationError, match="season"):
        spots.resolve_areas(["Bat Galim"], "spring")
    with pytest.raises(ValidationError, match="at least one area"):
        spots.resolve_areas([])


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Bat Galim, Sdot Yam", ["Bat Galim", "Sdot Yam"]),
        ("בת גלים; אילת", ["בת גלים", "אילת"]),
        (" , north ,, ", ["north"]),
    ],
)
def test_split_areas(text, expected):
    assert spots.split_areas(text) == expected


def test_format_areas_lists_everything():
    text = spots.format_areas()
    for s in SPOTS:
        assert s.name in text
    assert text.splitlines()[0] == "North (צפון):"
    assert "  Bat Galim (בת גלים) — summer 10–18 kn, winter 18–35 kn, waves" in text
    assert "  Dor / Habonim (דור) — summer 10–18 kn, winter 15–30 kn, flat" in text
    assert "Tel Aviv (תל אביב) — summer 10–16 kn, winter 15–30 kn, waves, gusty" in text
    assert text.splitlines()[-1].startswith("Type spot names or a region")
