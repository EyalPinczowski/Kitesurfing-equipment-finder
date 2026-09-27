"""Shop / website collector.

Understands, in order:
  1. WooCommerce category pages (product cards, "Showing 1–12 of 45 results", pagination) and
     WooCommerce product pages with size options (one post per size, with its own price)
  2. Pages with schema.org Product / ItemList data (JSON-LD), used by many shop platforms
  3. Anything else: the page's visible text becomes one post and the extractor splits it

Every product becomes a RawPost whose text states title, option, price and availability, so
the normal extraction (and its checks against the text) turns it into listings.
"""

from __future__ import annotations

import html as htmlmod
import json
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup

from ..llm import normalize as nz
from .base import CollectorError, CollectResult, Fetch, RawPost, absolute, check_page, host

MAX_PAGES = 20
MAX_TEXT = 6000
USED_WORDS = ("משומש", "יד שנייה", "יד 2", "used", "second hand", "pre-owned", "demo", "דמו")

_COUNT_PATTERNS = [
    re.compile(r"(?:of|מתוך)\s*([\d,]+)\s*(?:results|תוצאות|מוצרים)", re.IGNORECASE),
    re.compile(
        r"(?:all|כל)\s*(?:ה)?\s*([\d,]+)\s*(?:results|התוצאות|תוצאות|המוצרים)", re.IGNORECASE
    ),
    re.compile(r"(?:את\s+כל\s+)([\d,]+)", re.IGNORECASE),
]
_SINGLE = re.compile(r"single result|תוצאה אחת|התוצאה היחידה", re.IGNORECASE)


def _soup(text: str) -> BeautifulSoup:
    return BeautifulSoup(text, "html.parser")


def _clean(text: str) -> str:
    return " ".join(htmlmod.unescape(text or "").split())


def result_count(soup: BeautifulSoup) -> int | None:
    """Total products the shop says the category has."""
    node = soup.select_one(".woocommerce-result-count")
    if node is None:
        return None
    text = _clean(node.get_text(" "))
    if _SINGLE.search(text):
        return 1
    for pat in _COUNT_PATTERNS:
        m = pat.search(text)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def _price_text(node) -> str:
    """'₪7,450 (instead of ₪8,900)' for sale prices, '₪6,900 – ₪7,900' for ranges."""
    if node is None:
        return ""
    ins = node.select_one("ins")
    delete = node.select_one("del")
    if ins is not None:
        now = _clean(ins.get_text(" "))
        was = f" (instead of {_clean(delete.get_text(' '))})" if delete is not None else ""
        return f"{now}{was}"
    return _clean(node.get_text(" "))


def parse_woo_cards(soup: BeautifulSoup, base_url: str) -> list[dict]:
    cards = []
    for li in soup.select("li.product, div.product.type-product"):
        link = li.select_one(
            "a.woocommerce-LoopProduct-link, a.woocommerce-loop-product__link, a[href]"
        )
        title = li.select_one(".woocommerce-loop-product__title, h2, h3")
        if link is None or title is None:
            continue
        img = li.select_one("img")
        image = ""
        if img is not None:
            image = img.get("data-src") or img.get("src") or ""
        classes = " ".join(li.get("class", []))
        button = li.select_one("a.button, a.add_to_cart_button")
        variable = "product-type-variable" in classes or (
            button is not None and "product_type_variable" in " ".join(button.get("class", []))
        )
        cards.append(
            {
                "url": absolute(base_url, link.get("href", "")),
                "title": _clean(title.get_text(" ")),
                "price": _price_text(li.select_one(".price")),
                "image": absolute(base_url, image) if image else "",
                "variable": variable,
                "out_of_stock": "outofstock" in classes,
            }
        )
    return cards


def next_page(soup: BeautifulSoup, base_url: str) -> str:
    for sel in ("a.next.page-numbers", "link[rel=next]", "a[rel=next]", ".pagination a.next"):
        node = soup.select_one(sel)
        if node is not None and node.get("href"):
            return absolute(base_url, node["href"])
    return ""


def _option_label(attrs: dict) -> str:
    parts = []
    for key, value in attrs.items():
        name = unquote(key.removeprefix("attribute_").removeprefix("pa_")).replace("-", " ")
        parts.append(f"{name}: {unquote(str(value)).replace('-', ' ')}")
    return ", ".join(parts)


def parse_variations(page_html: str) -> list[dict]:
    """WooCommerce size/colour options from a product page (data-product_variations)."""
    soup = _soup(page_html)
    form = soup.select_one("form.variations_form")
    if form is None or not form.get("data-product_variations"):
        return []
    try:
        data = json.loads(htmlmod.unescape(form["data-product_variations"]))
    except (ValueError, TypeError):
        return []
    out = []
    for v in data if isinstance(data, list) else []:
        out.append(
            {
                "id": v.get("variation_id"),
                "option": _option_label(v.get("attributes") or {}),
                "price": v.get("display_price"),
                "in_stock": bool(v.get("is_in_stock", True)),
                "image": ((v.get("image") or {}).get("src")) or "",
            }
        )
    return out


def short_description(page_html: str) -> str:
    soup = _soup(page_html)
    node = soup.select_one(
        ".woocommerce-product-details__short-description, .product-short-description"
    )
    return _clean(node.get_text(" "))[:600] if node is not None else ""


def parse_jsonld_products(soup: BeautifulSoup, base_url: str) -> list[dict]:
    """schema.org Product / ItemList entries embedded as JSON-LD."""
    found = []

    def visit(obj):
        if isinstance(obj, list):
            for x in obj:
                visit(x)
            return
        if not isinstance(obj, dict):
            return
        kind = obj.get("@type")
        kinds = kind if isinstance(kind, list) else [kind]
        if "Product" in kinds:
            offers = obj.get("offers") or {}
            offer_list = offers if isinstance(offers, list) else [offers]
            for offer in offer_list or [{}]:
                price = offer.get("price") or offer.get("lowPrice")
                available = "OutOfStock" not in str(offer.get("availability", ""))
                image = obj.get("image")
                if isinstance(image, list):
                    image = image[0] if image else ""
                if isinstance(image, dict):
                    image = image.get("url", "")
                found.append(
                    {
                        "url": absolute(base_url, offer.get("url") or obj.get("url") or base_url),
                        "title": _clean(str(obj.get("name", ""))),
                        "price": f"₪{price}" if price not in (None, "") else "",
                        "image": absolute(base_url, image) if image else "",
                        "in_stock": available,
                        "sku": str(offer.get("sku") or obj.get("sku") or ""),
                    }
                )
        for key in ("itemListElement", "item", "@graph"):
            if key in obj:
                visit(obj[key])

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            visit(json.loads(script.string or ""))
        except ValueError:
            continue
    return [p for p in found if p["title"]]


def visible_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "svg"]):
        tag.decompose()
    return _clean(soup.get_text(" "))


def _is_used(title: str) -> bool:
    return nz.has_any(title.lower(), USED_WORDS)


def _post_text(title: str, option: str, price: str, in_stock: bool, extra: str = "") -> str:
    parts = [title]
    if option:
        parts.append(option)
    if price:
        parts.append(f"מחיר: {price}")
    if not _is_used(title):
        parts.append("(חדש)")  # a shop item is new unless it says otherwise
    if not in_stock:
        parts.append("אזל מהמלאי / sold out")
    if extra:
        parts.append(extra)
    return " | ".join(parts)


def collect_site(
    url: str, fetch: Fetch, max_pages: int = MAX_PAGES, details: bool = True
) -> CollectResult:
    """All products under a shop URL, following pagination."""
    source = host(url)
    result = CollectResult(source)
    seen_pages: set[str] = set()
    page_url = url
    kind = ""
    while page_url:
        if page_url in seen_pages:
            result.stopped_because = "pagination loop"
            break
        if result.pages >= max_pages:
            result.stopped_because = "max pages"
            break
        seen_pages.add(page_url)
        try:
            page = fetch(page_url)
            check_page(page, source)
        except CollectorError as e:
            result.errors.append(str(e))
            break
        result.pages += 1
        soup = _soup(page.text)
        if result.expected_count is None:
            result.expected_count = result_count(soup)
        cards = parse_woo_cards(soup, page.url)
        if cards:
            kind = "woocommerce"
            for card in cards:
                result.posts.extend(_card_posts(card, fetch, source, details, result))
        else:
            products = parse_jsonld_products(soup, page.url)
            if products:
                kind = "jsonld"
                for p in products:
                    sid = p["url"] + (f"#{p['sku']}" if p["sku"] else "")
                    text = _post_text(p["title"], "", p["price"], p["in_stock"])
                    images = [p["image"]] if p["image"] else []
                    result.posts.append(
                        RawPost(source, sid, text, url=p["url"], author=source,
                                image_urls=images, hints={"single_item": True})
                    )  # fmt: skip
            elif result.pages == 1:
                kind = "text"
                text = visible_text(soup)[:MAX_TEXT]
                if text:
                    result.posts.append(
                        RawPost(source, page.url, text, url=page.url, author=source)
                    )
        page_url = next_page(soup, page.url) if kind != "text" else ""
        if not page_url and not result.stopped_because:
            result.stopped_because = "last page"
    result.kind = kind
    return result


def _card_posts(card: dict, fetch: Fetch, source: str, details: bool, result: CollectResult):
    """One post per product, or one per size option for variable products."""
    images = [card["image"]] if card["image"] else []
    if card["variable"] and details:
        try:
            page = fetch(card["url"])
            check_page(page, source)
        except CollectorError as e:
            result.errors.append(f"{card['title']}: {e}")
            page = None
        variations = parse_variations(page.text) if page is not None else []
        if variations:
            desc = short_description(page.text)
            posts = []
            for i, v in enumerate(variations):
                price = f"₪{v['price']:,}" if isinstance(v["price"], int | float) else ""
                # a theme may leave out variation ids: fall back to the option itself
                key = f"v{v['id']}" if v["id"] else f"o{i}-{v['option']}"
                posts.append(
                    RawPost(
                        source,
                        f"{card['url']}#{key}",
                        _post_text(card["title"], v["option"], price, v["in_stock"], desc),
                        url=card["url"],
                        author=source,
                        image_urls=[v["image"]] if v["image"] else images,
                        hints={"product_url": card["url"], "single_item": True},
                    )
                )
            return posts
    return [
        RawPost(
            source,
            card["url"],
            _post_text(card["title"], "", card["price"], not card["out_of_stock"]),
            url=card["url"],
            author=source,
            image_urls=images,
            hints={"product_url": card["url"], "single_item": True},
        )
    ]
