"""The /setup questionnaire: one question at a time, buttons where there's a fixed choice.

Progress is kept in the database, so a restart in the middle doesn't lose your answers. At the
end the profile, owned gear and websites are saved and the three quiver options are shown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..assemble import normalize_brand
from ..cli import parse_wind_range
from ..db import Database
from ..llm import normalize as nz
from ..models import DISCIPLINES, OwnedItem, Profile, ValidationError
from ..sizing import spots
from .telegram import button

STATE_KEY = "setup_state"


@dataclass
class Reply:
    text: str
    buttons: list[list[dict]] = field(default_factory=list)
    edit: bool = False  # replace the message whose button was pressed (the spot list)


def _choices(*pairs: tuple[str, str]) -> list[list[dict]]:
    return [[button(label, f"q:{value}") for label, value in pairs]]


SKIP = [[button("Skip", "q:skip")]]

PROMPTS = {
    "weight": ("Let's set you up. What's your weight in kg?", []),
    "waist": ("Your hip / waist size in cm? (for the harness)", []),
    "skill": (
        "Your level?",
        _choices(
            ("Beginner", "beginner"), ("Intermediate", "intermediate"), ("Advanced", "advanced")
        ),
    ),
    "style": (
        "What do you ride?",
        _choices(("Twin tip", "twintip"), ("Surfboard", "surfboard"), ("Foil", "foil")),
    ),
    "focus": (
        "What kind of twin tip riding? (so I suggest the right kite models)",
        [
            [button("Freeride", "q:freeride"), button("Big air", "q:bigair")],
            [button("Freestyle", "q:freestyle"), button("A bit of everything", "q:any")],
        ],
    ),
    "areas": (
        "Where do you ride? Tap a region and pick your spots from its list (as many as you "
        "like, from several regions). Or type them — several at once: בת גלים, Sdot Yam — "
        "or type a wind range in knots like 12-25.",
        [],  # the region buttons: see _regions()
    ),
    "season": (
        "Which season's wind should I plan for?",
        _choices(("All year", "all"), ("Summer", "summer"), ("Winter", "winter")),
    ),
    "gear": (
        "Do you own any gear? Add items one by one — I won't recommend what you have.",
        [[button("➕ Add an item", "q:add"), button("Done", "q:done")]],
    ),
    "gear_type": (
        "What is it?",
        [
            [button("Kite", "q:kite"), button("Board", "q:board"), button("Bar", "q:bar")],
            [button("Harness", "q:harness"), button("Foil", "q:foil")],
        ],
    ),
    "gear_size": (
        "Its size? (kite m², board length cm, foil board litres) — or Skip",
        SKIP,
    ),
    "gear_name": ("Brand, model and year? e.g. North Orbit 2021 — or Skip", SKIP),
    "budget": ("Your budget in ₪ for what you still need? — or Skip", SKIP),
    "condition": (
        "New or used gear?",
        _choices(("New", "new"), ("Used", "used"), ("Both", "both")),
    ),
    "min_year": ("Oldest model year you'd accept? e.g. 2018 — or Skip", SKIP),
    "travel": ("How far would you drive to pick up, in km? — or Skip", SKIP),
    "sites": (
        "Websites to search: send links one per message (shops, used-gear pages). "
        "Tap Done when finished.",
        [[button("Done", "q:done")]],
    ),
}
ORDER = [
    "weight",
    "waist",
    "skill",
    "style",
    "focus",
    "areas",
    "season",
    "gear",
    "budget",
    "condition",
    "min_year",
    "travel",
    "sites",
]


def _picked_line(picked: list[str]) -> str:
    return f"\nPicked: {', '.join(picked)}" if picked else ""


def _number(text: str, lo: float, hi: float, what: str) -> float:
    m = re.search(r"\d+(?:[.,]\d+)?", text or "")
    if not m:
        raise ValidationError(f"please send a number for {what}")
    value = float(m.group(0).replace(",", "."))
    if not lo <= value <= hi:
        raise ValidationError(f"{what} should be between {lo:g} and {hi:g}")
    return value


def _typed_choice(step: str, text: str) -> str:
    """A typed button label ("Intermediate", "twin tip") counts as pressing that button."""
    key = step if step in PROMPTS else ""
    for row in PROMPTS[key][1] if key else []:
        for b in row:
            label = b["text"].lstrip("➕ ").casefold()
            if text.casefold() in (label, label.replace(" ", "")):
                return b["callback_data"].removeprefix("q:")
    return text


REGION_BUTTONS = (("North", "north"), ("Center", "center"), ("South", "south"),
                  ("Eilat", "eilat"), ("Kinneret", "kinneret"))  # fmt: skip


def _done_row(picked: list[str]) -> list[dict]:
    return [button(f"✅ Done — {len(picked)} spot{'s' if len(picked) != 1 else ''}", "q:done")]


def _regions(picked: list[str]) -> list[list[dict]]:
    """The region buttons (each opens its spot list), and Done once something is picked."""
    rows = [
        [button(label, f"q:{key}") for label, key in REGION_BUTTONS[:3]],
        [button(label, f"q:{key}") for label, key in REGION_BUTTONS[3:]],
    ]
    if picked:
        rows.append(_done_row(picked))
    return rows


def _spot_rows(region: str, picked: list[str]) -> list[list[dict]]:
    all_spots, _ = spots.load_spots()
    members = [(i, s) for i, s in enumerate(all_spots) if s.region == region]
    cells = [button(("✓ " if s.name in picked else "") + s.name, f"q:spot:{i}") for i, s in members]
    rows = [cells[i : i + 2] for i in range(0, len(cells), 2)]
    rows.append([button("All of them", f"q:all:{region}"), button("⬅ Other regions", "q:regions")])
    if picked:
        rows.append(_done_row(picked))
    return rows


class Questionnaire:
    def __init__(self, db: Database):
        self.db = db

    # --- state ----------------------------------------------------------------------------

    def state(self) -> dict | None:
        raw = self.db.meta_get(STATE_KEY)
        return json.loads(raw) if raw else None

    def _save(self, state: dict | None) -> None:
        if state is None:
            with self.db.conn:
                self.db.conn.execute("DELETE FROM meta WHERE key = ?", (STATE_KEY,))
        else:
            self.db.meta_set(STATE_KEY, json.dumps(state, ensure_ascii=False))

    @property
    def active(self) -> bool:
        return self.state() is not None

    def cancel(self) -> Reply:
        self._save(None)
        return Reply("Setup stopped. Nothing was changed. Start again with /setup")

    def start(self) -> Reply:
        self._save({"step": "weight", "draft": {}, "gear": [], "item": {}})
        return self._ask("weight")

    def _ask(self, step: str, note: str = "", state: dict | None = None) -> Reply:
        text, buttons = PROMPTS[step]
        if step == "areas":
            buttons = _regions((state or {}).get("draft", {}).get("picked", []))
        return Reply(f"{note}\n{text}".strip(), buttons)

    def _next(self, state: dict, after: str) -> str:
        i = ORDER.index(after) + 1
        while True:
            step = ORDER[i]
            if step == "season" and state["draft"].get("wind_source") == "manual":
                i += 1  # a typed wind range needs no season
            elif step == "focus" and state["draft"].get("style") != "twintip":
                i += 1  # the riding focus is a twin tip question
            else:
                return step

    # --- the spot list (areas step) ----------------------------------------------------------

    def _spot_list(self, state: dict, value: str) -> Reply | None:
        """Region → its spots as tick boxes; tapping a spot ticks or unticks it. The list
        replaces the message it was tapped in. None: not a list action (typed text, Done)."""
        regions = dict((key, label) for label, key in REGION_BUTTONS)
        d = state["draft"]
        picked: list[str] = d.setdefault("picked", [])
        all_spots, _ = spots.load_spots()
        kind, _, arg = value.partition(":")
        if value in regions:
            region = value
        elif kind == "spot" and arg.isdigit() and int(arg) < len(all_spots):
            spot = all_spots[int(arg)]
            if spot.name in picked:
                picked.remove(spot.name)
            else:
                picked.append(spot.name)
            region = spot.region
        elif kind == "all" and arg in regions:
            region = arg
            picked.extend(s.name for s in all_spots if s.region == arg and s.name not in picked)
        elif value == "regions":
            self._save(state)
            return Reply(PROMPTS["areas"][0] + _picked_line(picked), _regions(picked), edit=True)
        else:
            return None
        self._save(state)
        text = f"{spots.REGION_LABELS[region]} — tap the spots you ride; tap again to remove."
        return Reply(text + _picked_line(picked), _spot_rows(region, picked), edit=True)

    # --- answers --------------------------------------------------------------------------

    def answer(self, text: str, pressed: bool = False) -> list[Reply]:
        """`pressed`: a button tap (typed text means what it says: 'north' = every spot
        in the north, while tapping North opens its spot list)."""
        state = self.state()
        if state is None:
            return [Reply("No setup in progress. Start with /setup")]
        value = (text or "").strip()
        step = state["step"]
        if not (step == "areas" and not pressed):
            value = _typed_choice(step, value)
        if step == "areas" and pressed:
            listed = self._spot_list(state, value)
            if listed is not None:
                return [listed]
        try:
            goto = self._apply(state, step, value)
        except ValidationError as e:
            return [self._ask(step, f"⚠ {e}", state)]
        if goto == "finish":
            return self._finish(state)
        state["step"] = goto
        self._save(state)
        replies = []
        if step == "sites" and goto == "sites":
            replies.append(Reply(f"Added. {len(state['draft'].get('sites', []))} site(s) so far."))
            return replies
        replies.append(self._ask(goto, state=state))
        return replies

    def _apply(self, state: dict, step: str, value: str) -> str:
        d = state["draft"]
        skip = value.lower() == "skip"
        if step == "weight":
            d["weight_kg"] = _number(value, 30, 150, "weight")
        elif step == "waist":
            d["waist_cm"] = _number(value, 50, 150, "hip/waist")
        elif step == "skill":
            if value not in ("beginner", "intermediate", "advanced"):
                raise ValidationError("tap one of the buttons")
            d["skill"] = value
        elif step == "style":
            if value not in ("twintip", "surfboard", "foil"):
                raise ValidationError("tap one of the buttons")
            d["style"] = value
        elif step == "focus":
            if value not in (*DISCIPLINES, "any"):
                raise ValidationError("tap one of the buttons")
            d["discipline"] = "" if value == "any" else value
        elif step == "areas":
            picked = d.pop("picked", [])
            if value == "done":
                if not picked:
                    d["picked"] = picked
                    raise ValidationError("pick at least one spot — or type them")
                names = picked
            else:
                names = None
            try:
                if names is not None:
                    raise ValidationError("picked from the list")
                lo, hi = parse_wind_range(value)
            except ValidationError:
                try:
                    area = spots.resolve_areas(names or [*picked, *spots.split_areas(value)])
                except ValidationError:
                    d["picked"] = picked  # keep what was ticked when the typing has a typo
                    raise
                d.update(spots=area.spot_names, wind_source="areas", gusty=area.gusty)
            else:
                if not (4 <= lo <= 50 and lo < hi <= 60):  # the profile's own limits
                    raise ValidationError("wind range should look like 12-25 (knots)") from None
                d.update(wind_min_kn=lo, wind_max_kn=hi, wind_source="manual")
        elif step == "season":
            if value not in spots.SEASONS:
                raise ValidationError("tap one of the buttons")
            area = spots.resolve_areas(d["spots"], value)
            d.update(season=value, wind_min_kn=area.wind_min_kn, wind_max_kn=area.wind_max_kn)
        elif step == "gear":
            if value == "add":
                state["item"] = {}
                return "gear_type"
            if value != "done":
                raise ValidationError("tap Add an item or Done")
        elif step == "gear_type":
            if value not in ("kite", "board", "bar", "harness", "foil"):
                raise ValidationError("tap one of the buttons")
            state["item"] = {"type": value}
            return "gear_size" if value != "harness" else "gear_name"
        elif step == "gear_size":
            if not skip:
                state["item"]["size"] = _number(value, 1, 3000, "the size")
            return "gear_name"
        elif step == "gear_name":
            item = state["item"]
            if not skip:
                year = nz.parse_year(value)
                words = re.sub(r"(?<!\d)(19|20)\d{2}(?!\d)|['’]\d{2}\b", "", value).split()
                if words:
                    item["brand"] = normalize_brand(words[0])
                    item["model"] = " ".join(words[1:])
                if year:
                    item["year"] = year
            OwnedItem(**item).validate()
            state["gear"].append(item)
            state["item"] = {}
            return "gear"
        elif step == "budget":
            if not skip:
                d["budget_ils"] = int(_number(value.replace(",", ""), 0, 1_000_000, "the budget"))
        elif step == "condition":
            if value not in ("new", "used", "both"):
                raise ValidationError("tap one of the buttons")
            d["condition_pref"] = value
        elif step == "min_year":
            if not skip:
                d["min_year"] = int(_number(value, 1995, 2100, "the year"))
        elif step == "travel":
            if not skip:
                d["travel_km"] = int(_number(value, 0, 1000, "the distance"))
        elif step == "sites":
            if value == "done":
                return "finish"
            from ..db import normalize_url

            d.setdefault("sites", []).append(normalize_url(value))
            return "sites"
        return self._next(state, step)

    def _finish(self, state: dict) -> list[Reply]:
        from ..cli import format_owned, format_profile

        d = dict(state["draft"])
        sites = d.pop("sites", [])
        try:
            profile = Profile.from_dict(d).validate()
        except ValidationError as e:  # shouldn't happen — every answer was checked
            return [Reply(f"⚠ Couldn't save: {e}. Send /setup to start again.")]
        self.db.save_profile(profile)
        for item in state["gear"]:
            self.db.add_owned(OwnedItem(**item))
        added = sum(self.db.add_site(url)[1] for url in sites)
        self._save(None)
        owned = [format_owned(i) for i in self.db.list_owned()]
        summary = ["✅ Saved. Your profile:", format_profile(profile)]
        summary.append("Your gear:\n" + "\n".join(owned) if owned else "Your gear: none")
        summary.append(f"Websites: {added} added, {len(self.db.list_sites())} in total.")
        return [
            Reply("\n".join(summary)),
            Reply("Here are your options:", [[button("See the recommendation", "cmd:recommend")]]),
        ]
