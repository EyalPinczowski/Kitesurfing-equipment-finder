"""Rule-based extractor: the fallback when there is no Gemini key or the quota is used up.

Less clever than the model (no model names, weaker on unusual wording) but free, instant and
offline. It returns the same JSON shape as the model so both go through `clean_items`.
"""

from __future__ import annotations

import re

from ..assemble import BRAND_ALIASES
from . import normalize as nz

SALE_WORDS = ("מוכר", "מוכרת", "למכירה", "מכירה", "for sale", "selling", "sale", "מחיר", "price",
              "₪", 'ש"ח', "ש״ח", "שח", "nis", "במבצע", "בהזדמנות")  # fmt: skip
BUNDLE_WORDS = ("הכל ב", "הכל ביחד", "ביחד", "כל הציוד", "הכול ב", "all for", "together",
                "package", "as a set", "בחבילה", "כסט")  # fmt: skip
GENERIC_KITE = re.compile(r"(ציוד|gear|בית ספר|school|שיעור|lesson)\W{0,3}[הל]?$", re.IGNORECASE)

_PRICE_PATTERNS = [
    re.compile(r"(\d+(?:\.\d+)?)\s*(?:k|K|אלף)(?![\w])"),
    re.compile(r"(\d{1,3}(?:,\d{3})+|\d{3,5})\s*(?:₪|ש\"ח|ש״ח|שח|nis|ils|שקל)", re.IGNORECASE),
    re.compile(
        r"(?:₪|מחיר[:\s]*|price[:\s]*|(?<![\w])ב-?\s?)(\d{1,3}(?:,\d{3})+|\d{3,5})(?![\d])",
        re.IGNORECASE,
    ),
]
_KITE_SIZE = re.compile(
    r"(\d{1,2}(?:[.,]\d)?)\s*(?:מטר|מ['׳\"״]ר|מ['׳]|m²|m2|sqm|qm|m(?![a-z])|מ(?![א-ת]))",
    re.IGNORECASE,
)
_BARE_KITE = re.compile(
    r"(?<![\d.,'’])(\d{1,2}(?:[.,]\d)?)(?![\d.,]|\s*(?:ס\"מ|ס״מ|cm|ליטר|l\b|₪|ש\"ח|שח|k\b))"
)
_BOARD_DIMS = re.compile(r"(?<!\d)(1[1-9]\d)\s*(?:x|\*|×|/|על)\s*\d{2}")
_BOARD_CM = re.compile(r"(?<!\d)(1[1-9]\d)\s*(?:ס\"מ|ס״מ|סמ|cm)?(?!\d)", re.IGNORECASE)
_FEET = re.compile(r"(?<![\d'’])([4-9])\s*['’′]\s*(\d{1,2})?(?!\d)")
_LITRES = re.compile(r"(\d{2,3})\s*(?:ליטר|liters|litres|l)(?![\w])", re.IGNORECASE)
_BAR_CM = re.compile(r"(?<!\d)([3-6]\d)\s*(?:ס\"מ|ס״מ|סמ|cm)", re.IGNORECASE)
_WING_AREA = re.compile(r"(?<!\d)(\d{3,4})\s*(?:סמ\"ר|cm2|cm²|cm)?", re.IGNORECASE)
_YEAR = re.compile(
    # 2019 — but not a price ("ב 2000 ש\"ח", "₪2020"); 'model 21'; '21 — but not feet (5'10)
    r"(?<![\d₪])(?<!ב-)(?<!ב )(20[0-3]\d)(?!\d|\s*(?:₪|ש\"ח|ש״ח|שח|nis|ils|שקל))"
    r"|(?:מודל|model|דגם)\s*['’]?(\d{2})(?!\d)"
    r"|(?<![\d])['’](\d{2})(?!\d)",
    re.IGNORECASE,
)

_HARNESS_LABEL = re.compile(
    r"(?:מידה|size)\s*[:\-]?\s*(XXS|XS|XXL|XL|S|M|L)(?![a-z])|(?<![\w])(XXS|XS|XXL|XL|S|M|L)(?![\w])",
    re.IGNORECASE,
)
_BRANDS = [
    (nz._word_pattern(alias), canon)
    for canon, aliases in BRAND_ALIASES.items()
    for alias in (canon, *aliases)
    if len(alias) > 2  # skip "lf" etc.: too many false hits
]


def _prices(text: str) -> list[tuple[int, int]]:
    """(position, price) for every price-looking number."""
    found = {}
    for i, pat in enumerate(_PRICE_PATTERNS):
        for m in pat.finditer(text):
            # the "3.2k" pattern needs its suffix to scale; the others are the number itself
            raw = m.group(0) if i == 0 else m.group(1)
            price = nz.sane_price(nz.parse_price(raw))
            if price is not None:
                found.setdefault(m.start(1), price)
    return sorted(found.items())


def _year(text: str) -> int | None:
    for m in _YEAR.finditer(text):
        raw = m.group(1) or ("20" + (m.group(2) or m.group(3)))
        year = nz.parse_year(raw)
        if year:
            return year
    return None


def _size(chunk: str, item_type: str, subtype: str, price_spans: list[tuple[int, int]]):
    def not_price(m) -> bool:
        return not any(abs(m.start(1) - p) < 2 for p, _ in price_spans)

    if item_type == "kite":
        for m in _KITE_SIZE.finditer(chunk):
            return nz.sane_size("kite", "", nz.parse_size(m.group(1)))
        for m in _BARE_KITE.finditer(chunk):
            size = nz.sane_size("kite", "", nz.parse_size(m.group(1)))
            if size and 3 <= size <= 19 and not_price(m):
                return size
        return None
    if item_type == "board":
        if subtype == "foilboard":
            m = _LITRES.search(chunk)
            return nz.sane_size("board", "foilboard", float(m.group(1))) if m else None
        m = _BOARD_DIMS.search(chunk)  # 138x41
        if m:
            return float(m.group(1))
        m = _FEET.search(chunk)  # 5'4"
        if m:
            return nz.parse_size(m.group(0), "board")
        for m in _BOARD_CM.finditer(chunk):
            if not_price(m):
                return nz.sane_size("board", subtype, float(m.group(1)))
        return None
    if item_type == "bar":
        m = _BAR_CM.search(chunk)
        return float(m.group(1)) if m else None
    if item_type == "foil" and subtype == "front_wing":
        for m in _WING_AREA.finditer(chunk):
            if not_price(m) and not _YEAR.match(m.group(0)):
                return nz.sane_size("foil", "front_wing", float(m.group(1)))
    return None


def _harness_label(chunk: str) -> str:
    labels = [(m.group(1) or m.group(2)).upper() for m in _HARNESS_LABEL.finditer(chunk)]
    return "/".join(dict.fromkeys(labels))


def _brand(chunk: str) -> str:
    hits = [(m.start(), canon) for pat, canon in _BRANDS if (m := pat.search(chunk))]
    return min(hits)[1] if hits else ""


# Model names that identify the gear type when no gear word is written ("Duotone Evo 10m").
# Only used when a brand or a size is on the same line, so common words ("Edge") stay safe.
MODEL_WORDS = {
    "kite": ["orbit", "evo", "rebel", "neo", "dice", "juice", "reach", "pulse", "carve", "bandit",
             "switchblade", "moto", "drifter", "fx", "enduro", "catalyst", "edge", "zeolite",
             "pivot", "boxer", "dash", "triad", "xr", "xr5", "xr6", "xr7", "nexus", "section",
             "rpm", "rally", "machine", "sculp", "rise", "roam", "supermodel", "lithium", "ultra",
             "union", "sonic", "peak", "rs", "vegas", "mono", "breeze", "reo", "alpha",
             "קטליסט", "אבו", "רבל", "ניאו", "אורביט", "באנדיט", "סוויצ'בלייד"],
    "bar": ["trust bar", "click bar", "sensor", "overdrive", "trimlite"],
}  # fmt: skip
_MODEL_PATTERNS = [(nz._word_pattern(w), t) for t, words in MODEL_WORDS.items() for w in words]
SPLIT_ITEMS = re.compile(r"\s+ו-?(?=\s*[\w\d])|\s*\+\s*|,\s+(?=[\w\d])")
_TRAILING_PRICE = re.compile(r"[-–:]\s*(\d{3,5})\s*(?:₪|ש\"ח|שח|nis)?\s*$", re.IGNORECASE)


def _overlaps(found, start: int, end: int) -> bool:
    return any(a < end and start < b for a, b, *_ in found)


def _line_mentions(line: str) -> list[tuple[int, str, str]]:
    """(position, type, subtype) of each item named in one line, after cleanup."""
    raw = []
    for pat, t, sub in nz._TYPE_PATTERNS:
        for m in pat.finditer(line):
            if not _overlaps(raw, m.start(), m.end()):  # longer phrases were added first
                raw.append((m.start(), m.end(), t, sub, m.group(0), False))
    # a model name only counts with a brand, or with a size of its own kind, on the line
    context = {"kite": bool(_KITE_SIZE.search(line)), "bar": bool(_BAR_CM.search(line))}
    if _brand(line) or any(context.values()):
        for pat, t in _MODEL_PATTERNS:
            if not (_brand(line) or context[t]):
                continue
            for m in pat.finditer(line):
                if not _overlaps(raw, m.start(), m.end()):
                    raw.append((m.start(), m.end(), t, "", m.group(0), True))
    raw.sort()
    kite_size = bool(_KITE_SIZE.search(line))
    keep = []
    for i, (start, end, t, sub, word, is_model) in enumerate(raw):
        prev_ = raw[i - 1] if i else None
        next_ = raw[i + 1] if i + 1 < len(raw) else None
        near_prev = prev_ is not None and start - prev_[1] <= 3
        near_next = next_ is not None and next_[0] - end <= 3
        if t == "kite":
            if near_next and next_[2] == "foil" and kite_size:
                keep.append((start, t, sub, is_model))  # "קייט פויל 11 מטר" = a foil kite
                continue
            # "kite" describing another item: "משאבה לקייט", "kite surfboard", "סרפבורד לקייט"
            if (near_prev and prev_[2] != "kite") or (near_next and next_[2] != "kite"):
                continue
            if word.startswith("ל") and len(raw) > 1:
                continue
            if GENERIC_KITE.search(line[:start].rstrip()):
                continue  # "ציוד קייט" = kite gear in general
        if t == "foil" and near_prev and prev_[2] == "kite" and kite_size:
            continue  # the "פויל" of a foil kite
        keep.append((start, t, sub, is_model))
    if any(t == "foil" and sub == "complete" for _, t, sub, _ in keep):
        # mast / wing listed with a complete foil are parts of it, not extra items
        keep = [k for k in keep if not (k[1] == "foil" and k[2] in ("mast", "front_wing"))]
    merged: list[tuple[int, str, str]] = []
    for j, (pos, t, sub, is_model) in enumerate(keep):
        if merged and merged[-1][1] == t:
            end = keep[j + 1][0] if j + 1 < len(keep) else len(line)
            size_re = _KITE_SIZE if t == "kite" else _BOARD_DIMS
            size_between = size_re.search(line[merged[-1][0] : pos])
            own_size = size_re.search(line[pos:end])
            if is_model:
                # "קייט North Orbit 12 מטר" (no size in between) and "קייט 7 מטר של Duotone
                # Neo" (the model has no size of its own) both name the same kite
                same = not size_between or not own_size
            else:
                same = pos - merged[-1][0] <= 20  # "גלשן טווין טיפ": two words, one item
            if same:
                if sub and not merged[-1][2]:
                    merged[-1] = (merged[-1][0], t, sub)
                continue
        merged.append((pos, t, sub))
    if not merged and kite_size and _brand(line):
        merged = [(0, "kite", "")]  # "אוזון 12 מטר": a size in m² with a brand is a kite
    return merged


def _split_multi(t: str, chunk: str) -> list[str]:
    """'9 מטר ב-2400 ו-12 מטר ב-2900' → one part per item when a chunk names several."""
    if t == "kite":
        count = len(_KITE_SIZE.findall(chunk))
    elif t == "harness":
        count = len(re.findall(r"מידה|size", chunk, re.IGNORECASE))
    else:
        return [chunk]
    if count < 2:
        return [chunk]
    parts = [p for p in SPLIT_ITEMS.split(chunk) if p and p.strip()]
    sized = [p for p in parts if (_KITE_SIZE.search(p) if t == "kite" else _harness_label(p))]
    return sized if len(sized) == count else [chunk]


def _chunks(text: str) -> list[tuple[str, str, str, str]]:
    """(type, subtype, item text, surrounding text) for every item in the post."""
    out = []
    for line in re.split(r"[\n•]+", text):
        if line.strip().endswith(":") and not re.search(r"\d", line):
            continue  # a header such as "מוכר גלשנים:" — the items follow on the next lines
        mentions = _line_mentions(line)
        for i, (pos, t, sub) in enumerate(mentions):
            start = 0 if i == 0 else pos
            if i:  # "a 140 twin tip": a size written just before the item word belongs to it
                before = re.search(r"(\d{2,3}(?:[.,]\d)?)\s*$", line[max(0, pos - 8) : pos])
                if before:
                    start = pos - len(before.group(0))
            end = mentions[i + 1][0] if i + 1 < len(mentions) else len(line)
            chunk = line[start:end]
            for part in _split_multi(t, chunk):
                out.append((t, sub, part, chunk))
    return out


def _item_price(chunk: str) -> list[tuple[int, int]]:
    prices = _prices(chunk)
    if not prices:  # "טווין טיפ 135x40 - 900": a trailing number after a dash
        m = _TRAILING_PRICE.search(chunk.strip())
        if m and not nz.parse_year(m.group(1)):
            prices = [(m.start(1), int(m.group(1)))]
    return prices


def _condition(text: str, default=None):
    if nz.has_any(text, nz.NEW_WORDS):
        return True
    if nz.has_any(text, nz.USED_WORDS):
        return False
    return default


def extract_raw(text: str) -> dict:
    """Same JSON shape as the Gemini answer."""
    low = text.lower()
    offering = nz.has_any(low, ("מוכר", "מוכרת", "למכירה", "for sale", "selling"))
    selling = nz.has_any(low, SALE_WORDS) or bool(_prices(text)) or nz.has_any(low, nz.SOLD_WORDS)
    if nz.has_any(low, nz.WANTED_WORDS) and not offering:
        return {"is_sale_post": False, "not_sale_reason": "looking to buy", "items": []}
    if nz.has_any(low, nz.NOT_SALE_WORDS) and not offering:
        return {"is_sale_post": False, "not_sale_reason": "lesson / trip / course", "items": []}
    chunks = _chunks(text)
    if not chunks:
        return {"is_sale_post": False, "not_sale_reason": "no gear mentioned", "items": []}
    if not selling:
        return {"is_sale_post": False, "not_sale_reason": "no sale wording or price", "items": []}

    post_new = _condition(low)
    post_sold = nz.has_any(low, nz.SOLD_WORDS)
    items = []
    for t, sub, chunk, parent in chunks:
        prices = _item_price(chunk)
        cl = chunk.lower()
        items.append(
            {
                "type": t,
                "subtype": sub,
                "brand": _brand(chunk) or _brand(parent),
                "model": None,
                "size": _size(chunk, t, sub, prices),
                "size_label": _harness_label(chunk) if t == "harness" else None,
                "year": _year(chunk) or _year(parent),
                "price_ils": prices[0][1] if prices else None,
                "is_new": _condition(cl, post_new),
                "sold": nz.has_any(cl, nz.SOLD_WORDS) or (post_sold and len(chunks) == 1),
                "description": chunk.strip()[:200],
            }
        )
    bundle = None
    all_prices = _prices(text)
    if len(all_prices) == 1 and any(i["price_ils"] is None for i in items):
        ((_, only),) = all_prices
        priced = [i for i in items if i["price_ils"] is not None]
        if len(items) == 1:
            items[0]["price_ils"] = only
        elif nz.has_any(low, BUNDLE_WORDS) or not priced:
            bundle = only
            for i in priced:  # the single price belongs to the bundle, not one item
                i["price_ils"] = None
    return {
        "is_sale_post": True,
        "location": nz.find_city(text) or None,
        "bundle_price_ils": bundle,
        "items": items,
    }
