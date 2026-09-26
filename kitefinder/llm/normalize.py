"""Parse the messy ways people write prices, sizes, years and gear types (Hebrew + English).

Used to clean what the LLM returns, to cross-check it against the post text, and by the
rule-based fallback extractor.
"""

from __future__ import annotations

import re
from datetime import date

from ..assemble import normalize_brand

# --- item types -------------------------------------------------------------------------------

# (type, subtype) for words that name gear. Order matters: longer / more specific first.
TYPE_WORDS: list[tuple[str, str, str]] = [
    # foil parts before boards/kites ("foil board", "foil kite" are handled below)
    ("foil board", "board", "foilboard"),
    ("foilboard", "board", "foilboard"),
    ("פויל בורד", "board", "foilboard"),
    ("גלשן פויל", "board", "foilboard"),
    ("front wing", "foil", "front_wing"),
    ("כנף קדמית", "foil", "front_wing"),
    ("mast", "foil", "mast"),
    ("תורן", "foil", "mast"),
    ("hydrofoil", "foil", "complete"),
    ("foil", "foil", "complete"),
    ("פויל", "foil", "complete"),
    ("twin tip", "board", "twintip"),
    ("twintip", "board", "twintip"),
    ("טווין טיפ", "board", "twintip"),
    ("טוויןטיפ", "board", "twintip"),
    ("טווינטיפ", "board", "twintip"),
    ("directional", "board", "surfboard"),
    ("surfboard", "board", "surfboard"),
    ("surf board", "board", "surfboard"),
    ("גלשן גלים", "board", "surfboard"),
    ("סרפבורד", "board", "surfboard"),
    ("דיירקשונל", "board", "surfboard"),
    ("kiteboard", "board", ""),
    ("קייטבורד", "board", ""),
    ("boards", "board", ""),
    ("board", "board", ""),
    ("גלשנים", "board", ""),
    ("גלשן", "board", ""),
    ("בורד", "board", ""),
    ("harnesses", "harness", ""),
    ("harness", "harness", ""),
    ("טרפזים", "harness", ""),
    ("טרפז", "harness", ""),
    ("trapez", "harness", ""),
    ("control bar", "bar", ""),
    ("bar", "bar", ""),
    ("בר", "bar", ""),
    ("wetsuit", "wetsuit", ""),
    ("חליפה", "wetsuit", ""),
    ("חליפת", "wetsuit", ""),
    ("kites", "kite", ""),
    ("kite", "kite", ""),
    ("קייטים", "kite", ""),
    ("קייט", "kite", ""),
    ("עפיפונים", "kite", ""),
    ("עפיפון", "kite", ""),
    ("pump", "other", ""),
    ("משאבה", "other", ""),
]

VALID_TYPES = ("kite", "bar", "board", "harness", "foil", "wetsuit", "other")
VALID_SUBTYPES = {
    "board": ("twintip", "surfboard", "foilboard"),
    "foil": ("front_wing", "mast", "complete"),
}


_HEB_PREFIX = "[הובלמשכ]{0,2}"  # Hebrew attaches ה/ו/ב/ל/מ/ש/כ to the next word


def _word_pattern(word: str) -> re.Pattern:
    hebrew = bool(re.search(r"[\u05d0-\u05ea]", word))
    body = re.escape(word).replace(r"\ ", r"[\s\-]*")
    # a 2-letter word like "בר" would match "כבר" (already) / "שבר" (broken) with any prefix:
    # short words only take ה / ו ("הבר", "והבר")
    if not hebrew:
        prefix = ""
    elif len(word.replace(" ", "")) <= 2:
        prefix = "[הו]{0,2}"
    else:
        prefix = _HEB_PREFIX
    return re.compile(rf"(?<![\w]){prefix}{body}(?![\w])", re.IGNORECASE)


_TYPE_PATTERNS = [(_word_pattern(w), t, sub) for w, t, sub in TYPE_WORDS]


def find_types(text: str) -> list[tuple[int, str, str]]:
    """All gear words in the text as (position, type, subtype), longest phrase wins."""
    taken: list[tuple[int, int]] = []
    found = []
    for pat, t, sub in _TYPE_PATTERNS:
        for m in pat.finditer(text or ""):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue  # already part of a longer phrase ("foil board" vs "board")
            taken.append((m.start(), m.end()))
            found.append((m.start(), t, sub))
    return sorted(found)


def normalize_type(raw: str | None) -> tuple[str, str]:
    """('kite', '') / ('board', 'twintip') … from a word or phrase; ('other', '') if unknown."""
    text = (raw or "").strip().lower()
    if text in VALID_TYPES:
        return text, ""
    found = find_types(text)
    return (found[0][1], found[0][2]) if found else ("other", "")


def normalize_subtype(item_type: str, raw: str | None) -> str:
    raw = (raw or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"twin_tip": "twintip", "surf": "surfboard", "directional": "surfboard",
               "foil_board": "foilboard", "wing": "front_wing", "frontwing": "front_wing",
               "full": "complete", "set": "complete"}  # fmt: skip
    raw = aliases.get(raw, raw)
    return raw if raw in VALID_SUBTYPES.get(item_type, ()) else ""


# --- numbers ----------------------------------------------------------------------------------

_NUM = r"\d+(?:[.,]\d+)?"


def _to_float(s: str) -> float:
    return float(s.replace(",", "."))


def parse_price(raw) -> int | None:
    """₪3,200 / 3200 ש"ח / 3.2k / 3,200₪ / 3200 nis / 'מחיר 3200' → 3200. None if unclear."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return int(round(raw)) if raw > 0 else None
    text = str(raw).lower().replace("‏", "").strip()
    if not text:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:k|אלף)\b", text)
    if m:
        return int(round(float(m.group(1)) * 1000))
    m = re.search(r"\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?", text)
    if not m:
        return None
    value = float(m.group(0).replace(",", ""))
    return int(round(value)) if value > 0 else None


def parse_size(raw, item_type: str = "kite") -> float | None:
    """Kite '12 מטר' / '12m²' / '12 מ"ר' → 12; board '138x41' / '138 ס"מ' / 5'4" → cm."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        return float(raw) if raw > 0 else None
    text = str(raw).strip().lower()
    feet = re.search(r"(\d)\s*['’′]\s*(\d{1,2})?\s*(?:\"|”|″|'')?", text)
    if item_type == "board" and feet and not re.search(r"\d{3}", text):
        ft, inch = int(feet.group(1)), int(feet.group(2) or 0)
        return float(round((ft * 12 + inch) * 2.54))
    m = re.search(_NUM, text)
    return _to_float(m.group(0)) if m else None


def parse_year(raw) -> int | None:
    """2021 / '21 / 'model 2021' → 2021; out-of-range years → None."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        year = int(raw)
    else:
        text = str(raw)
        m = re.search(r"(19|20)\d{2}", text)
        if m:
            year = int(m.group(0))
        else:
            m = re.search(r"['’](\d{2})\b", text)
            if not m:
                return None
            year = 2000 + int(m.group(1))
    if year < 100:
        year += 2000
    return year if 1995 <= year <= date.today().year + 1 else None


def normalize_size_label(raw: str | None) -> str:
    """Harness sizes: 'm', 'מידה L', 'S/M', 'xl' → 'M', 'L', 'S/M', 'XL'."""
    labels = re.findall(r"\b(XXS|XS|XXL|XL|S|M|L)\b", (raw or "").upper())
    return "/".join(dict.fromkeys(labels))


def normalize_brand_name(raw: str | None) -> str:
    return normalize_brand(raw or "")


# --- sanity ranges ----------------------------------------------------------------------------

# Values outside these are almost certainly misreads (a phone number, a price as a size …).
SIZE_RANGES = {
    ("kite", ""): (2, 21),
    ("board", "twintip"): (110, 165),
    ("board", "surfboard"): (140, 200),
    ("board", "foilboard"): (15, 180),  # litres
    ("board", ""): (15, 200),
    ("bar", ""): (35, 65),
    ("foil", "front_wing"): (400, 3000),  # cm²
}
PRICE_RANGE = (50, 40000)


def sane_size(item_type: str, subtype: str, size: float | None) -> float | None:
    if size is None:
        return None
    lo, hi = SIZE_RANGES.get((item_type, subtype), SIZE_RANGES.get((item_type, ""), (0, 1e9)))
    return size if lo <= size <= hi else None


def sane_price(price: int | None) -> int | None:
    if price is None:
        return None
    return price if PRICE_RANGE[0] <= price <= PRICE_RANGE[1] else None


# --- cross-checking against the post text -----------------------------------------------------


def numbers_in_text(text: str) -> set[float]:
    """Every number a reader could see in the text, including 3.2k → 3200, 3,200 → 3200,
    5'4" → 163 cm and '21 → 2021, so LLM output can be checked against it."""
    t = (text or "").lower()
    found: set[float] = set()
    for m in re.finditer(r"\d{1,3}(?:,\d{3})+", t):
        found.add(float(m.group(0).replace(",", "")))
    for m in re.finditer(_NUM, t):
        found.add(_to_float(m.group(0)))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:k|אלף)\b", t):
        found.add(float(m.group(1)) * 1000)
    for m in re.finditer(r"(\d)\s*['’′]\s*(\d{1,2})?", t):  # 5'4" and whole feet 6'
        found.add(float(round((int(m.group(1)) * 12 + int(m.group(2) or 0)) * 2.54)))
    for m in re.finditer(r"(?:(?<!\d)['’]|מודל\s*|model\s*|דגם\s*)(\d{2})\b", t):
        found.add(2000.0 + int(m.group(1)))
    return found


def in_text(value: float | None, numbers: set[float], tolerance: float = 0.01) -> bool:
    return value is None or any(abs(value - n) <= tolerance for n in numbers)


# --- sale / sold / wanted signals -------------------------------------------------------------

NOT_SALE_WORDS = ("שיעור", "שיעורי", "קורס", "טיול", "לינה", "הרשמה", "להרשמה", "lesson",
                  "course", "camp", "trip", "סדנה")  # fmt: skip
SOLD_WORDS = ("נמכר", "sold", "נסגר", "לא רלוונטי", "not available")
WANTED_WORDS = ("מחפש", "מחפשת", "looking for", "wtb", "want to buy", "קונה ", "מעוניין לקנות",
                "מעוניינת לקנות", "wanted")  # fmt: skip
NEW_WORDS = ("חדש באריזה", "חדש לגמרי", "brand new", "bnib", "new in box", "חדש!", "חדש,", "(חדש)")
USED_WORDS = ("משומש", "יד שנייה", "יד 2", "used", "כמו חדש", "like new", "שימוש קל")


def has_any(text: str, words) -> bool:
    t = (text or "").lower()
    return any(w in t for w in words)


# --- places -----------------------------------------------------------------------------------

# Hebrew/English spellings -> one canonical English name (used for distance ranking later).
CITIES = {
    "Tel Aviv": ["תל אביב", 'ת"א', "תא", "tel aviv", "tlv"],
    "Haifa": ["חיפה", "haifa"],
    "Herzliya": ["הרצליה", "herzliya", "herzlia"],
    "Netanya": ["נתניה", "netanya"],
    "Ashdod": ["אשדוד", "ashdod"],
    "Ashkelon": ["אשקלון", "ashkelon"],
    "Eilat": ["אילת", "eilat"],
    "Hadera": ["חדרה", "hadera"],
    "Raanana": ["רעננה", "raanana", "ra'anana"],
    "Kfar Saba": ["כפר סבא", "kfar saba"],
    "Rishon LeZion": ["ראשון לציון", 'ראשל"צ', "rishon", "rishon lezion"],
    "Holon": ["חולון", "holon"],
    "Bat Yam": ["בת ים", "bat yam"],
    "Ramat Gan": ["רמת גן", "ramat gan"],
    "Petah Tikva": ["פתח תקווה", "פתח תקוה", "petah tikva"],
    "Jerusalem": ["ירושלים", "jerusalem"],
    "Beer Sheva": ["באר שבע", "beer sheva", "be'er sheva"],
    "Caesarea": ["קיסריה", "caesarea"],
    "Zichron Yaakov": ["זכרון יעקב", "זכרון", "zichron"],
    "Atlit": ["עתלית", "atlit"],
    "Nahariya": ["נהריה", "nahariya"],
    "Akko": ["עכו", "akko", "acre"],
    "Kiryat Yam": ["קריית ים", "קרית ים", "kiryat yam"],
    "Krayot": ["הקריות", "קריות", "krayot"],
    "Tiberias": ["טבריה", "tiberias"],
    "Rehovot": ["רחובות", "rehovot"],
    "Ness Ziona": ["נס ציונה", "ness ziona"],
    "Modiin": ["מודיעין", "modiin"],
    "Yavne": ["יבנה", "yavne"],
    "Givatayim": ["גבעתיים", "givatayim"],
    "Hod HaSharon": ["הוד השרון", "hod hasharon"],
    "Karmiel": ["כרמיאל", "karmiel"],
    "Michmoret": ["מכמורת", "michmoret"],
    "Sdot Yam": ["שדות ים", "sdot yam"],
    "Beit Yanai": ["בית ינאי", "beit yanai"],
}
_CITY_PATTERNS = sorted(
    ((_word_pattern(alias), canon) for canon, aliases in CITIES.items() for alias in aliases),
    key=lambda pc: -len(pc[0].pattern),
)


def find_city(text: str) -> str:
    """First known Israeli city mentioned in the text (canonical English), else ''."""
    hits = [(m.start(), canon) for pat, canon in _CITY_PATTERNS if (m := pat.search(text or ""))]
    return min(hits)[1] if hits else ""


def normalize_location(raw: str | None) -> str:
    raw = (raw or "").strip()
    return find_city(raw) or raw[:60]
