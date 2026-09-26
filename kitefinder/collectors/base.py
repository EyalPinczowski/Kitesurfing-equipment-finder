"""Shared pieces for collectors: the post record, results, and a polite HTTP fetcher."""

from __future__ import annotations

import hashlib
import json
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Mobile Safari/537.36"
)


class CollectorError(RuntimeError):
    """A source could not be read this run (reported, retried next run)."""


class LoginRequired(CollectorError):
    """Facebook showed a login page: the cookies are missing or expired."""


class Blocked(CollectorError):
    """The site answered with a bot-protection / captcha page."""


@dataclass
class RawPost:
    source: str  # "facebook", "yad2", or the site host
    source_id: str  # stable id within the source (post id, product URL + variation…)
    text: str
    url: str = ""
    author: str = ""
    posted_at: str | None = None  # ISO date when known
    image_urls: list[str] = field(default_factory=list)
    # structured hints from the page (shop price, size option…) — the extractor still checks
    # them against the text, so they never bypass the anti-hallucination rules
    hints: dict = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        blob = json.dumps([self.text, sorted(self.image_urls)], ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class CollectResult:
    source: str
    posts: list[RawPost] = field(default_factory=list)
    pages: int = 0
    expected_count: int | None = None  # what the page says it contains, when it says
    stopped_because: str = ""  # "last page", "reached known posts", "max pages", …
    errors: list[str] = field(default_factory=list)
    kind: str = ""  # page type the collector recognised (woocommerce, jsonld, text, group…)

    @property
    def complete(self) -> bool:
        """True unless something shows posts may be missing."""
        if self.errors:
            return False
        if self.expected_count is not None and len(self.counted_ids) < self.expected_count:
            return False
        # "first page only" (Marketplace) is partial by design: honest, but not an error
        return self.stopped_because not in ("max pages", "pagination loop", "first page only")

    @property
    def counted_ids(self) -> set[str]:
        """What the source's own count counts: products, not their size options."""
        return {p.hints.get("product_url", p.source_id) for p in self.posts}


@dataclass
class Page:
    status: int
    text: str
    url: str  # final URL after redirects


# fetch(url) -> Page. Real runs use HttpFetcher; tests pass a dict-backed fake.
Fetch = Callable[[str], Page]


class HttpFetcher:
    """requests with a mobile UA, optional cookies and a random pause between requests."""

    def __init__(
        self,
        cookies: dict | None = None,
        min_delay: float = 2.0,
        max_delay: float = 6.0,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        import requests

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": MOBILE_UA, "Accept-Language": "he,en;q=0.8"})
        if cookies:
            self.session.cookies.update(cookies)
        self.min_delay, self.max_delay = min_delay, max_delay
        self.timeout, self.sleep = timeout, sleep
        self._first = True

    def __call__(self, url: str) -> Page:
        import requests

        if not self._first:
            self.sleep(random.uniform(self.min_delay, self.max_delay))
        self._first = False
        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            raise CollectorError(f"could not fetch {url}: {e}") from e
        return Page(resp.status_code, resp.text, resp.url)


# Signs only challenge pages carry — safe to look for anywhere in the page.
BOT_WALL_STRONG = ("px-captcha", "perimeterx", "shieldsquare", "are you a robot", "cf-chl-",
                   "just a moment...", "attention required", "_incapsula_resource")  # fmt: skip
# Generic words that normal pages also contain (a shop's contact form loads reCAPTCHA):
# they only count in the page title or on a tiny page.
BOT_WALL_WEAK = ("captcha", "access denied", "blocked")
TINY_PAGE = 1500


def _is_bot_wall(text: str) -> bool:
    import re

    low = text.lower()
    if any(sign in low for sign in BOT_WALL_STRONG):
        return True
    m = re.search(r"<title[^>]*>(.*?)</title>", text[:5000], re.IGNORECASE | re.DOTALL)
    title = (m.group(1) if m else "").lower()
    if any(sign in title for sign in BOT_WALL_WEAK):
        return True
    return len(text) < TINY_PAGE and any(sign in low for sign in BOT_WALL_WEAK)


def check_page(page: Page, source: str) -> None:
    """Raise for pages that are not content: errors and bot walls."""
    if page.status in (403, 429) or _is_bot_wall(page.text):
        raise Blocked(f"{source}: blocked by bot protection (HTTP {page.status})")
    if page.status >= 400:
        raise CollectorError(f"{source}: HTTP {page.status} for {page.url}")


def absolute(base: str, href: str) -> str:
    return urljoin(base, href) if href else ""


def host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")
