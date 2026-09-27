"""Judge equipment condition from listing photos with Gemini.

Photos are shrunk before upload (Pillow, when installed) to save quota and mobile data. The
answer is validated: the score is clamped to 1–10, flags must come from a fixed list, and a
stock / catalogue photo gets no score because it says nothing about this item's condition.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from itertools import islice

from .gemini import GeminiClient, Image

MAX_IMAGES = 4
MAX_SIDE = 1024
MAX_BYTES = 3_500_000  # without Pillow, bigger photos are skipped rather than sent

FLAGS = {
    "repair_patch": "repair patch or glued repair",
    "tear": "tear or hole",
    "uv_faded": "UV fading / tired cloth",
    "delamination": "delamination",
    "dings_scratches": "dings or deep scratches",
    "broken_part": "broken fin, strap, pulley or line",
    "worn_lines": "worn or frayed lines",
    "missing_parts": "parts look missing",
    "looks_like_new": "looks like new",
    "stock_photo": "catalogue / stock photo",
    "not_equipment": "photo doesn't show the gear",
}

PROMPT = """You are checking second-hand kitesurfing gear from a marketplace listing.
The listing says it is: {what}.
Look at the photos and judge the visible condition of THIS item.
- score: 1 (wreck) to 10 (new). null if the photos can't show condition (e.g. only a
  catalogue/stock photo, or no gear visible).
- flags: any that apply, from: {flags}
- verdict: one short sentence for a buyer (what to check or ask the seller).
Judge only what is visible; do not guess about things the photos don't show."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "NUMBER", "nullable": True},
        "flags": {"type": "ARRAY", "items": {"type": "STRING", "enum": list(FLAGS)}},
        "verdict": {"type": "STRING"},
    },
    "required": ["flags", "verdict"],
}


@dataclass
class Assessment:
    score: float | None
    flags: list[str] = field(default_factory=list)
    verdict: str = ""
    photos_used: int = 0

    @property
    def summary(self) -> str:
        score = f"{self.score:g}/10" if self.score is not None else "condition not visible"
        notes = ", ".join(FLAGS[f] for f in self.flags if f not in ("looks_like_new",))
        return f"{score}{f' — {notes}' if notes else ''}"


def sniff_mime(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def prepare_image(data: bytes) -> Image | None:
    """Shrink to ≤1024 px JPEG when Pillow is available; None for unusable data."""
    mime = sniff_mime(data)
    if mime is None:
        return None
    try:
        from PIL import Image as PILImage
    except ImportError:  # pkg install python-pillow; until then send small photos as they are
        return Image(data, mime) if len(data) <= MAX_BYTES else None
    try:
        with PILImage.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            im.save(out, "JPEG", quality=80)
            return Image(out.getvalue(), "image/jpeg")
    except Exception:  # corrupt or unsupported image: skip it  # noqa: BLE001
        return None


def validate(raw: dict) -> Assessment:
    flags = [f for f in dict.fromkeys(raw.get("flags") or []) if f in FLAGS]
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, int | float):
        score = None
    else:
        score = float(min(10, max(1, round(score, 1))))
    if "stock_photo" in flags or "not_equipment" in flags:
        score = None  # a catalogue photo says nothing about this item's condition
    if "looks_like_new" in flags and any(
        f in flags for f in ("repair_patch", "tear", "delamination", "broken_part")
    ):
        flags.remove("looks_like_new")  # contradictory: trust the damage
    verdict = str(raw.get("verdict") or "").strip()[:200]
    return Assessment(score, flags, verdict)


def assess(
    client: GeminiClient, photos: list[bytes], what: str = "kitesurfing equipment"
) -> Assessment:
    """Condition from up to 4 photos. No usable photos → no score, and no quota spent."""
    usable = (img for img in map(prepare_image, photos) if img is not None)
    images = list(islice(usable, MAX_IMAGES))  # stop decoding once we have enough
    if not images:
        return Assessment(None, [], "no usable photos")
    raw = client.generate_json(
        PROMPT.format(what=what, flags=", ".join(FLAGS)), SCHEMA, images=images
    )
    result = validate(raw)
    result.photos_used = len(images)
    return result
