"""Command-line interface (runs in Termux)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from . import pricing
from .config import load_settings
from .db import Database
from .models import (
    CONDITION_PREFS,
    EQUIPMENT_TYPES,
    QUIVER_VARIANTS,
    SEASONS,
    SKILL_LEVELS,
    STYLES,
    SUBTYPES,
    OwnedItem,
    Profile,
    RecItem,
    Recommendation,
    ValidationError,
)
from .sizing import quiver, spots


def parse_wind_range(text: str) -> tuple[float, float]:
    """Accepts '12-25', '12 - 25', '12–25' (en dash) or '12 25'."""
    cleaned = text.replace("–", "-").replace("—", "-").replace(",", " ")
    parts = [p for p in cleaned.replace("-", " ").split() if p]
    if len(parts) != 2:
        raise ValidationError(f"wind range should look like 12-25, got {text!r}")
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except ValueError as e:
        raise ValidationError(f"wind range should be numbers, got {text!r}") from e
    return lo, hi


DEFAULT_UNITS = {"kite": "m²", "foil": "cm²", "board": "cm", "bar": "cm"}


def fmt_size(item_type: str, size: float | None, unit: str | None = None) -> str:
    if size is None:
        return "?"
    unit = DEFAULT_UNITS.get(item_type, "") if unit is None else unit
    s = f"{size:g}"
    return f"{s}{unit}" if unit == "m²" else f"{s} {unit}".strip()


SEASON_LABELS = {"all": "all year", "summer": "summer", "winter": "winter"}


def _wind_origin(p: Profile) -> str:
    return f" (from your areas, {SEASON_LABELS[p.season]})" if p.wind_source == "areas" else ""


def format_profile(p: Profile) -> str:
    lines = [
        f"Weight: {p.weight_kg:g} kg",
        f"Hip/waist: {p.waist_cm:g} cm",
        f"Wind range: {p.wind_min_kn:g}–{p.wind_max_kn:g} kn{_wind_origin(p)}",
        f"Skill: {p.skill}",
        f"Style: {p.style}",
        f"Spots: {', '.join(p.spots) if p.spots else '—'}",
        f"Gusty spots: {'yes' if p.gusty else 'no'}",
        f"Budget: {f'₪{p.budget_ils:,}' if p.budget_ils is not None else '—'}",
        f"New/used: {p.condition_pref}",
        f"Travel: {f'{p.travel_km} km' if p.travel_km is not None else '—'}",
        f"Home: {p.home_location or '—'}",
    ]
    return "\n".join(lines)


def format_rec_item(item: RecItem) -> str:
    kind = f"{item.type} {item.subtype}" if item.subtype and item.type != "harness" else item.type
    if item.type == "harness":
        size = f"size {item.subtype}"
    else:
        unit = item.unit or ""
        sep = "" if unit in ("m²", "") else " "
        size = f"{item.size:g}{sep}{unit}"
        if item.size_min is not None and item.size_max is not None:
            size += f" ({item.size_min:g}–{item.size_max:g}{sep}{unit})"
    price = f" · ~{ils(item.est_price_ils)}" if item.est_price_ils is not None else ""
    return f"• {kind} {size} — {item.reason}{price}"


VARIANT_LABELS = {
    "minimum": "minimum quiver",
    "comfortable": "comfortable quiver",
    "one_kite": "one-kite quiver",
}


def ils(amount: int) -> str:
    return f"₪{amount:,}"


def budget_line(total: int, budget: int) -> str:
    if total <= budget:
        return f"✓ Fits your {ils(budget)} budget."
    return f"✗ {ils(total - budget)} over your {ils(budget)} budget."


def format_recommendation(
    rec: Recommendation, alt_new_total: int | None = None, budget: int | None = None
) -> str:
    label = f"{rec.kind}, {rec.variant}" if rec.kind == "set" else rec.kind
    head = f"Recommendation #{rec.id} ({label})" if rec.id else f"Recommendation ({label})"
    body = [head, rec.explanation]
    if rec.items:
        body.append("To look for:")
        body.extend(format_rec_item(i) for i in rec.items)
        if any(i.est_price_ils is not None for i in rec.items):
            total = pricing.total(rec)
            cost = f"Estimated cost: ~{ils(total)} {rec.price_condition}"
            if alt_new_total is not None:
                cost += f" · ~{ils(alt_new_total)} new"
            body.append(cost + " (typical Israeli prices, not live listings)")
            if budget is not None:
                body.append(budget_line(total, budget))
    else:
        body.append("Nothing to buy — your gear covers it.")
    return "\n".join(body)


def format_owned(item: OwnedItem) -> str:
    name = " ".join(x for x in (item.brand, item.model) if x) or "(no brand)"
    year = f" {item.year}" if item.year else ""
    return f"#{item.id} {item.type}: {name} {fmt_size(item.type, item.size)}{year}".rstrip()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kitefinder", description="Kitesurf gear finder (Israel)")
    sub = p.add_subparsers(dest="cmd", required=True)

    prof = sub.add_parser("profile", help="rider profile").add_subparsers(
        dest="action", required=True
    )
    ps = prof.add_parser("set", help="create or update the profile")
    ps.add_argument("--weight", type=float, help="kg")
    ps.add_argument("--waist", type=float, help="hip/waist in cm")
    ps.add_argument(
        "--wind", help="expected wind range in knots, e.g. 12-25 ('areas' = from your areas)"
    )
    ps.add_argument(
        "--areas",
        "--spots",
        dest="areas",
        help="where you ride, instead of wind speeds: 'Bat Galim, Sdot Yam', 'בת גלים', 'north'",
    )
    ps.add_argument("--season", choices=SEASONS, help="wind season for --areas (default all)")
    ps.add_argument(
        "--gusty",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="bias kite sizes smaller for gusty spots (default: from your areas)",
    )
    ps.add_argument("--skill", choices=SKILL_LEVELS)
    ps.add_argument("--style", choices=STYLES)
    ps.add_argument("--budget", type=int, help="₪")
    ps.add_argument("--condition", choices=CONDITION_PREFS)
    ps.add_argument("--travel-km", type=int)
    ps.add_argument("--home", help="home city")
    prof.add_parser("show")
    sub.add_parser("areas", help="list the surfing areas you can type instead of wind speeds")

    gear = sub.add_parser("gear", help="equipment you own").add_subparsers(
        dest="action", required=True
    )
    ga = gear.add_parser("add")
    ga.add_argument("--type", required=True, choices=EQUIPMENT_TYPES)
    ga.add_argument(
        "--subtype",
        default="",
        choices=sorted({"", *(x for v in SUBTYPES.values() for x in v)}),
        help="board: twintip/surfboard/foilboard; foil: front_wing/mast/complete",
    )
    ga.add_argument("--brand", default="")
    ga.add_argument("--model", default="")
    ga.add_argument(
        "--size",
        type=float,
        help="kite m², twintip/surfboard length cm, foilboard litres, front wing cm², bar cm",
    )
    ga.add_argument("--year", type=int)
    ga.add_argument("--notes", default="")
    gear.add_parser("list")
    gr = gear.add_parser("rm")
    gr.add_argument("id", type=int)

    sites = sub.add_parser("sites", help="websites to search").add_subparsers(
        dest="action", required=True
    )
    sa = sites.add_parser("add")
    sa.add_argument("url")
    sa.add_argument("--label", default="")
    sites.add_parser("list")
    sr = sites.add_parser("rm")
    sr.add_argument("id", type=int)

    rc = sub.add_parser("recommend", help="recommend a set (or one item) for your profile")
    rc.add_argument("--item", choices=("kite", "board", "bar", "harness", "foil"))
    rc.add_argument("--wind", help="override wind range for a single item, e.g. 18-24")
    rc.add_argument(
        "--option",
        choices=(*QUIVER_VARIANTS, "all"),
        help="minimum = fewest kites, comfortable = one more kite for more overlap, "
        "one_kite = a single kite (default: show all)",
    )
    rc.add_argument(
        "--under",
        type=int,
        nargs="?",
        const=-1,  # "--under" with no number: use the profile budget
        metavar="PRICE",
        help="the best set within this price in ₪ (no number: your profile budget)",
    )
    us = sub.add_parser("use", help="choose which saved set searches use")
    us.add_argument("id", type=int)

    hist = sub.add_parser("history", help="past recommendations")
    hist.add_argument("--limit", type=int, default=10)

    for name, status in (("fav", "favorite"), ("dismiss", "dismissed")):
        m = sub.add_parser(name, help=f"mark a listing or recommended item as {status}")
        m.add_argument("kind", choices=("listing", "rec_item"))
        m.add_argument("id", type=int)
        m.add_argument("--note", default="")
        m.set_defaults(status=status)
    um = sub.add_parser("unmark", help="remove a favorite/dismiss mark")
    um.add_argument("kind", choices=("listing", "rec_item"))
    um.add_argument("id", type=int)

    sub.add_parser("favorites", help="list favorites")

    rs = sub.add_parser("restore", help="replace the database with a backup file")
    rs.add_argument("file")

    bk = sub.add_parser("backup", help="copy the database")
    bk.add_argument("dest", nargs="?", default="/sdcard/Download")
    return p


def _profile_set(db: Database, a: argparse.Namespace) -> str:
    existing = db.get_profile()
    wind_from_areas = a.wind is not None and a.wind.strip().lower() == "areas"
    wind = parse_wind_range(a.wind) if a.wind and not wind_from_areas else None
    season = a.season or (existing.season if existing else "all")
    area_names = spots.split_areas(a.areas) if a.areas is not None else None
    if (
        area_names is None
        and existing
        and existing.spots
        and (wind_from_areas or (a.season and existing.wind_source == "areas"))
    ):
        area_names = existing.spots  # re-derive from the saved areas (new season / --wind areas)
    area = spots.resolve_areas(area_names, season) if area_names else None
    if wind_from_areas and area is None:
        raise ValidationError("--wind areas needs --areas (which spots do you ride?)")

    if existing is None:
        missing = [n for n, v in (("--weight", a.weight), ("--waist", a.waist)) if v is None]
        if wind is None and area is None:
            missing.append("--wind or --areas")
        if missing:
            raise ValidationError(f"first-time setup needs {', '.join(missing)}")
        lo, hi = wind or (area.wind_min_kn, area.wind_max_kn)
        existing = Profile(weight_kg=a.weight, waist_cm=a.waist, wind_min_kn=lo, wind_max_kn=hi)
        existing.wind_source = "manual" if wind else "areas"

    notes = []
    if a.weight is not None:
        existing.weight_kg = a.weight
    if a.waist is not None:
        existing.waist_cm = a.waist
    if wind:
        existing.wind_min_kn, existing.wind_max_kn = wind
        existing.wind_source = "manual"
    elif wind_from_areas:
        existing.wind_source = "areas"
    existing.season = season
    if area is not None:
        existing.spots = area.spot_names
        if existing.wind_source == "areas":
            existing.wind_min_kn, existing.wind_max_kn = area.wind_min_kn, area.wind_max_kn
        elif a.areas is not None and wind is None:
            notes.append(
                f"Wind range kept at {existing.wind_min_kn:g}–{existing.wind_max_kn:g} kn "
                "(set by hand). Use --wind areas to take it from your areas."
            )
    if a.areas is not None and not area_names:  # --areas "" clears them
        existing.spots = []
        if existing.wind_source == "areas":
            existing.wind_source = "manual"
            notes.append(
                f"Areas cleared; keeping {existing.wind_min_kn:g}–{existing.wind_max_kn:g} kn "
                "as your wind range."
            )
    if a.gusty is not None:
        existing.gusty = a.gusty
    elif area is not None and a.areas is not None:
        existing.gusty = area.gusty
    for attr, val in (
        ("skill", a.skill),
        ("style", a.style),
        ("budget_ils", a.budget),
        ("condition_pref", a.condition),
        ("travel_km", a.travel_km),
        ("home_location", a.home),
    ):
        if val is not None:
            setattr(existing, attr, val)
    db.save_profile(existing)
    return "\n".join(["Profile saved.", format_profile(existing), *notes])


def _same_items(a: Recommendation, b: Recommendation) -> bool:
    key = lambda r: [(i.type, i.subtype, i.size) for i in r.items]  # noqa: E731
    return key(a) == key(b)


DISPLAY_ORDER = ("minimum", "comfortable", "one_kite")


def _priced(prof: Profile, rec: Recommendation) -> tuple[Recommendation, int | None]:
    """Price in the preferred condition (used when both are fine); also the new total."""
    primary = "new" if prof.condition_pref == "new" else "used"
    pricing.price_recommendation(rec, primary)
    alt = None
    if prof.condition_pref == "both" and rec.items:
        alt = sum(pricing.estimate_item(i, "new") for i in rec.items)
    return rec, alt


def _recommend_sets(db: Database, prof: Profile, option: str | None) -> str:
    owned = db.list_owned()
    variants = DISPLAY_ORDER if option in (None, "all") else (option,)
    recs: list[Recommendation] = []
    notes = []
    for v in variants:
        rec = quiver.recommend_set(prof, owned, v)
        twin = next((r for r in recs if _same_items(r, rec)), None)
        if twin is not None:
            notes.append(
                f"The {VARIANT_LABELS[v]} is the same as the {VARIANT_LABELS[twin.variant]} "
                "for your range."
            )
            continue
        recs.append(rec)
    budget = prof.budget_ils
    parts = []
    for rec in recs:
        rec.budget_ils = budget
        _, alt = _priced(prof, rec)
        db.save_recommendation(rec)
        parts.append(format_recommendation(rec, alt, budget))
    # A fresh recommendation becomes the active set: the one asked for; with several, the best
    # one that fits the budget (else the minimum quiver).
    active = recs[0]
    if len(recs) > 1 and budget is not None:
        fitting = [r for r in recs if pricing.total(r) <= budget]
        if fitting:
            active = min(fitting, key=lambda r: QUIVER_VARIANTS.index(r.variant))
    db.set_active_recommendation(active.id)
    footer = [*notes, f"Searches will use set #{active.id} ({active.variant})."]
    if len(recs) > 1:
        footer.append("Switch with: kitefinder use <id>")
    return "\n\n".join(parts) + "\n\n" + "\n".join(footer)


def _recommend_under(db: Database, prof: Profile, budget: int) -> str:
    owned = db.list_owned()

    def build(variant: str) -> Recommendation:
        return quiver.recommend_set(prof, owned, variant)

    rec, fits = pricing.best_within_budget(build, budget, prof.condition_pref)
    total = pricing.total(rec)
    label = f"{VARIANT_LABELS[rec.variant]}, {rec.price_condition}"
    if fits:
        head = f"Best set within {ils(budget)}: {label} — ~{ils(total)}."
    else:
        head = (
            f"Nothing fits {ils(budget)}. The cheapest rideable set ({label}) is "
            f"~{ils(total)}, {ils(total - budget)} over."
        )
    db.save_recommendation(rec)
    if fits:
        db.set_active_recommendation(rec.id)
    lines = [head, "", format_recommendation(rec, budget=budget)]
    better = QUIVER_VARIANTS[: QUIVER_VARIANTS.index(rec.variant)]
    if fits and better:
        cond = pricing.conditions_for(prof.condition_pref)[-1]  # the cheaper condition
        up = [pricing.price_recommendation(build(v), cond) for v in better]
        up = [r for r in up if not _same_items(r, rec)]
        if up:
            nxt = min(up, key=pricing.total)
            extra = pricing.total(nxt) - budget
            lines.append(
                f"For {ils(extra)} more: {VARIANT_LABELS[nxt.variant]}, {cond} "
                f"(~{ils(pricing.total(nxt))})."
            )
    return "\n".join(lines)


def run(argv: Sequence[str] | None = None, db: Database | None = None) -> str:
    """Executes one command and returns its output text (easy to test)."""
    a = build_parser().parse_args(argv)
    own_db = db is None
    if db is None:
        settings = load_settings()
        db = Database(settings.db_path)
        db.seed_sites(settings.sources.get("sites") or [])
    try:
        if a.cmd == "profile":
            if a.action == "set":
                return _profile_set(db, a)
            prof = db.get_profile()
            return (
                format_profile(prof) if prof else "No profile yet. Run: kitefinder profile set ..."
            )
        if a.cmd == "gear":
            if a.action == "add":
                item = OwnedItem(a.type, a.brand, a.model, a.size, a.year, a.notes, a.subtype)
                db.add_owned(item)
                return "Added " + format_owned(item)
            if a.action == "list":
                items = db.list_owned()
                return "\n".join(map(format_owned, items)) if items else "No gear yet."
            return f"Removed #{a.id}" if db.remove_owned(a.id) else f"No gear with id {a.id}"
        if a.cmd == "sites":
            if a.action == "add":
                sid, created = db.add_site(a.url, a.label)
                return f"{'Added' if created else 'Already listed'} site #{sid}"
            if a.action == "list":
                rows = db.list_sites()
                return (
                    "\n".join(f"#{s['id']} {s['url']}" for s in rows) if rows else "No sites yet."
                )
            return f"Removed site #{a.id}" if db.remove_site(a.id) else f"No site with id {a.id}"
        if a.cmd == "recommend":
            prof = db.get_profile()
            if prof is None:
                raise ValidationError("set up your profile first: kitefinder profile set ...")
            if a.wind and not a.item:
                raise ValidationError("--wind only applies with --item")
            if a.under is not None and (a.item or a.option):
                raise ValidationError("--under picks the option itself; drop --item/--option")
            if a.item:
                wind = parse_wind_range(a.wind) if a.wind else None
                rec, alt = _priced(prof, quiver.recommend_single(prof, a.item, wind))
                db.save_recommendation(rec)
                return format_recommendation(rec, alt)
            if a.under is not None:
                budget = prof.budget_ils if a.under == -1 else a.under
                if budget is None:
                    raise ValidationError(
                        "give a price (--under 9000) or set a budget: profile set --budget 9000"
                    )
                if budget < 0:
                    raise ValidationError("the price can't be negative")
                return _recommend_under(db, prof, budget)
            return _recommend_sets(db, prof, a.option)
        if a.cmd == "use":
            db.set_active_recommendation(a.id)
            return f"Searches will use set #{a.id}."
        if a.cmd == "areas":
            return spots.format_areas()
        if a.cmd == "restore":
            safety = db.restore(a.file)
            return f"Restored {a.file}. The previous database was saved to {safety}"
        if a.cmd == "history":
            recs = db.list_recommendations(a.limit)
            if not recs:
                return "No recommendations yet."
            out = []
            for r in recs:
                parts = ", ".join(
                    f"{i.type} size {i.subtype}"
                    if i.type == "harness"
                    else f"{i.type} {fmt_size(i.type, i.size, i.unit or None)}"
                    for i in r.items
                )
                label = f"{r.kind}, {r.variant}" if r.kind == "set" else r.kind
                cost = ""
                if any(i.est_price_ils is not None for i in r.items):
                    cost = f" · ~{ils(pricing.total(r))} {r.price_condition}"
                out.append(f"#{r.id} {r.created_at} [{label}] {parts}{cost}")
            return "\n".join(out)
        if a.cmd in ("fav", "dismiss"):
            db.set_mark(a.kind, a.id, a.status, a.note)
            return f"Marked {a.kind} #{a.id} as {a.status}"
        if a.cmd == "unmark":
            return "Mark removed" if db.clear_mark(a.kind, a.id) else "Nothing to remove"
        if a.cmd == "favorites":
            marks = db.list_marks("favorite")
            return (
                "\n".join(f"{m['target_kind']} #{m['target_id']}" for m in marks)
                or "No favorites yet."
            )
        if a.cmd == "backup":
            return f"Backup written to {db.backup(a.dest)}"
        raise AssertionError(a.cmd)  # pragma: no cover - argparse enforces choices
    finally:
        if own_db:
            db.close()


def main(argv: Sequence[str] | None = None) -> int:
    try:
        print(run(argv))
    except ValidationError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    except PermissionError as e:
        hint = " (in Termux run termux-setup-storage first)" if "/sdcard" in str(e) else ""
        print(f"Error: permission denied: {e.filename}{hint}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
