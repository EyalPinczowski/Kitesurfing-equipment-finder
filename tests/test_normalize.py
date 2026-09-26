import pytest

from kitefinder.llm import normalize as nz


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("kite", ("kite", "")), ("קייט 12", ("kite", "")), ("הקייט", ("kite", "")),
        ("קייטים", ("kite", "")), ("עפיפון", ("kite", "")), ("הבר", ("bar", "")),
        ("חבר שלי", ("other", "")), ("בריכה", ("other", "")), ("foil board", ("board", "foilboard")),
        ("גלשן פויל", ("board", "foilboard")), ("kiteboard", ("board", "")),
        ("טווין טיפ", ("board", "twintip")), ("twin-tip", ("board", "twintip")),
        ("והטרפז", ("harness", "")), ("סרפבורד", ("board", "surfboard")), ("תורן", ("foil", "mast")),
        ("כנף קדמית", ("foil", "front_wing")), ("wetsuit", ("wetsuit", "")), ("משאבה", ("other", "")),
        ("", ("other", "")), (None, ("other", "")),
    ],
)  # fmt: skip
def test_normalize_type(raw, expected):
    assert nz.normalize_type(raw) == expected


@pytest.mark.parametrize(
    "t, raw, expected",
    [("board", "twin tip", "twintip"), ("board", "Surf", "surfboard"), ("board", "foil-board", "foilboard"),
     ("foil", "wing", "front_wing"), ("foil", "full", "complete"), ("kite", "twintip", ""), ("board", None, "")],
)  # fmt: skip
def test_normalize_subtype(t, raw, expected):
    assert nz.normalize_subtype(t, raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("₪3,200", 3200), ('3200 ש"ח', 3200), ("3.2k", 3200), ("3,200₪", 3200), ("מחיר 3200", 3200),
     ("2 אלף", 2000), ("5.9K", 5900), ("1,250.50", 1250), (3200, 3200), (3199.6, 3200),
     ("abc", None), ("", None), (0, None), (-5, None), (None, None), (True, None)],
)  # fmt: skip
def test_parse_price(raw, expected):
    assert nz.parse_price(raw) == expected


@pytest.mark.parametrize(
    "raw, t, expected",
    [("12 מטר", "kite", 12), ("12m²", "kite", 12), ("9,5", "kite", 9.5), ("10.5", "kite", 10.5),
     ("138x41", "board", 138), ("138 ס\"מ", "board", 138), ("5'4\"", "board", 163), ("5'10", "board", 178),
     ("5'", "board", 152), (12, "kite", 12), (0, "kite", None), ("big", "kite", None), (None, "kite", None)],
)  # fmt: skip
def test_parse_size(raw, t, expected):
    assert nz.parse_size(raw, t) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("2021", 2021), ("'21", 2021), ("model 2019", 2019), (2020, 2020), (21, 2021),
     (1990, None), (2099, None), ("x", None), (None, None)],
)  # fmt: skip
def test_parse_year(raw, expected):
    assert nz.parse_year(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("m", "M"), ("מידה L", "L"), ("S/M", "S/M"), ("xl", "XL"), ("", ""), (None, "")],
)
def test_size_label(raw, expected):
    assert nz.normalize_size_label(raw) == expected


def test_sanity_ranges():
    assert nz.sane_size("kite", "", 12) == 12
    assert nz.sane_size("kite", "", 52) is None  # a bar width misread as a kite size
    assert nz.sane_size("board", "twintip", 2021) is None
    assert nz.sane_size("bar", "", 50) == 50
    assert nz.sane_size("wetsuit", "", 50) == 50  # no range known: keep
    assert nz.sane_price(3200) == 3200
    assert nz.sane_price(10) is None and nz.sane_price(521234567) is None
    assert nz.sane_size("kite", "", None) is None and nz.sane_price(None) is None


def test_numbers_in_text():
    nums = nz.numbers_in_text("קייט 12 ב-3.2k, גלשן 5'4\" מודל '21, 3,200, model 19")
    assert {12, 3200, 163, 2021, 2019} <= nums
    assert nz.in_text(12, nums) and nz.in_text(None, nums) and not nz.in_text(99, nums)


@pytest.mark.parametrize(
    "text, city",
    [("איסוף מהרצליה או ת\"א", "Herzliya"), ("pickup in Haifa", "Haifa"), ("בתל אביב", "Tel Aviv"),
     ("קרית ים", "Kiryat Yam"), ("nothing", "")],
)  # fmt: skip
def test_find_city(text, city):
    assert nz.find_city(text) == city


def test_normalize_location_keeps_unknown_places():
    assert nz.normalize_location("Kfar Vitkin") == "Kfar Vitkin"
    assert nz.normalize_location("  חיפה ") == "Haifa"
    assert nz.normalize_location(None) == ""


def test_find_types_prefers_longest_phrase():
    found = nz.find_types("foil board and a kite")
    assert [(t, s) for _, t, s in found] == [("board", "foilboard"), ("kite", "")]
