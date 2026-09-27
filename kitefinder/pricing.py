"""Price estimates for recommended items, and picking the best set that fits a budget."""

from __future__ import annotations

from collections.abc import Callable

from .models import QUIVER_VARIANTS, RecItem, Recommendation
from .sizing.engine import load_reference

PRICE_CONDITIONS = ("new", "used")


def _round100(x: float) -> int:
    return int(round(x / 100.0)) * 100


def estimate_item(item: RecItem, condition: str) -> int:
    """Typical ₪ price of one recommended item, new or used."""
    if condition not in PRICE_CONDITIONS:
        raise ValueError(f"condition must be new or used, not {condition!r}")
    prices = load_reference("prices_il")
    if item.type == "kite":
        k = prices["kite"]
        new = k["new_base"] + k["new_per_m2"] * (item.size or 0)
        return _round100(new if condition == "new" else new * k["used_factor"])
    table = prices.get(item.type)
    if table is None:
        raise ValueError(f"no price estimate for {item.type!r}")
    if "new" not in table:  # keyed by subtype
        table = table.get(item.subtype) or next(iter(table.values()))
    return _round100(table[condition])


def price_recommendation(rec: Recommendation, condition: str) -> Recommendation:
    """Fill est_price_ils on every item (in place) and return the recommendation."""
    rec.price_condition = condition
    for item in rec.items:
        item.est_price_ils = estimate_item(item, condition)
    return rec


def total(rec: Recommendation) -> int:
    return sum(i.est_price_ils or 0 for i in rec.items)


def conditions_for(pref: str) -> tuple[str, ...]:
    """Which conditions to consider, best first: new is preferred when affordable."""
    return {"new": ("new",), "used": ("used",), "both": ("new", "used")}[pref]


def best_within_budget(
    build: Callable[[str], Recommendation], budget: int, condition_pref: str = "both"
) -> tuple[Recommendation, bool]:
    """The newest gear that fits the budget (your choice, #5): every variant bought new is
    tried before any used one; within the same condition, comfortable > minimum > one kite.

    `build(variant)` makes a fresh recommendation. Returns (recommendation, fits). When nothing
    fits, returns the cheapest rideable set (fits=False) so the caller can say how far over it
    is — never a partial set, since a kite without a bar, harness or board can't be ridden.
    """
    cheapest: Recommendation | None = None
    for condition in conditions_for(condition_pref):  # new first
        for variant in QUIVER_VARIANTS:
            rec = price_recommendation(build(variant), condition)
            rec.budget_ils = budget
            if total(rec) <= budget:
                return rec, True
            if cheapest is None or total(rec) < total(cheapest):
                cheapest = rec
    return cheapest, False
