"""Load the labelled post corpus and score an extraction against it."""

from pathlib import Path

import yaml

CORPUS = yaml.safe_load(
    (Path(__file__).parent / "fixtures" / "posts.yaml").read_text(encoding="utf-8")
)
FIELDS = ("type", "subtype", "size", "size_label", "brand", "year", "price", "is_new", "sold")
SIZE_TOL = {"kite": 0.5, "board": 2}


def listing_value(listing, name):
    return listing.price_ils if name == "price" else getattr(listing, name)


def field_ok(name, expected, got, item_type):
    if name == "size" and expected is not None and got is not None:
        return abs(expected - got) <= SIZE_TOL.get(item_type, 0.5)
    return expected == got


def score(post, result):
    """Returns a list of (field, ok, detail) checks for one post."""
    exp = post["expect"]
    checks = [("is_sale", (result.status == "listing") == exp["is_sale"], result.status)]
    if not exp["is_sale"]:
        return checks
    if "location" in exp:
        got = result.listings[0].location if result.listings else ""
        checks.append(("location", got == exp["location"], got))
    if "bundle_price" in exp:
        checks.append(
            (
                "bundle_price",
                result.bundle_price_ils == exp["bundle_price"],
                result.bundle_price_ils,
            )
        )
    items = exp.get("items", [])
    checks.append(("item_count", len(result.listings) == len(items), len(result.listings)))
    for i, want in enumerate(items):
        got = result.listings[i] if i < len(result.listings) else None
        for name in FIELDS:
            if name not in want:
                continue
            value = listing_value(got, name) if got else "<missing>"
            checks.append(
                (name, got is not None and field_ok(name, want[name], value, want["type"]), value)
            )
    return checks
