"""A small fake world for pipeline tests: one shop, Yad2, a Facebook group and Marketplace,
all served from the saved sample pages (see tests/fixtures/pages)."""

from pathlib import Path

from kitefinder.collectors.base import Page
from kitefinder.config import load_settings
from kitefinder.pipeline import Fetchers

PAGES = Path(__file__).parent / "fixtures" / "pages"
SHOP = "https://shop.example.co.il"
CATEGORY = f"{SHOP}/product-category/kites/"
GROUP = "https://www.facebook.com/groups/123456/"
YAD2_URL = "https://y2.example/?q={query}&p={page}"
MARKET_URL = ("https://www.facebook.com/marketplace/telaviv/search?query=kite"
              "&sortBy=creation_time_descend&exact=false")  # fmt: skip

ROUTES = {
    CATEGORY: "woo_cat_p1.html",
    f"{SHOP}/product-category/kites/page/2/": "woo_cat_p2.html",
    f"{SHOP}/product/duotone-rebel-sls-2024/": "woo_product_rebel.html",
    "https://y2.example/?q=%D7%A7%D7%99%D7%99%D7%98&p=1": "yad2_p1.html",
    "https://y2.example/?q=%D7%A7%D7%99%D7%99%D7%98&p=2": "yad2_p2.html",
    "https://m.facebook.com/groups/123456/": "fb_group_p1.html",
    "https://m.facebook.com/groups/123456/?bacr=1690000000%3A1003&refid=18": "fb_group_p2.html",
    MARKET_URL: "fb_marketplace.html",
}


class World:
    """Serves pages; tests can replace a page's text (an edited post) or break a URL."""

    def __init__(self):
        self.texts = {
            url: (PAGES / name).read_text(encoding="utf-8") for url, name in ROUTES.items()
        }
        self.calls: list[str] = []
        self.photos: list[str] = []

    def fetch(self, url):
        self.calls.append(url)
        if url not in self.texts:
            return Page(404, "not found", url)
        return Page(200, self.texts[url], url)

    def photo(self, url):
        import io

        from PIL import Image

        self.photos.append(url)
        out = io.BytesIO()
        Image.new("RGB", (60, 40), (10, 120, 200)).save(out, "JPEG")
        return out.getvalue()

    def settings(self, root=None):
        s = load_settings(root or Path(__file__).parent.parent, env={})
        s.sources = {
            "yad2": {"queries": ["קייט"], "search_url": YAD2_URL},
            "facebook": {"groups": [GROUP], "marketplace_queries": ["kite"]},
        }
        return s

    def fetchers(self, settings=None):
        return Fetchers(settings or self.settings(), overrides={
            "web": self.fetch, "yad2": self.fetch, "facebook": self.fetch, "photo": self.photo})  # fmt: skip
