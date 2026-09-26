"""Cheap first pass before the LLM: drop posts that clearly aren't about gear.

Tuned for recall — a post is only rejected when nothing in it points at kitesurfing gear
(no gear word, no brand, no model name, no kite size). Rejections are stored with a reason,
never silently dropped, so the run ledger can account for every post.
"""

from __future__ import annotations

from dataclasses import dataclass

from .llm import normalize as nz
from .llm import rules

_BRANDS = [p for p, _ in rules._BRANDS]
_MODELS = [p for p, _ in rules._MODEL_PATTERNS]
GEAR_CONTEXT = ("kitesurf", "קייטסרפ", "kiteboard", "קייטבורד", "wing foil", "ווינג")


@dataclass
class Decision:
    keep: bool
    reason: str


def check(text: str) -> Decision:
    t = text or ""
    if not t.strip():
        return Decision(False, "empty post")
    if nz.find_types(t):
        return Decision(True, "gear word")
    if any(p.search(t) for p in _BRANDS):
        return Decision(True, "brand")
    if rules._KITE_SIZE.search(t) or rules._BOARD_DIMS.search(t):
        return Decision(True, "gear size")
    if any(p.search(t) for p in _MODELS):
        return Decision(True, "model name")
    if nz.has_any(t.lower(), GEAR_CONTEXT):
        return Decision(True, "kitesurf wording")
    return Decision(False, "no gear word, brand, model or size")
