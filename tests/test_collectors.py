"""Family 3 (nothing missed): collectors on saved pages must return every post exactly once,
say why they stopped, and flag anything that suggests posts may be missing."""

from pathlib import Path

import pytest
from corpus import CORPUS

from kitefinder import prefilter
from kitefinder.collectors import base, web, yad2
from kitefinder.collectors import facebook as fb
from kitefinder.collectors.base import Blocked, LoginRequired, Page, RawPost

PAGES = Path(__file__).parent / "fixtures" / "pages"
SHOP = "https://shop.example.co.il"
CAT = f"{SHOP}/product-category/kites/"
CAT2 = f"{SHOP}/product-category/kites/page/2/"
REBEL = f"{SHOP}/product/duotone-rebel-sls-2024/"
GROUP = "https://m.facebook.com/groups/123456/"
GROUP2 = "https://m.facebook.com/groups/123456/?bacr=1690000000%3A1003&refid=18"


def read(name):
    return (PAGES / name).read_text(encoding="utf-8")


class Fetch:
    """Serves saved pages by URL and records what was requested."""

    def __init__(self, routes, status=None):
        self.routes, self.status, self.calls = routes, status or {}, []

    def __call__(self, url):
        self.calls.append(url)
        if url not in self.routes:
            return Page(404, "not found", url)
        target = self.routes[url]
        final = url
        if isinstance(target, tuple):  # (file, final URL after redirect)
            target, final = target
        return Page(self.status.get(url, 200), read(target), final)


SHOP_ROUTES = {CAT: "woo_cat_p1.html", CAT2: "woo_cat_p2.html", REBEL: "woo_product_rebel.html"}


# --- shops ------------------------------------------------------------------------------------


def test_shop_collects_every_product_and_size_option():
    f = Fetch(SHOP_ROUTES)
    r = web.collect_site(CAT, f)
    assert (r.kind, r.pages, r.expected_count, r.stopped_because) == (
        "woocommerce",
        2,
        5,
        "last page",
    )
    assert len(r.counted_ids) == 5 and r.complete and r.errors == []
    ids = [p.source_id for p in r.posts]
    assert len(ids) == len(set(ids)) == 7  # 4 simple products + 3 Rebel sizes
    assert f.calls == [CAT, REBEL, CAT2]
    texts = {p.source_id: p.text for p in r.posts}
    assert (
        texts[f"{SHOP}/product/north-orbit-2023/"]
        == 'North Orbit 2023 12 מ"ר | מחיר: 6,900 ₪ (instead of 8,900 ₪) | (חדש)'
    )
    assert texts[f"{REBEL}#v9001"].startswith(
        "Duotone Rebel SLS 2024 | גודל: 9 מטר | מחיר: ₪7,450 | (חדש)"
    )
    assert "אזל מהמלאי" in texts[f"{REBEL}#v9003"]  # out-of-stock size is kept, marked
    assert "אזל מהמלאי" in texts[f"{SHOP}/product/north-atmos-2023/"]
    used = texts[f"{SHOP}/product/used-cabrinha-switchblade-10/"]
    assert "(חדש)" not in used  # a used item in a shop is not marked new
    assert all(p.hints["single_item"] and p.author == "shop.example.co.il" for p in r.posts)
    assert r.posts[1].image_urls == [f"{SHOP}/img/rebel9.jpg"]


def test_shop_missing_product_is_detected():
    routes = dict(SHOP_ROUTES)
    routes[CAT2] = "woo_cat_p1.html"  # page 2 wrongly repeats page 1: 2 products never seen
    r = web.collect_site(CAT, Fetch(routes))
    assert r.stopped_because == "pagination loop" and not r.complete


def test_shop_count_mismatch_makes_result_incomplete():
    r = web.collect_site(CAT, Fetch({CAT: "woo_cat_p1.html", REBEL: "woo_product_rebel.html"}))
    assert r.errors and not r.complete  # page 2 404 → error; and 3 of 5 products
    assert len(r.counted_ids) == 3 and r.expected_count == 5


def test_shop_max_pages_is_not_complete():
    r = web.collect_site(CAT, Fetch(SHOP_ROUTES), max_pages=1)
    assert r.stopped_because == "max pages" and not r.complete


def test_shop_detail_page_failure_falls_back_to_card():
    r = web.collect_site(CAT, Fetch({CAT: "woo_cat_p1.html", CAT2: "woo_cat_p2.html"}))
    rebel = [p for p in r.posts if "rebel" in p.source_id]
    assert [p.source_id for p in rebel] == [REBEL]  # the card itself, with its price range
    assert "7,450" in rebel[0].text and any("Rebel" in e for e in r.errors)


def test_shop_without_details():
    f = Fetch(SHOP_ROUTES)
    r = web.collect_site(CAT, f, details=False)
    assert REBEL not in f.calls and len(r.posts) == 5


def test_jsonld_shop():
    url = "https://other.example.com/kites"
    r = web.collect_site(url, Fetch({url: "jsonld_list.html"}))
    assert r.kind == "jsonld" and [p.source_id for p in r.posts] == [
        "https://other.example.com/p/bandit-12#FB25-12",
        "https://other.example.com/p/linx",
    ]
    assert r.posts[0].text == "F-One Bandit 2025 12m | מחיר: ₪7890 | (חדש)"
    assert "sold out" in r.posts[1].text and r.posts[0].image_urls == [
        "https://other.example.com/b.jpg"
    ]


def test_plain_text_page_becomes_one_post_without_scripts_or_menus():
    url = "https://used.example.com/gear"
    r = web.collect_site(url, Fetch({url: "text_gear_page.html"}))
    (post,) = r.posts
    assert r.kind == "text" and "Ozone Enduro 10 מטר" in post.text and "Nobile 138x42" in post.text
    assert "ignore me" not in post.text and "צור קשר" not in post.text and "טלפון" not in post.text


@pytest.mark.parametrize(
    "text, n",
    [("Showing 1–12 of 45 results", 45), ("מציג 1–12 מתוך 1,045 תוצאות", 1045), ("Showing all 7 results", 7),
     ("מציג את כל 7 התוצאות", 7), ("Showing the single result", 1), ("מציג תוצאה אחת", 1), ("sorted", None)],
)  # fmt: skip
def test_result_count_variants(text, n):
    soup = web._soup(f'<p class="woocommerce-result-count">{text}</p>')
    assert web.result_count(soup) == n
    assert web.result_count(web._soup("<p>no count</p>")) is None


@pytest.mark.parametrize("page", ["yad2_captcha.html"])
def test_bot_wall_is_an_error_not_an_empty_shop(page):
    r = web.collect_site(CAT, Fetch({CAT: page}))
    assert r.posts == [] and "bot protection" in r.errors[0] and not r.complete


# --- yad2 -------------------------------------------------------------------------------------


def yad2_fetch(pages):
    return lambda url: Page(200, read(pages[int(url.split("page=")[1])]), url)


def test_yad2_all_pages_deduplicated_with_total():
    r = yad2.collect_query("קייט", yad2_fetch({1: "yad2_p1.html", 2: "yad2_p2.html"}))
    assert [p.source_id for p in r.posts] == ["a1b2", "c3d4", "e5f6", "g7h8"]
    assert (r.expected_count, r.stopped_because, r.complete) == (4, "last page", True)
    first = r.posts[0]
    assert first.text == "קייט North Orbit 12 מטר | שנת 2021, מצב מצוין | מחיר: 3200 ₪ | הרצליה"
    assert first.url == "https://www.yad2.co.il/item/a1b2" and first.image_urls == [
        "https://img.yad2.co.il/1.jpg"
    ]


def test_yad2_without_total_stops_when_a_page_adds_nothing():
    r = yad2.collect_query("q", yad2_fetch({1: "yad2_p1_nototal.html", 2: "yad2_p1_nototal.html"}))
    assert (len(r.posts), r.pages, r.stopped_because) == (3, 2, "last page")


def test_yad2_blocked_and_format_change():
    r = yad2.collect_query("q", yad2_fetch({1: "yad2_captcha.html"}))
    assert "bot protection" in r.errors[0] and not r.complete
    r = yad2.collect_query("q", yad2_fetch({1: "text_gear_page.html"}))
    assert "page format may have changed" in r.errors[0]


def test_yad2_max_pages():
    r = yad2.collect_query(
        "q",
        yad2_fetch({i: "yad2_p1_nototal.html" if i == 1 else "yad2_p2.html" for i in range(1, 9)}),
        max_pages=1,
    )
    assert r.stopped_because == "max pages" and not r.complete


def test_yad2_ad_shapes():
    ads, total = yad2.find_ads({"a": [{"id": 5, "name": "בר", "priceValue": 900, "city": "חיפה",
                                       "images": [{"src": "https://i/1.jpg"}, "https://i/2.jpg"]}]})  # fmt: skip
    post = yad2.ad_to_post(ads[0])
    assert (total, post.source_id, post.text) == (
        None,
        "5",
        "בר | מחיר: 900 ₪ | חיפה",
    )  # no 'price' key → no price text
    assert post.image_urls == ["https://i/1.jpg", "https://i/2.jpg"]


# --- facebook ---------------------------------------------------------------------------------


def test_cookies_formats(tmp_path):
    assert fb.load_cookies(PAGES / "fb_cookies_editor.json") == {
        "c_user": "100001",
        "xs": "12%3Aabc",
        "datr": "d",
    }
    assert fb.load_cookies(PAGES / "fb_cookies.txt") == {"c_user": "100001", "xs": "abc"}
    d = tmp_path / "d.json"
    d.write_text('{"c_user": "1", "xs": "2"}')
    assert fb.load_cookies(d) == {"c_user": "1", "xs": "2"}
    bad = tmp_path / "bad.json"
    bad.write_text('[{"domain": ".facebook.com", "name": "datr", "value": "x"}]')
    with pytest.raises(LoginRequired, match="no c_user, xs"):
        fb.load_cookies(bad)
    with pytest.raises(LoginRequired, match="no Facebook cookies file"):
        fb.load_cookies(tmp_path / "missing.json")


def test_resolve_group_urls_and_share_links():
    assert fb.group_mobile_url("https://www.facebook.com/groups/123456/?ref=share") == GROUP
    assert (
        fb.resolve_group("https://facebook.com/groups/kiteil/about", Fetch({}))
        == "https://m.facebook.com/groups/kiteil/"
    )
    share = "https://www.facebook.com/share/g/1EbtddJgWS/"
    redirected = Fetch(
        {share: ("fb_share.html", "https://www.facebook.com/groups/555/?mibextid=x")}
    )
    assert fb.resolve_group(share, redirected) == "https://m.facebook.com/groups/555/"
    via_meta = Fetch({share: "fb_share.html"})
    assert fb.resolve_group(share, via_meta) == "https://m.facebook.com/groups/987654/"
    with pytest.raises(base.CollectorError, match="could not find the group"):
        fb.resolve_group(share, Fetch({share: "text_gear_page.html"}))
    with pytest.raises(LoginRequired):
        fb.resolve_group(share, Fetch({share: "fb_login.html"}))


def test_group_all_pages_no_duplicates():
    f = Fetch({GROUP: "fb_group_p1.html", GROUP2: "fb_group_p2.html"})
    r = fb.collect_group("https://www.facebook.com/groups/123456/", f)
    assert [p.source_id for p in r.posts] == ["1001", "1002", "1003", "1004", "1005"]
    assert (r.pages, r.stopped_because, r.complete) == (2, "last page", True)
    first = r.posts[0]
    assert first.author == "דני כהן" and first.text.startswith("מוכר קייט North Orbit 12 מטר 2021")
    assert first.image_urls == [
        "https://scontent.xx.fbcdn.net/v/a1.jpg",
        "https://scontent.xx.fbcdn.net/v/a2.jpg",
    ]
    assert r.posts[1].image_urls == []  # emoji / icon images are not listing photos
    assert first.hints["time_text"] == "לפני 3 שעות"


def test_group_stops_after_a_streak_of_known_posts(monkeypatch):
    monkeypatch.setattr(fb, "KNOWN_STREAK_STOP", 2)
    f = Fetch({GROUP: "fb_group_p1.html", GROUP2: "fb_group_p2.html"})
    r = fb.collect_group(GROUP, f, known_ids={"1002", "1003"})
    assert r.stopped_because == "reached known posts" and f.calls == [GROUP]


def test_group_login_wall_and_format_change():
    r = fb.collect_group(GROUP, Fetch({GROUP: "fb_login.html"}))
    assert "log in" in r.errors[0].lower() and not r.complete
    r = fb.collect_group(GROUP, Fetch({GROUP: "fb_empty_group.html"}))
    assert "may have changed its page format" in r.errors[0]


def test_group_max_pages_and_loop():
    r = fb.collect_group(
        GROUP, Fetch({GROUP: "fb_group_p1.html", GROUP2: "fb_group_p2.html"}), max_pages=1
    )
    assert r.stopped_because == "max pages" and not r.complete
    looping = Fetch({GROUP: "fb_group_p1.html", GROUP2: "fb_group_p1.html"})
    looping.routes[GROUP2] = "fb_group_p1.html"
    r = fb.collect_group(GROUP, looping)
    assert r.stopped_because == "pagination loop"


def test_marketplace():
    posts = fb.parse_marketplace(read("fb_marketplace.html"))
    assert [p.source_id for p in posts] == ["mp-555001", "mp-555002"]  # repeat collapsed
    assert posts[0].text == "קייט Cabrinha Moto 9 מטר 2021 | מחיר: 3600 ₪ | Givatayim"
    assert posts[0].url == "https://www.facebook.com/marketplace/item/555001/"
    assert "נמכר" in posts[1].text and posts[1].image_urls == [
        "https://scontent.xx.fbcdn.net/mp2.jpg"
    ]


def test_collect_marketplace():
    url = fb.MARKETPLACE_SEARCH.format(location="telaviv", query="%D7%A7%D7%99%D7%99%D7%98")
    r = fb.collect_marketplace("קייט", Fetch({url: "fb_marketplace.html"}))
    assert len(r.posts) == 2 and r.stopped_because == "first page only" and not r.complete
    r = fb.collect_marketplace("קייט", Fetch({url: "fb_login.html"}))
    assert "log in" in r.errors[0].lower()
    r = fb.collect_marketplace("קייט", Fetch({url: "text_gear_page.html"}))
    assert "format may have changed" in r.errors[0]


# --- prefilter: recall must be 100% -----------------------------------------------------------


def test_prefilter_keeps_every_sale_post():
    holdout = __import__("yaml").safe_load(
        (Path(__file__).parent / "fixtures" / "posts_holdout.yaml").read_text(encoding="utf-8")
    )
    sale = [p for p in CORPUS + holdout if p["expect"]["is_sale"]]
    dropped = [p["id"] for p in sale if not prefilter.check(p["text"]).keep]
    assert dropped == []


@pytest.mark.parametrize(
    "text, keep, reason",
    [("", False, "empty post"), ("איזה יום!", False, "no gear word, brand, model or size"),
     ("Duotone 12", True, "brand"), ("12 מטר ב-3000", True, "gear size"), ("Orbit 12m", True, "gear size"),
     ("rebel orbit", True, "model name"), ("קייטסרפינג בכנרת", True, "kitesurf wording"), ("טרפז", True, "gear word")],
)  # fmt: skip
def test_prefilter_reasons(text, keep, reason):
    d = prefilter.check(text)
    assert (d.keep, d.reason) == (keep, reason)


# --- base -------------------------------------------------------------------------------------


def test_rawpost_hash_changes_with_text_or_photos_only():
    a = RawPost("s", "1", "text", image_urls=["b", "a"])
    assert (
        a.content_hash == RawPost("s", "1", "text", url="other", image_urls=["a", "b"]).content_hash
    )
    assert a.content_hash != RawPost("s", "1", "text!", image_urls=["a", "b"]).content_hash


def test_check_page():
    base.check_page(Page(200, "<html>ok</html>", "u"), "s")
    for page, exc in ((Page(403, "", "u"), Blocked), (Page(200, "Just a moment...", "u"), Blocked),
                      (Page(500, "", "u"), base.CollectorError)):  # fmt: skip
        with pytest.raises(exc):
            base.check_page(page, "s")


def test_http_fetcher_pauses_between_requests(monkeypatch):
    import requests

    sleeps = []

    class Resp:
        status_code, text, url = 200, "hi", "https://x/final"

    f = base.HttpFetcher(cookies={"c_user": "1"}, min_delay=1, max_delay=1, sleep=sleeps.append)
    monkeypatch.setattr(f.session, "get", lambda url, timeout: Resp())
    assert f("https://x") == Page(200, "hi", "https://x/final")
    f("https://y")
    assert sleeps == [1] and f.session.cookies.get("c_user") == "1"
    assert "Android" in f.session.headers["User-Agent"]

    def boom(url, timeout):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(f.session, "get", boom)
    with pytest.raises(base.CollectorError, match="could not fetch"):
        f("https://z")


def test_host():
    assert base.host("https://www.Kitelab.co.il/x") == "kitelab.co.il"


# --- regressions from the step-4 review --------------------------------------------------------


def test_recaptcha_on_a_real_shop_page_is_not_a_bot_wall():
    html = read("woo_cat_p1.html").replace(
        "<head>", '<head><script src="https://www.google.com/recaptcha/api.js"></script>'
    )
    base.check_page(Page(200, html, "u"), "shop")  # no exception
    with pytest.raises(Blocked):
        base.check_page(
            Page(200, "<html><title>Attention Required! | Cloudflare</title>" + "x" * 9000, "u"),
            "s",
        )


def test_httponly_cookie_lines(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("# Netscape HTTP Cookie File\n.facebook.com\tTRUE\t/\tTRUE\t0\tc_user\t1\n"
                 "#HttpOnly_.facebook.com\tTRUE\t/\tTRUE\t0\txs\tabc\n")  # fmt: skip
    assert fb.load_cookies(f) == {"c_user": "1", "xs": "abc"}


def test_package_product_keeps_all_parts_as_bundle():
    from kitefinder.llm import rules

    raw = rules.extract_raw(
        "Duotone Rebel 12m kite + Click bar package | מחיר: ₪9,900 | (חדש)", single_item=True
    )
    assert [i["type"] for i in raw["items"]] == ["kite", "bar"]
    assert raw["bundle_price_ils"] == 9900 and all(i["price_ils"] is None for i in raw["items"])


def test_single_item_hint_survives_storage(db):
    r = web.collect_site(CAT, Fetch(SHOP_ROUTES))
    for post in r.posts:
        db.upsert_raw_post(post)
    rows = db.raw_posts_in_stage("fetched")
    assert rows and all(row["hints"]["single_item"] for row in rows)
    assert db.raw_posts_in_stage("fetched", limit=0) == []


def test_marketplace_empty_page_reports_format_change():
    url = fb.MARKETPLACE_SEARCH.format(location="telaviv", query="x")
    looks_normal = "<html><body><a href='/marketplace/'>Marketplace</a></body></html>"
    r = fb.collect_marketplace("x", lambda u: Page(200, looks_normal, u))
    assert "format may have changed" in r.errors[0]
    no_results = "<html><body>Marketplace — No listings found</body></html>"
    r = fb.collect_marketplace("x", lambda u: Page(200, no_results, u))
    assert r.errors == [] and r.posts == [] and url


def test_variations_without_ids_stay_separate():
    import html as h
    import json

    variations = [{"attributes": {"attribute_pa_size": s}, "display_price": p, "is_in_stock": True}
                  for s, p in (("9m", 7000), ("12m", 8000))]  # fmt: skip
    page = f'<form class="variations_form" data-product_variations="{h.escape(json.dumps(variations))}"></form>'
    routes = {CAT: "woo_cat_p1.html"}
    f = Fetch(routes)
    f.routes = {**routes}
    fetch = lambda u: Page(200, page, u) if u == REBEL else f(u)  # noqa: E731
    r = web.collect_site(CAT, fetch)
    rebel = [p.source_id for p in r.posts if "rebel" in p.source_id]
    assert len(rebel) == len(set(rebel)) == 2


def test_yad2_total_must_sit_next_to_the_ads():
    data = {
        "pagination": {"total": 3, "totalItems": 99},
        "feed": {
            "items": [
                {"token": "a", "title": "קייט", "price": 1},
                {"token": "b", "title": "בר", "price": 2},
            ],
            "totalItems": 45,
        },
    }
    ads, total = yad2.find_ads(data)
    assert len(ads) == 2 and total == 45
    ads, total = yad2.find_ads(
        {"feed": {"items": [{"token": "a", "title": "t", "price": 1}], "totalItems": True}}
    )
    assert total is None


@pytest.mark.parametrize(
    "body, expected",
    [("<p>יש לי גם בר ועוד</p>", "יש לי גם בר ועוד"), ("<p>מוכר קייט... עוד</p>", "מוכר קייט"),
     ("<p>Selling kite See more</p>", "Selling kite"), ("<p>נשאר עוד</p>", "נשאר עוד")],
)  # fmt: skip
def test_trailing_see_more_removed_but_not_the_word_od(body, expected):
    from bs4 import BeautifulSoup

    assert (
        fb._post_text(BeautifulSoup(f"<article>{body}</article>", "html.parser").article)
        == expected
    )
