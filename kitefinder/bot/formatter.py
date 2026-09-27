"""Turn a match into the Telegram alert: photos + a card with every detail + buttons.

Every alert states a description, the price (or that none was given), the location (or that
it's unknown), photos when the listing has any, the condition check, the source and why it
fits. The same item posted on several sources becomes one alert listing all of them.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from ..assemble import normalize_brand
from ..cli import fmt_size
from ..models import Listing
from .telegram import button

EMOJI = {"kite": "🪁", "board": "🏄", "bar": "🎚️", "harness": "🦺", "foil": "🛫", "wetsuit": "🤿"}
SOURCE_NAMES = {"facebook": "Facebook", "yad2": "Yad2"}
MAX_DESCRIPTION = 300
MAX_PHOTOS = 4


@dataclass
class Alert:
    listing_id: int
    match_ids: list[int]
    text: str  # HTML
    photos: list[str] = field(default_factory=list)
    buttons: list[list[dict]] = field(default_factory=list)


def _e(text) -> str:
    return html.escape(str(text), quote=False)


def html_to_text(text: str) -> str:
    return html.unescape(re.sub(r"</?b>", "", text))


def title(listing: Listing) -> str:
    kind = listing.type.capitalize()
    if listing.subtype:
        kind += f" ({listing.subtype.replace('_', ' ')})"
    parts = [kind]
    if listing.size is not None:
        parts.append(fmt_size(listing.type, listing.size))
    brand = normalize_brand(listing.brand)
    parts += [str(x) for x in (brand, listing.model) if x]
    if listing.size_label:
        parts.append(f"size {listing.size_label}")
    if listing.year:
        parts.append(str(listing.year))
    return " ".join(parts)


def source_label(listing: Listing) -> str:
    return SOURCE_NAMES.get(listing.source, listing.source or "unknown source")


def _price_line(listing: Listing, why: str) -> str:
    cond = {True: "new", False: "used", None: "condition not stated"}[listing.is_new]
    if listing.price_ils is not None:
        line = f"₪{listing.price_ils:,} · {cond}"
    elif listing.bundle_price_ils:
        line = f"sold as a bundle for ₪{listing.bundle_price_ils:,} · {cond}"
    else:
        line = f"price not stated — ask the seller · {cond}"
    compare = next((p for p in why.split("; ") if " the market " in p or " the typical " in p), "")
    return f"{line} · {compare}" if compare and listing.price_ils is not None else line


def _condition_line(assessment: dict | None) -> str:
    if not assessment:
        return "not checked from photos yet"
    score = assessment.get("score")
    verdict = assessment.get("verdict") or ""
    head = f"{score:g}/10" if score is not None else "photos don't show the condition"
    return f"{head} — {verdict}" if verdict else head


def format_alert(
    listing: Listing,
    match: dict,
    photos: list[str],
    assessment: dict | None = None,
    also: list[Listing] = (),
) -> Alert:
    why = match.get("why", "")
    description = (listing.description or "").strip(" |\n") or "(no description in the post)"
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1].rstrip() + "…"
    lines = [
        f"{EMOJI.get(listing.type, '📦')} <b>{_e(title(listing))}</b>",
        f"💰 {_e(_price_line(listing, why))}",
        f"📍 {_e(listing.location or 'location unknown')}",
        f"📝 {_e(description)}",
        f"🔍 Condition: {_e(_condition_line(assessment))}",
        f"🌐 {_e(source_label(listing))}"
        + (f" · {_e(listing.seller)}" if listing.seller not in ("", None, listing.source) else ""),
        f"✅ {_e(why.split('; ')[0])}" if why else "",
    ]
    shown = (" the market ", " the typical ", "no price stated")  # already on the 💰 line
    extra = [p for p in why.split("; ")[1:] if not any(s in p for s in shown)]
    if extra:
        lines.append(f"ℹ️ {_e('; '.join(extra))}")
    if match.get("query"):
        lines.append(f"🔔 Your search: {_e(match['query'])}")
    if also:
        others = ", ".join(
            f"{source_label(o)}" + (f" ₪{o.price_ils:,}" if o.price_ils else "") for o in also
        )
        lines.append(f"🔁 Also posted on: {_e(others)}")
    buttons = [
        [button("✅ Favorite", f"fav:{listing.id}"), button("❌ Dismiss", f"dis:{listing.id}")]
    ]
    if listing.url:
        buttons.append([button("🔗 Open the post", url=listing.url)])
    return Alert(
        listing.id,
        [match["id"]],
        "\n".join(line for line in lines if line),
        photos[:MAX_PHOTOS],
        buttons,
    )


def duplicate_key(listing: Listing) -> tuple | None:
    """Same item posted twice (e.g. Yad2 and a Facebook group): type, brand, size, price."""
    brand = normalize_brand(listing.brand)
    if not brand or listing.size is None and not listing.size_label:
        return None  # too little to tell two items apart
    return (listing.type, brand, listing.size, listing.size_label, listing.price_ils)
