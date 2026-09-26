"""Yad2 collector.

Yad2 pages are Next.js apps: the search results are embedded as JSON in
<script id="__NEXT_DATA__">. The exact field names are not documented and change, so the
parser looks for *any* list of objects that have an id, a title and a price, instead of a
fixed path. If Yad2 shows its bot-protection page, the run reports "blocked".
"""

from __future__ import annotations

import json
from urllib.parse import quote

from bs4 import BeautifulSoup

from .base import CollectorError, CollectResult, Fetch, RawPost, check_page

SOURCE = "yad2"
# Search page for a free-text query in Yad2's second-hand products board. Configurable in
# sources.yaml (yad2.search_url) because Yad2 changes its URLs from time to time.
DEFAULT_SEARCH_URL = "https://www.yad2.co.il/products/all?text={query}&page={page}"
MAX_PAGES = 10

_ID_KEYS = ("token", "adNumber", "id", "orderId", "itemId")
_TITLE_KEYS = ("title", "name", "title1", "heading", "productName")
_PRICE_KEYS = ("price", "priceValue", "adPrice")
_TEXT_KEYS = ("description", "info", "text", "subtitle", "title2")
_TOTAL_KEYS = ("totalItems", "totalCount", "itemsCount", "totalAds")


def _first(d: dict, keys) -> object:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _as_text(v) -> str:
    if isinstance(v, dict):
        return str(_first(v, ("text", "textHeb", "name", "value")) or "")
    return str(v) if v is not None else ""


def _city(item: dict) -> str:
    for key in ("city", "cityName", "area", "neighborhood"):
        if key in item:
            return _as_text(item[key])
    addr = item.get("address")
    if isinstance(addr, dict):
        for key in ("city", "area"):
            if key in addr:
                return _as_text(addr[key])
    return ""


def _images(item: dict) -> list[str]:
    out = []
    for key in ("images", "imagesUrls", "media", "coverImage", "image", "photos"):
        v = item.get(key)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            for x in v:
                if isinstance(x, str):
                    out.append(x)
                elif isinstance(x, dict) and _first(x, ("url", "src")):
                    out.append(str(_first(x, ("url", "src"))))
        elif isinstance(v, dict) and _first(v, ("url", "src")):
            out.append(str(_first(v, ("url", "src"))))
    meta = item.get("metaData")
    if isinstance(meta, dict):
        out.extend(_images(meta))
    return [u for u in dict.fromkeys(out) if u.startswith("http")]


def _looks_like_ad(d: dict) -> bool:
    return (
        _first(d, _ID_KEYS) is not None
        and _first(d, _TITLE_KEYS) is not None
        and any(k in d for k in _PRICE_KEYS)
    )


def find_ads(data) -> tuple[list[dict], int | None]:
    """All ad-like objects anywhere in the page JSON, and a total count if one is given."""
    ads: dict[str, dict] = {}
    total = None

    def visit(obj):
        nonlocal total
        if isinstance(obj, dict):
            if _looks_like_ad(obj):
                ads.setdefault(str(_first(obj, _ID_KEYS)), obj)
            # a total only counts when it sits next to the list of ads it counts
            holds_ads = any(
                isinstance(v, list) and any(isinstance(x, dict) and _looks_like_ad(x) for x in v)
                for v in obj.values()
            )
            for k in _TOTAL_KEYS:
                value = obj.get(k)
                if (
                    holds_ads
                    and total is None
                    and isinstance(value, int)
                    and not isinstance(value, bool)
                ):
                    total = value
            for v in obj.values():
                visit(v)
        elif isinstance(obj, list):
            for v in obj:
                visit(v)

    visit(data)
    return list(ads.values()), total


def next_data(html: str) -> dict | None:
    node = BeautifulSoup(html, "html.parser").select_one("script#__NEXT_DATA__")
    if node is None or not node.string:
        return None
    try:
        return json.loads(node.string)
    except ValueError:
        return None


def ad_to_post(ad: dict) -> RawPost:
    ad_id = str(_first(ad, _ID_KEYS))
    title = _as_text(_first(ad, _TITLE_KEYS))
    body = " ".join(_as_text(ad.get(k)) for k in _TEXT_KEYS if ad.get(k))
    price = ad.get("price") if "price" in ad else _first(ad, _PRICE_KEYS)
    price_text = f"מחיר: {price} ₪" if isinstance(price, int | float) and price > 0 else ""
    city = _city(ad)
    text = " | ".join(x for x in (title, body, price_text, city) if x)
    url = f"https://www.yad2.co.il/item/{ad_id}"
    return RawPost(SOURCE, ad_id, text, url=url, image_urls=_images(ad), hints={"city": city})


def collect_query(query: str, fetch: Fetch, search_url: str = DEFAULT_SEARCH_URL,
                  max_pages: int = MAX_PAGES) -> CollectResult:  # fmt: skip
    """Every ad for one search query, page by page until a page adds nothing new."""
    result = CollectResult(SOURCE, kind="yad2")
    seen: set[str] = set()
    for page_no in range(1, max_pages + 1):
        url = search_url.format(query=quote(query), page=page_no)
        try:
            page = fetch(url)
            check_page(page, SOURCE)
        except CollectorError as e:
            result.errors.append(str(e))
            return result
        result.pages += 1
        data = next_data(page.text)
        if data is None:
            result.errors.append(
                f"yad2: no result data on {url} — the page format may have changed"
            )
            return result
        ads, total = find_ads(data)
        if result.expected_count is None and total is not None:
            result.expected_count = total
        fresh = [a for a in ads if str(_first(a, _ID_KEYS)) not in seen]
        if not fresh:
            result.stopped_because = "last page"
            return result
        for ad in fresh:
            seen.add(str(_first(ad, _ID_KEYS)))
            result.posts.append(ad_to_post(ad))
        if total is not None and len(seen) >= total:
            result.stopped_because = "last page"
            return result
    result.stopped_because = "max pages"
    return result
