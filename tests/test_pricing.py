import pytest

from kitefinder import pricing
from kitefinder.models import QUIVER_VARIANTS, OwnedItem, Profile, RecItem
from kitefinder.sizing import engine, quiver

PRICES = engine.load_reference("prices_il")


def test_price_table_well_formed():
    assert isinstance(PRICES["verified"], bool)
    for key in ("bar", "harness"):
        assert 0 < PRICES[key]["used"] < PRICES[key]["new"]
    for table in (PRICES["board"], PRICES["foil"]):
        for sub, p in table.items():
            assert 0 < p["used"] < p["new"], sub
    assert 0 < PRICES["kite"]["used_factor"] < 1


@pytest.mark.parametrize(
    "item, new, used",
    [
        (RecItem("kite", 9), 6200, 2800),
        (RecItem("kite", 12), 7100, 3200),
        (RecItem("kite", 17), 8600, 3900),
        (RecItem("bar", 52), 2800, 1300),
        (RecItem("harness", None, subtype="M/L"), 1600, 700),
        (RecItem("board", 140, subtype="twintip"), 3000, 1400),
        (RecItem("board", 100, subtype="foilboard"), 3800, 2000),
        (RecItem("foil", 1500, subtype="front_wing"), 2500, 1300),
        (RecItem("board", 140), 3000, 1400),  # no subtype: first row (twintip)
    ],
)
def test_estimates(item, new, used):
    assert pricing.estimate_item(item, "new") == new
    assert pricing.estimate_item(item, "used") == used


def test_bigger_kite_costs_more_and_everything_rounded():
    prices = [pricing.estimate_item(RecItem("kite", s), "used") for s in engine.STANDARD_KITE_SIZES]
    assert prices == sorted(prices)
    assert all(p % 100 == 0 for p in prices)


def test_estimate_errors():
    with pytest.raises(ValueError, match="new or used"):
        pricing.estimate_item(RecItem("kite", 9), "refurbished")
    with pytest.raises(ValueError, match="no price estimate"):
        pricing.estimate_item(RecItem("wetsuit", None), "used")


def test_price_recommendation_and_total():
    rec = quiver.recommend_set(Profile(80, 86, 12, 25), [], "minimum")
    pricing.price_recommendation(rec, "used")
    assert rec.price_condition == "used"
    assert all(i.est_price_ils for i in rec.items)
    assert pricing.total(rec) == 9500


def test_owned_gear_costs_nothing():
    owned = [OwnedItem("kite", size=13), OwnedItem("kite", size=9), OwnedItem("bar")]
    rec = quiver.recommend_set(Profile(80, 86, 12, 25), owned, "minimum")
    pricing.price_recommendation(rec, "used")
    assert {i.type for i in rec.items} == {"board", "harness"}
    assert pricing.total(rec) == 1400 + 700


@pytest.mark.parametrize(
    "pref, expected", [("new", ("new",)), ("used", ("used",)), ("both", ("new", "used"))]
)
def test_conditions_for(pref, expected):
    assert pricing.conditions_for(pref) == expected


# --- best within budget -----------------------------------------------------------------------


def build_for(profile, owned=()):
    return lambda v: quiver.recommend_set(profile, list(owned), v)


@pytest.mark.parametrize(
    "budget, pref, variant, condition, fits",
    [
        (30000, "both", "comfortable", "new", True),
        (
            21000,
            "both",
            "minimum",
            "new",
            True,
        ),  # your choice (#5): new gear before a fuller quiver
        (15000, "both", "one_kite", "new", True),  # one new kite beats a used comfortable quiver
        (21000, "new", "minimum", "new", True),
        (13000, "both", "comfortable", "used", True),  # nothing new fits: the fullest used
        (9500, "both", "minimum", "used", True),
        (9499, "both", "one_kite", "used", True),
        (6600, "used", "one_kite", "used", True),
        (6599, "used", "one_kite", "used", False),
        (20000, "new", "one_kite", "new", True),
        (1000, "new", "one_kite", "new", False),
    ],
)
def test_best_within_budget(budget, pref, variant, condition, fits):
    rec, ok = pricing.best_within_budget(build_for(Profile(80, 86, 12, 25)), budget, pref)
    assert (rec.variant, rec.price_condition, ok) == (variant, condition, fits)
    assert rec.budget_ils == budget
    assert (pricing.total(rec) <= budget) == fits


@pytest.mark.parametrize("weight", [55, 70, 85, 100])
@pytest.mark.parametrize("budget", [3000, 6000, 9000, 12000, 20000, 30000])
@pytest.mark.parametrize("pref", ["new", "used", "both"])
def test_budget_pick_is_the_best_affordable(weight, budget, pref):
    """No better (variant, condition) pair that also fits the budget was skipped."""
    p = Profile(weight, 86, 12, 25)
    build = build_for(p)
    rec, ok = pricing.best_within_budget(build, budget, pref)
    order = [(v, c) for c in pricing.conditions_for(pref) for v in QUIVER_VARIANTS]  # new first
    picked = order.index((rec.variant, rec.price_condition))
    for v, c in order[: picked if ok else len(order)]:
        other = pricing.price_recommendation(build(v), c)
        assert pricing.total(other) > budget, (v, c)
    if not ok:  # nothing fits: we return the cheapest complete set
        cheapest = min(pricing.total(pricing.price_recommendation(build(v), c)) for v, c in order)
        assert pricing.total(rec) == cheapest


def test_foil_set_priced_as_complete_foil():
    rec = quiver.recommend_set(Profile(80, 86, 10, 20, style="foil"), [], "minimum")
    pricing.price_recommendation(rec, "used")
    foil = next(i for i in rec.items if i.type == "foil")
    assert foil.est_price_ils == 2800  # complete foil, not just a front wing
