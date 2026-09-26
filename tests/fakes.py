"""A fake Gemini server for offline tests."""

import json
import re


def gemini_reply(obj) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": json.dumps(obj, ensure_ascii=False)}]},
                            "finishReason": "STOP"}]}  # fmt: skip


class FakeTransport:
    """Records every request; answers with `responder(body)` → (status, json, headers)."""

    def __init__(self, responder):
        self.responder = responder
        self.requests = []

    def __call__(self, url, headers, body, timeout):
        self.requests.append({"url": url, "headers": headers, "body": body})
        return self.responder(body)


def post_text(body) -> str:
    prompt = body["contents"][0]["parts"][0]["text"]
    return re.search(r"<post>\n(.*)\n</post>", prompt, re.S).group(1)


HEBREW_BRANDS = {"Duotone": "דואוטון", "Cabrinha": "קברינה", "Ozone": "אוזון", "F-One": "f one"}


def noisy_answer(expect: dict, variant: int) -> dict:
    """What a real model might return for this post: right values, messy formats."""
    if not expect["is_sale"]:
        return {"is_sale_post": False, "not_sale_reason": "not selling", "items": []}
    items = []
    for i, want in enumerate(expect.get("items", [])):
        v = variant + i
        price = want.get("price")
        size = want.get("size")
        item = {
            "type": want["type"],
            "subtype": want.get("subtype"),
            "brand": HEBREW_BRANDS.get(want.get("brand"), want.get("brand"))
            if v % 2
            else want.get("brand"),
            "model": None,
            "size": (f"{size:g} מטר" if want["type"] == "kite" else f"{size:g}")
            if size is not None and v % 3 == 0
            else size,
            "size_label": (want.get("size_label") or "").lower() or None,
            "year": str(want["year"]) if want.get("year") and v % 2 else want.get("year"),
            "price_ils": (f"₪{price:,}" if v % 2 else f'{price} ש"ח')
            if price is not None and v % 4 < 2
            else price,
            "is_new": want.get("is_new"),
            "sold": want.get("sold", False),
            "description": "desc",
        }
        items.append(item)
    return {
        "is_sale_post": True,
        "location": expect.get("location"),
        "bundle_price_ils": expect.get("bundle_price"),
        "items": items,
    }
