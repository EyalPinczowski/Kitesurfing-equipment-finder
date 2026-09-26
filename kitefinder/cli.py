"""Command-line interface (runs in Termux)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .config import load_settings
from .db import Database
from .models import (
    CONDITION_PREFS,
    EQUIPMENT_TYPES,
    SKILL_LEVELS,
    STYLES,
    SUBTYPES,
    OwnedItem,
    Profile,
    RecItem,
    Recommendation,
    ValidationError,
)
from .sizing import quiver


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


def format_profile(p: Profile) -> str:
    lines = [
        f"Weight: {p.weight_kg:g} kg",
        f"Hip/waist: {p.waist_cm:g} cm",
        f"Wind range: {p.wind_min_kn:g}–{p.wind_max_kn:g} kn",
        f"Skill: {p.skill}",
        f"Style: {p.style}",
        f"Spots: {', '.join(p.spots) if p.spots else '—'}",
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
    return f"• {kind} {size} — {item.reason}"


def format_recommendation(rec: Recommendation) -> str:
    head = f"Recommendation #{rec.id} ({rec.kind})" if rec.id else f"Recommendation ({rec.kind})"
    body = [head, rec.explanation]
    if rec.items:
        body.append("To look for:")
        body.extend(format_rec_item(i) for i in rec.items)
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
    ps.add_argument("--wind", help="expected wind range in knots, e.g. 12-25")
    ps.add_argument("--skill", choices=SKILL_LEVELS)
    ps.add_argument("--style", choices=STYLES)
    ps.add_argument("--spots", help="comma separated, e.g. 'Bat Galim, Sdot Yam'")
    ps.add_argument("--budget", type=int, help="₪")
    ps.add_argument("--condition", choices=CONDITION_PREFS)
    ps.add_argument("--travel-km", type=int)
    ps.add_argument("--home", help="home city")
    prof.add_parser("show")

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

    bk = sub.add_parser("backup", help="copy the database")
    bk.add_argument("dest", nargs="?", default="/sdcard/Download")
    return p


def _profile_set(db: Database, a: argparse.Namespace) -> str:
    existing = db.get_profile()
    wind = parse_wind_range(a.wind) if a.wind else None
    if existing is None:
        missing = [
            n
            for n, v in (("--weight", a.weight), ("--waist", a.waist), ("--wind", wind))
            if v is None
        ]
        if missing:
            raise ValidationError(f"first-time setup needs {', '.join(missing)}")
        existing = Profile(
            weight_kg=a.weight, waist_cm=a.waist, wind_min_kn=wind[0], wind_max_kn=wind[1]
        )
    if a.weight is not None:
        existing.weight_kg = a.weight
    if a.waist is not None:
        existing.waist_cm = a.waist
    if wind:
        existing.wind_min_kn, existing.wind_max_kn = wind
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
    if a.spots is not None:
        existing.spots = [s.strip() for s in a.spots.split(",") if s.strip()]
    db.save_profile(existing)
    return "Profile saved.\n" + format_profile(existing)


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
            if a.item:
                wind = parse_wind_range(a.wind) if a.wind else None
                rec = quiver.recommend_single(prof, a.item, wind)
            else:
                rec = quiver.recommend_set(prof, db.list_owned())
            db.save_recommendation(rec)
            return format_recommendation(rec)
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
                out.append(f"#{r.id} {r.created_at} [{r.kind}] {parts}")
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
