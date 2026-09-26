"""Facebook collector: groups (mobile pages) and Marketplace search, using your cookies.

There is no free official API for group posts or Marketplace, so this reads the pages the
logged-in mobile site returns, slowly, with the cookies you export from your browser. When
Facebook shows a login page instead, the run stops with LoginRequired so the bot can tell
you to refresh the cookies. When a page has no posts at all, it reports that the page
format may have changed instead of silently returning nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from .base import (
    CollectorError,
    CollectResult,
    Fetch,
    LoginRequired,
    RawPost,
    absolute,
    check_page,
)

SOURCE = "facebook"
MOBILE = "https://m.facebook.com"
MAX_GROUP_PAGES = 5
KNOWN_STREAK_STOP = 10  # this many already-known posts in a row: we've caught up
MORE_POSTS_TEXT = ("see more posts", "more posts", "הצג פוסטים נוספים", "ראה פוסטים נוספים",
                   "עוד פוסטים", "הצגת פוסטים נוספים")  # fmt: skip
LOGIN_SIGNS = ('id="login_form"', "log in to facebook", "התחבר/י לפייסבוק", "התחברות לפייסבוק",
               'name="login"', "/login/?next=")  # fmt: skip


# --- cookies ----------------------------------------------------------------------------------


def load_cookies(path: Path | str) -> dict[str, str]:
    """Cookies exported from a browser: a JSON list (Cookie-Editor / EditThisCookie), a JSON
    {name: value} object, or a Netscape cookies.txt. Only facebook.com cookies are kept."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise LoginRequired(f"no Facebook cookies file at {path}")
    raw = path.read_text(encoding="utf-8").strip()
    cookies: dict[str, str] = {}
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if isinstance(data, list):
        for c in data:
            if isinstance(c, dict) and "facebook.com" in str(c.get("domain", "facebook.com")):
                if c.get("name") and c.get("value") is not None:
                    cookies[str(c["name"])] = str(c["value"])
    elif isinstance(data, dict):
        cookies = {str(k): str(v) for k, v in data.items()}
    else:  # Netscape format: domain \t flag \t path \t secure \t expiry \t name \t value
        for line in raw.splitlines():
            line = line.removeprefix("#HttpOnly_")  # HttpOnly cookies (xs!) are written like this
            parts = line.split("\t")
            if len(parts) == 7 and not line.startswith("#") and "facebook.com" in parts[0]:
                cookies[parts[5]] = parts[6]
    missing = [k for k in ("c_user", "xs") if k not in cookies]
    if missing:
        raise LoginRequired(
            f"the cookies file has no {', '.join(missing)} — export them again while logged in"
        )
    return cookies


# --- groups -----------------------------------------------------------------------------------

_GROUP_PATH = re.compile(r"/groups/([^/?#]+)")
_POST_ID_PATTERNS = [
    re.compile(r"/permalink/(\d+)"),
    re.compile(r"/posts/(\d+)"),
    re.compile(r"story_fbid=(\d+)"),
    re.compile(r"multi_permalinks=(\d+)"),
]


def group_mobile_url(url: str) -> str | None:
    """https://www.facebook.com/groups/123/?ref=… → https://m.facebook.com/groups/123/"""
    m = _GROUP_PATH.search(urlsplit(url).path)
    return f"{MOBILE}/groups/{m.group(1)}/" if m else None


def resolve_group(url: str, fetch: Fetch) -> str:
    """A group URL for the mobile site; share links (facebook.com/share/g/…) are followed."""
    direct = group_mobile_url(url)
    if direct:
        return direct
    page = fetch(url)
    check_page(page, SOURCE)
    _check_login(page.text)
    for candidate in (page.url, *_meta_urls(page.text)):
        found = group_mobile_url(candidate or "")
        if found:
            return found
    raise CollectorError(f"could not find the group behind {url}")


def _meta_urls(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for sel, attr in (('meta[property="og:url"]', "content"), ('link[rel="canonical"]', "href"),
                      ('meta[http-equiv="refresh"]', "content")):  # fmt: skip
        node = soup.select_one(sel)
        if node is not None and node.get(attr):
            out.append(node[attr].split("url=")[-1].split("URL=")[-1])
    return out


def _check_login(html: str) -> None:
    low = html[:30000].lower()
    if any(sign.lower() in low for sign in LOGIN_SIGNS) and "<article" not in low:
        raise LoginRequired("Facebook asked to log in — refresh the cookies file")


def _post_id(node) -> str | None:
    ft = node.get("data-ft")
    if ft:
        try:
            data = json.loads(ft)
            for key in ("top_level_post_id", "mf_story_key", "tl_objid"):
                if data.get(key):
                    return str(data[key]).split(":")[-1]
        except ValueError:
            pass
    for a in node.select("a[href]"):
        for pat in _POST_ID_PATTERNS:
            m = pat.search(a["href"])
            if m:
                return m.group(1)
    return None


def _post_text(node) -> str:
    body = node.select_one(".story_body_container, [data-gt]") or node
    paragraphs = [p.get_text(" ", strip=True) for p in body.select("p")]
    text = "\n".join(p for p in paragraphs if p)
    if not text:
        text = body.get_text(" ", strip=True)
    # the "…See more" link, not an ordinary trailing "עוד" (more / still)
    text = re.sub(r"\s*(?:…|\.\.\.)\s*(?:See more|עוד|הצג עוד|ראה עוד)\s*$", "", text)
    return re.sub(r"\s+(?:See more|See More|הצג עוד|ראה עוד)\s*$", "", text).strip()


def _author(node) -> str:
    for sel in ("h3 a", "header a", "strong a", "h3", "strong"):
        found = node.select_one(sel)
        if found is not None and found.get_text(strip=True):
            return found.get_text(" ", strip=True)
    return ""


def _images(node, base: str) -> list[str]:
    urls = []
    for img in node.select("img[src]"):
        src = img["src"]
        # photos come from scontent CDNs; static.xx.fbcdn.net/rsrc.php serves icons and emoji
        if ("scontent" in src or "fbcdn" in src) and "static." not in src and "rsrc.php" not in src:
            urls.append(absolute(base, src))
    return list(dict.fromkeys(urls))


def parse_group_page(html: str, base_url: str) -> tuple[list[RawPost], str]:
    """(posts, next page URL) from one mobile group feed page."""
    _check_login(html)
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.select("article") or soup.select("div[data-ft]")
    posts = []
    for node in nodes:
        pid = _post_id(node)
        text = _post_text(node)
        if not pid or not text:
            continue
        time_node = node.select_one("abbr")
        posts.append(
            RawPost(
                SOURCE,
                pid,
                text,
                url=f"https://www.facebook.com/{pid}",
                author=_author(node),
                image_urls=_images(node, base_url),
                hints={"time_text": time_node.get_text(strip=True) if time_node else ""},
            )
        )
    nxt = ""
    for a in soup.select("a[href]"):
        label = a.get_text(" ", strip=True).lower()
        if any(t in label for t in MORE_POSTS_TEXT) or "bacr=" in a["href"]:
            nxt = absolute(base_url, a["href"])
            break
    return posts, nxt


def collect_group(url: str, fetch: Fetch, known_ids: set[str] = frozenset(),
                  max_pages: int = MAX_GROUP_PAGES) -> CollectResult:  # fmt: skip
    """New posts of a group, newest pages first, until we reach posts we already have."""
    result = CollectResult(SOURCE, kind="group")
    try:
        page_url = resolve_group(url, fetch)
    except CollectorError as e:
        result.errors.append(str(e))
        return result
    streak = 0
    seen_pages: set[str] = set()
    seen_posts: set[str] = set()
    while page_url:
        if page_url in seen_pages:
            result.stopped_because = "pagination loop"
            return result
        if result.pages >= max_pages:
            result.stopped_because = "max pages"
            return result
        seen_pages.add(page_url)
        try:
            page = fetch(page_url)
            check_page(page, SOURCE)
            posts, page_url = parse_group_page(page.text, page.url)
        except CollectorError as e:
            result.errors.append(str(e))
            return result
        result.pages += 1
        if not posts and result.pages == 1:
            result.errors.append(
                f"facebook: no posts found in {url} — Facebook may have changed its page format"
            )
            return result
        for post in posts:
            if post.source_id in seen_posts:
                continue
            seen_posts.add(post.source_id)
            result.posts.append(post)
            streak = streak + 1 if post.source_id in known_ids else 0
        if streak >= KNOWN_STREAK_STOP:
            result.stopped_because = "reached known posts"
            return result
    result.stopped_because = "last page"
    return result


# --- marketplace ------------------------------------------------------------------------------

NO_RESULTS_MARKERS = ("no listings found", "no results", "לא נמצאו", "אין תוצאות",
                      "there are no listings")  # fmt: skip
MARKETPLACE_SEARCH = (
    "https://www.facebook.com/marketplace/{location}/search?query={query}"
    "&sortBy=creation_time_descend&exact=false"
)


def _walk(obj, found: dict):
    if isinstance(obj, dict):
        if "marketplace_listing_title" in obj and obj.get("id"):
            found.setdefault(str(obj["id"]), obj)
        for v in obj.values():
            _walk(v, found)
    elif isinstance(obj, list):
        for v in obj:
            _walk(v, found)


def _json_blobs(html: str):
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.select("script"):
        body = (script.string or "").strip()
        if "marketplace_listing_title" not in body:
            continue
        try:
            yield json.loads(body)
        except ValueError:
            continue


def parse_marketplace(html: str) -> list[RawPost]:
    _check_login(html)
    found: dict[str, dict] = {}
    for blob in _json_blobs(html):
        _walk(blob, found)
    posts = []
    for item_id, item in found.items():
        title = str(item.get("marketplace_listing_title") or "")
        price = item.get("listing_price") or {}
        amount = price.get("amount") or price.get("formatted_amount") or ""
        geo = (item.get("location") or {}).get("reverse_geocode") or {}
        city = str(geo.get("city") or "")
        photo = (((item.get("primary_listing_photo") or {}).get("image") or {}).get("uri")) or ""
        sold = bool(item.get("is_sold") or item.get("is_pending"))
        parts = [title]
        if amount:
            try:
                parts.append(f"מחיר: {int(float(str(amount).replace(',', '')))} ₪")
            except ValueError:
                parts.append(f"מחיר: {amount}")
        if city:
            parts.append(city)
        if sold:
            parts.append("נמכר / sold")
        posts.append(
            RawPost(
                SOURCE,
                f"mp-{item_id}",
                " | ".join(parts),
                url=f"https://www.facebook.com/marketplace/item/{item_id}/",
                image_urls=[photo] if photo else [],
                hints={"marketplace": True, "city": city, "single_item": True},
            )
        )
    return posts


def collect_marketplace(query: str, fetch: Fetch, location: str = "telaviv") -> CollectResult:
    """Newest Marketplace listings for one query (first results page only — Facebook loads
    the rest with scripts, so frequent runs with several queries keep coverage up)."""
    result = CollectResult(SOURCE, kind="marketplace")
    url = MARKETPLACE_SEARCH.format(location=location, query=quote(query))
    try:
        page = fetch(url)
        check_page(page, SOURCE)
        result.posts = parse_marketplace(page.text)
    except CollectorError as e:
        result.errors.append(str(e))
        return result
    result.pages = 1
    if not result.posts and not any(m in page.text.lower() for m in NO_RESULTS_MARKERS):
        result.errors.append(
            "facebook marketplace: no listings found — the page format may have changed"
        )
    result.stopped_because = "first page only"
    return result
