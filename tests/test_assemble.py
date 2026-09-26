import itertools
import random

import pytest

from kitefinder import assemble as asm
from kitefinder.models import Listing, Profile, RecItem, Recommendation, ValidationError
from kitefinder.sizing import quiver


def L(type, price, brand="", size=None, **kw):
    return Listing(type, price, brand, size=size, **kw)


def with_ids(listings):
    for i, listing in enumerate(listings, 1):
        listing.id = i
    return listings


KITE13 = RecItem("kite", 13, 12, 14, unit="m²")
KITE9 = RecItem("kite", 9, 8, 10, unit="m²")
BOARD = RecItem("board", 140, 138, 141, subtype="twintip", unit="cm")
BAR = RecItem("bar", 52, 50, 55, unit="cm")
HARNESS = RecItem("harness", None, subtype="M/L")


def rec_of(*items, rec_id=1, variant="minimum"):
    return Recommendation(
        profile=Profile(80, 86, 12, 25), items=list(items), id=rec_id, variant=variant
    )


# --- brand names ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, canon",
    [
        ("duotone", "Duotone"),
        ("DUOTONE", "Duotone"),
        ("דואוטון", "Duotone"),
        ("f one", "F-One"),
        ("F-ONE", "F-One"),
        ("אף וואן", "F-One"),
        ("קברינה", "Cabrinha"),
        ("Crazy Fly", "Crazyfly"),
        ("brunotti", "Brunotti"),  # unknown brand keeps its own name
        ("", ""),
        ("   ", ""),
    ],
)
def test_normalize_brand(raw, canon):
    assert asm.normalize_brand(raw) == canon


def test_brand_aliases_unique():
    seen = {}
    for canon, aliases in asm.BRAND_ALIASES.items():
        for a in (canon, *aliases):
            key = a.lower().replace(" ", "").replace("-", "").replace("'", "")
            assert seen.setdefault(key, canon) == canon, a


# --- matching ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "item, listing, ok, verified",
    [
        (KITE13, L("kite", 1, size=12), True, True),
        (KITE13, L("kite", 1, size=14), True, True),
        (KITE13, L("kite", 1, size=11), False, None),
        (KITE13, L("kite", 1), False, None),  # kite size must be stated
        (KITE13, L("bar", 1, size=12), False, None),  # wrong type
        (BOARD, L("board", 1, size=136, subtype="twintip"), True, True),  # 2 cm tolerance
        (BOARD, L("board", 1, size=135), False, None),
        (BOARD, L("board", 1, size=140, subtype="surfboard"), False, None),
        (BOARD, L("board", 1, size=140), True, True),  # subtype not stated: ok
        (BOARD, L("board", 1), False, None),
        (BAR, L("bar", 1), True, False),  # width often unstated
        (BAR, L("bar", 1, size=47), True, True),
        (BAR, L("bar", 1, size=40), False, None),
        (HARNESS, L("harness", 1, size_label="M"), True, True),
        (HARNESS, L("harness", 1, size_label="l"), True, True),
        (HARNESS, L("harness", 1, size_label="S/M"), True, True),
        (HARNESS, L("harness", 1, size_label="XL"), False, None),
        (HARNESS, L("harness", 1), True, False),
        (KITE13, L("kite", None, size=13), False, None),  # no price
        (KITE13, L("kite", 1, size=13, sold=True), False, None),
    ],
)
def test_match_rules(item, listing, ok, verified):
    m = asm.match(item, listing)
    assert (m is not None) == ok
    if ok:
        assert m.verified_size is verified


def test_match_foil():
    wing = RecItem("foil", 1500, 1300, 1800, subtype="front_wing", unit="cm²")
    complete = RecItem("foil", 1500, 1300, 1800, subtype="complete", unit="cm²")
    assert asm.match(wing, L("foil", 1, size=1500, subtype="front_wing")).verified_size
    assert asm.match(wing, L("foil", 1, size=2000, subtype="front_wing")) is None
    assert asm.match(complete, L("foil", 1, subtype="front_wing")) is None
    assert asm.match(complete, L("foil", 1, subtype="complete")).verified_size is False
    assert asm.match(RecItem("wetsuit", None), L("wetsuit", 1)) is None


@pytest.mark.parametrize(
    "pref, is_new, ok",
    [
        ("new", True, True),
        ("new", False, False),
        ("new", None, False),
        ("used", True, False),
        ("used", False, True),
        ("used", None, True),
        ("both", True, True),
        ("both", None, True),
    ],
)
def test_condition_preference(pref, is_new, ok):
    assert (asm.match(KITE13, L("kite", 1, size=13, is_new=is_new), pref) is not None) == ok


# --- mixed mode -------------------------------------------------------------------------------


def test_mixed_picks_cheapest_across_sources():
    listings = with_ids(
        [
            L("kite", 3200, "Duotone", 13, source="facebook", seller="a"),
            L("kite", 2600, "North", 13, source="yad2", seller="b"),
            L("kite", 2300, "Duotone", 9, source="kitelab.co.il", seller="c"),
            L("kite", 2500, "North", 9, source="yad2", seller="b"),
            L("board", 1300, "Cabrinha", 139, seller="d"),
            L("bar", 900, "Duotone", 52, seller="c"),
            L("harness", 600, "ION", size_label="M", seller="e"),
        ]
    )
    a = asm.assemble(rec_of(KITE13, KITE9, BOARD, BAR, HARNESS), listings)
    assert a.complete and a.total == 2600 + 2300 + 1300 + 900 + 600
    assert [m.listing.id for _, m in a.picks] == [2, 3, 5, 6, 7]
    assert a.brand_conflict
    assert any("different brands (Duotone, North)" in w for w in a.warnings)


def test_one_listing_never_fills_two_items():
    k10 = RecItem("kite", 10, 9, 11)
    k8 = RecItem("kite", 8, 7, 9)
    only = with_ids([L("kite", 2000, "North", 9)])  # fits both size ranges
    a = asm.assemble(rec_of(k10, k8), only)
    assert sum(m is not None for _, m in a.picks) == 1
    assert len(a.missing) == 1 and not a.complete


def test_reuse_conflict_resolved_optimally():
    """Greedy (cheapest per slot) would give the 9 m² to the 10 slot and leave 8 empty."""
    k10 = RecItem("kite", 10, 9, 11)
    k8 = RecItem("kite", 8, 7, 9)
    listings = with_ids(
        [L("kite", 1000, "A", 9), L("kite", 1500, "B", 10), L("kite", 9000, "C", 7)]
    )
    a = asm.assemble(rec_of(k10, k8), listings)
    assert a.complete
    assert [m.listing.size for _, m in a.picks] == [10, 9]
    assert a.total == 2500


def test_missing_items_reported():
    a = asm.assemble(rec_of(KITE13, BOARD), with_ids([L("kite", 2000, "North", 13)]))
    assert [i.type for i in a.missing] == ["board"]
    assert a.total == 2000 and not a.complete


def test_tie_break_fewer_sellers():
    listings = with_ids(
        [
            L("kite", 2000, "North", 13, seller="x"),
            L("kite", 2000, "North", 13, seller="y"),
            L("bar", 1000, "North", seller="y"),
            L("bar", 1000, "North", seller="z"),
        ]
    )
    a = asm.assemble(rec_of(KITE13, BAR), listings)
    assert a.sellers == {"y"}  # same price, one pickup instead of two


def test_verified_size_preferred_on_equal_price():
    listings = with_ids([L("bar", 1000, "North"), L("bar", 1000, "North", 52)])
    a = asm.assemble(rec_of(BAR), listings)
    assert a.picks[0][1].verified_size
    assert a.warnings == []


def test_unverified_warning():
    a = asm.assemble(rec_of(BAR), with_ids([L("bar", 1000, "North")]))
    assert a.warnings == ["Size not stated for: bar — ask the seller."]


@pytest.mark.parametrize("seed", range(40))
def test_mixed_is_optimal_against_brute_force(seed):
    rnd = random.Random(seed)
    items = [RecItem("kite", 12, 11, 13), RecItem("kite", 10, 9, 11), RecItem("kite", 8, 7, 9), BAR]
    listings = with_ids(
        [
            L(
                "kite",
                rnd.randrange(1000, 5000, 100),
                rnd.choice("ABC"),
                rnd.choice([7, 8, 9, 10, 11, 12, 13]),
            )
            for _ in range(rnd.randint(2, 7))
        ]
        + [
            L("bar", rnd.randrange(500, 2000, 100), rnd.choice("ABC"))
            for _ in range(rnd.randint(0, 3))
        ]
    )
    a = asm.assemble(rec_of(*items), listings)
    options = [[None, *[x for x in listings if asm.match(it, x)]] for it in items]
    best = None
    for combo in itertools.product(*options):
        chosen = [x for x in combo if x is not None]
        if len({x.id for x in chosen}) < len(chosen):
            continue
        missing_value = sum(
            asm._missing_value(it) for it, x in zip(items, combo, strict=True) if x is None
        )
        key = (len(combo) - len(chosen), missing_value, sum(x.price_ils for x in chosen))
        best = key if best is None or key < best else best
    assert a.score()[:3] == best


# --- same brand / kites and bar ---------------------------------------------------------------


BRANDED = [
    L("kite", 2600, "North", 13, seller="n"),
    L("kite", 2500, "North", 9, seller="n"),
    L("kite", 3200, "Duotone", 13, seller="d"),
    L("kite", 2300, "Duotone", 9, seller="d"),
    L("bar", 1100, "North", seller="n"),
    L("bar", 900, "Duotone", 52, seller="d"),
    L("board", 1300, "Cabrinha", 139, seller="c"),
    L("board", 1500, "North", 140, seller="n"),
    L("harness", 600, "ION", size_label="M", seller="i"),
    L("harness", 800, "North", size_label="L", seller="n"),
]


def test_same_brand_picks_cheapest_complete_brand():
    a = asm.assemble(rec_of(KITE13, KITE9, BOARD, BAR, HARNESS), with_ids(list(BRANDED)), "same")
    assert a.brand == "North" and a.complete
    assert {asm.normalize_brand(m.listing.brand) for _, m in a.picks} == {"North"}
    assert a.total == 2600 + 2500 + 1500 + 1100 + 800
    assert not a.brand_conflict


def test_same_brand_incomplete_when_no_brand_has_everything():
    listings = with_ids([x for x in BRANDED if not (x.brand == "North" and x.type == "board")])
    a = asm.assemble(rec_of(KITE13, BOARD), listings, "same")
    assert not a.complete  # no brand has both a 13 m² kite and a board now
    assert a.brand == "North"  # missing the board beats missing the (pricier) kite (Cabrinha)


def test_kites_bar_mode_frees_board_and_harness():
    a = asm.assemble(
        rec_of(KITE13, KITE9, BOARD, BAR, HARNESS), with_ids(list(BRANDED)), "kites_bar"
    )
    assert a.complete and a.brand == "North"  # 2600+2500+1100 < 3200+2300+900
    kinds = {it.type: asm.normalize_brand(m.listing.brand) for it, m in a.picks}
    assert kinds["kite"] == kinds["bar"] == "North"
    assert kinds["board"] == "Cabrinha" and kinds["harness"] == "ION"  # cheapest, any brand
    assert a.total == 2600 + 2500 + 1100 + 1300 + 600


def test_hebrew_brand_counts_as_same_brand():
    listings = with_ids([L("kite", 2000, "דואוטון", 13), L("bar", 900, "Duotone")])
    a = asm.assemble(rec_of(KITE13, BAR), listings, "same")
    assert a.complete and a.brand == "Duotone"


def test_unbranded_listings_excluded_from_brand_modes():
    a = asm.assemble(rec_of(KITE13), with_ids([L("kite", 2000, "", 13)]), "same")
    assert not a.complete and a.brand == ""


def test_bad_brand_mode():
    with pytest.raises(ValidationError, match="brands"):
        asm.assemble(rec_of(KITE13), [], "cheap")


def test_bar_brand_mismatch_warning():
    a = asm.assemble(
        rec_of(KITE13, BAR), with_ids([L("kite", 2000, "North", 13), L("bar", 900, "Duotone")])
    )
    assert a.brand_conflict
    assert a.warnings[0].startswith("Bar (Duotone) and kite (North) brands differ")


# --- best quiver under a budget from real listings --------------------------------------------


def build(owned=()):
    p = Profile(80, 86, 12, 25)
    return lambda v: quiver.recommend_set(p, list(owned), v)


MARKET = with_ids(
    [
        L("kite", 3000, "North", 13),
        L("kite", 2800, "North", 10),
        L("kite", 2600, "North", 8),
        L("kite", 2500, "North", 9),
        L("board", 1300, "Cabrinha", 140),
        L("bar", 1000, "North"),
        L("harness", 600, "ION", size_label="M"),
    ]
)


def test_best_under_picks_best_affordable_quiver():
    comfy = asm.best_assembly_under(build(), MARKET, 20000)
    assert comfy.rec.variant == "comfortable" and comfy.complete
    # cheapest: the 9 m² (₪2,500) fills the 10 m² slot (allowed 9–11) and the 8 m² its own
    assert sorted(m.listing.size for it, m in comfy.picks if it.type == "kite") == [8, 9, 13]
    assert comfy.total == 3000 + 2500 + 2600 + 1300 + 1000 + 600
    minimum = asm.best_assembly_under(build(), MARKET, 9000)
    assert minimum.rec.variant == "minimum" and minimum.total == 3000 + 2500 + 1300 + 1000 + 600
    one = asm.best_assembly_under(build(), MARKET, 6000)
    assert one.rec.variant == "one_kite" and one.total == 2500 + 1300 + 1000 + 600


def test_best_under_over_budget_returns_cheapest_complete():
    a = asm.best_assembly_under(build(), MARKET, 1000)
    assert a.complete and a.rec.variant == "one_kite" and a.total > 1000


def test_best_under_without_any_complete_set():
    a = asm.best_assembly_under(build(), with_ids([L("kite", 2000, "North", 9)]), 9000)
    assert not a.complete
    assert any(m is not None for _, m in a.picks)


def test_listing_fields_after_size_are_keyword_only():
    with pytest.raises(TypeError):
        Listing("kite", 2600, "North", "Orbit", 13, 2022)  # year must be named
    assert Listing("kite", 2600, "North", "Orbit", 13, year=2022).year == 2022


# --- regressions from the self-review ----------------------------------------------------------


def test_kites_bar_with_only_board_and_harness_to_buy():
    """You own kites and bar: the brand rule has nothing to apply to, so pick freely."""
    listings = with_ids(
        [L("board", 1300, "Cabrinha", 140), L("harness", 600, "ION", size_label="M")]
    )
    a = asm.assemble(rec_of(BOARD, HARNESS), listings, "kites_bar")
    assert a.complete and a.total == 1900 and a.brand == ""


def test_brand_mode_without_branded_listings_still_fills_free_items():
    listings = with_ids([L("kite", 2000, "", 13), L("board", 1300, "Cabrinha", 140)])
    a = asm.assemble(rec_of(KITE13, BOARD), listings, "kites_bar")
    assert [i.type for i in a.missing] == ["kite"]  # unbranded kite can't satisfy the rule
    assert a.picks[1][1].listing.brand == "Cabrinha"


@pytest.mark.parametrize("label", ["M-L", "m–l", "M - L"])
def test_hyphenated_harness_sizes(label):
    m = asm.match(HARNESS, L("harness", 1, size_label=label))
    assert m is not None and m.verified_size
