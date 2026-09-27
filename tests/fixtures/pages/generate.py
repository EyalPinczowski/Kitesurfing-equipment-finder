"""Builds the synthetic sample pages in this folder (run: python tests/fixtures/pages/generate.py).

They follow each platform's known markup (WooCommerce, schema.org JSON-LD, Yad2 __NEXT_DATA__,
Facebook mobile + Marketplace JSON). Real saved pages should be added next to them once the
sites are reachable; see docs/DECISIONS_FOR_APPROVAL.md.
"""

import html
import json
import pathlib

D = pathlib.Path(__file__).parent
B = "https://shop.example.co.il"
Q = '"'  # a literal double quote for Hebrew abbreviations like ש"ח


def card(url, title, price_html, img, variable=False, oos=False):
    kind = "product-type-variable" if variable else "product-type-simple"
    stock = "outofstock" if oos else "instock"
    btn = "product_type_variable" if variable else "product_type_simple add_to_cart_button"
    label = "בחירת אפשרויות" if variable else "הוספה לסל"
    return f"""<li class="product type-product {kind} {stock}">
  <a href="{url}" class="woocommerce-LoopProduct-link woocommerce-loop-product__link">
    <img src="{img}" class="attachment-woocommerce_thumbnail" alt="">
    <h2 class="woocommerce-loop-product__title">{title}</h2>
    <span class="price">{price_html}</span>
  </a>
  <a href="?add-to-cart=1" class="button {btn}">{label}</a>
</li>"""


def amt(n):
    return (
        f'<span class="woocommerce-Price-amount amount"><bdi>{n}&nbsp;'
        f'<span class="woocommerce-Price-currencySymbol">&#8362;</span></bdi></span>'
    )


def page(cards, count, nxt):
    return f"""<!doctype html><html lang="he" dir="rtl"><head><title>קייטים</title></head><body>
<header><nav>תפריט ראשי קייטים גלשנים</nav></header>
<main><p class="woocommerce-result-count">{count}</p>
<ul class="products columns-4">{"".join(cards)}</ul>
<nav class="woocommerce-pagination"><ul class="page-numbers">{nxt}</ul></nav></main>
<footer>כל הזכויות שמורות</footer></body></html>"""


def write(name, text):
    (D / name).write_text(text, encoding="utf-8")


def shops():
    p1 = page(
        [
            card(
                f"{B}/product/north-orbit-2023/",
                f"North Orbit 2023 12 מ{Q}ר",
                f"<del>{amt('8,900')}</del> <ins>{amt('6,900')}</ins>",
                f"{B}/img/orbit.jpg",
            ),
            card(
                f"{B}/product/duotone-rebel-sls-2024/",
                "Duotone Rebel SLS 2024",
                f"{amt('7,450')} – {amt('8,450')}",
                f"{B}/img/rebel.jpg",
                variable=True,
            ),
            card(
                f"{B}/product/north-atmos-2023/",
                "גלשן טווין טיפ North Atmos Carbon 2023 139x42",
                amt("4,290"),
                f"{B}/img/atmos.jpg",
                oos=True,
            ),
        ],
        "מציג 1&ndash;3 מתוך 5 תוצאות",
        f'<li><span class="page-numbers current">1</span></li>'
        f'<li><a class="page-numbers" href="{B}/product-category/kites/page/2/">2</a></li>'
        f'<li><a class="next page-numbers" href="{B}/product-category/kites/page/2/">&larr;</a></li>',
    )
    p2 = page(
        [
            card(
                f"{B}/product/ion-apex-harness/",
                "טרפז ION Apex מידה M",
                amt("1,590"),
                f"{B}/img/apex.jpg",
            ),
            card(
                f"{B}/product/used-cabrinha-switchblade-10/",
                "משומש: Cabrinha Switchblade 10 מטר 2020",
                amt("3,200"),
                f"{B}/img/sb.jpg",
            ),
        ],
        "מציג 4&ndash;5 מתוך 5 תוצאות",
        f'<li><a class="prev page-numbers" href="{B}/product-category/kites/">&rarr;</a></li>'
        f'<li><span class="page-numbers current">2</span></li>',
    )
    write("woo_cat_p1.html", p1)
    write("woo_cat_p2.html", p2)
    size = "attribute_pa_%d7%92%d7%95%d7%93%d7%9c"  # URL-encoded Hebrew "גודל" (size)
    variations = [
        {
            "attributes": {size: "9-%d7%9e%d7%98%d7%a8"},
            "display_price": 7450,
            "variation_id": 9001,
            "is_in_stock": True,
            "image": {"src": f"{B}/img/rebel9.jpg"},
        },
        {
            "attributes": {size: "10-%d7%9e%d7%98%d7%a8"},
            "display_price": 7950,
            "variation_id": 9002,
            "is_in_stock": True,
            "image": {"src": f"{B}/img/rebel10.jpg"},
        },
        {
            "attributes": {size: "12-%d7%9e%d7%98%d7%a8"},
            "display_price": 8450,
            "variation_id": 9003,
            "is_in_stock": False,
            "image": {},
        },
    ]
    write(
        "woo_product_rebel.html",
        f"""<!doctype html><html><body>
<h1 class="product_title">Duotone Rebel SLS 2024</h1>
<div class="woocommerce-product-details__short-description"><p>קייט רבל SLS החדש, הכי יציב בגלים. אחריות יבואן.</p></div>
<form class="variations_form cart" data-product_id="555" data-product_variations="{html.escape(json.dumps(variations))}">
<select name="attribute_pa_גודל"><option>9 מטר</option></select></form></body></html>""",
    )
    ld = {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "item": {
                    "@type": "Product",
                    "name": "F-One Bandit 2025 12m",
                    "image": ["https://other.example.com/b.jpg"],
                    "sku": "FB25-12",
                    "offers": {
                        "@type": "Offer",
                        "price": "7890",
                        "priceCurrency": "ILS",
                        "availability": "https://schema.org/InStock",
                        "url": "https://other.example.com/p/bandit-12",
                    },
                },
            },
            {
                "@type": "ListItem",
                "position": 2,
                "item": {
                    "@type": "Product",
                    "name": "F-One Linx bar 2024",
                    "offers": [
                        {
                            "@type": "Offer",
                            "price": "3290",
                            "availability": "https://schema.org/OutOfStock",
                            "url": "https://other.example.com/p/linx",
                        }
                    ],
                },
            },
        ],
    }
    write(
        "jsonld_list.html",
        f'<html><head><script type="application/ld+json">{json.dumps(ld)}</script>'
        f'</head><body><div id="app"></div></body></html>',
    )
    write(
        "text_gear_page.html",
        f"""<html><body><nav>בית | ציוד | צור קשר</nav><main><h1>ציוד יד שנייה</h1>
<p>קייט Ozone Enduro 10 מטר 2021 - 2,800 ש{Q}ח</p><p>גלשן טווין טיפ Nobile 138x42 - 1,100 ש{Q}ח</p>
<script>var x = "ignore me";</script></main><footer>טלפון 03-1234567</footer></body></html>""",
    )


def yad2():
    def nd(items, total):
        feed = {"items": items}
        if total is not None:
            feed["totalItems"] = total
        data = {
            "props": {
                "pageProps": {"dehydratedState": {"queries": [{"state": {"data": {"feed": feed}}}]}}
            }
        }
        return (
            f'<html><body><script id="__NEXT_DATA__" type="application/json">'
            f"{json.dumps(data, ensure_ascii=False)}</script></body></html>"
        )

    y1 = [
        {
            "token": "a1b2",
            "title": "קייט North Orbit 12 מטר",
            "price": 3200,
            "description": "שנת 2021, מצב מצוין",
            "address": {"city": {"text": "הרצליה"}},
            "metaData": {"images": ["https://img.yad2.co.il/1.jpg"]},
        },
        {
            "token": "c3d4",
            "title": "גלשן קייט טווין טיפ 138",
            "price": 1200,
            "description": "כולל סטרפים",
            "address": {"city": {"text": "חיפה"}},
            "metaData": {"images": []},
        },
        {
            "token": "e5f6",
            "title": "טרפז ION מידה L",
            "price": 450,
            "address": {"city": {"text": "נתניה"}},
        },
    ]
    y2 = [
        {
            "token": "c3d4",
            "title": "גלשן קייט טווין טיפ 138",
            "price": 1200,
            "address": {"city": {"text": "חיפה"}},
        },
        {
            "token": "g7h8",
            "title": "קייט Duotone Neo 9 מטר",
            "price": 2500,
            "description": "2020",
            "address": {"city": {"text": "אשדוד"}},
            "metaData": {"images": ["https://img.yad2.co.il/2.jpg"]},
        },
    ]
    write("yad2_p1.html", nd(y1, 4))
    write("yad2_p2.html", nd(y2, 4))
    write("yad2_p1_nototal.html", nd(y1, None))
    write("yad2_empty.html", nd([], None))
    write(
        "yad2_captcha.html",
        "<html><head><title>ShieldSquare Captcha</title></head><body>Are you a robot?</body></html>",
    )


def facebook():
    def art(pid, author, text, imgs=(), permalink=True):
        ft = html.escape(json.dumps({"top_level_post_id": pid, "content_owner_id_new": "1"}))
        link = (
            f'<a href="/groups/123456/permalink/{pid}/?refid=18">כל התגובות</a>'
            if permalink
            else ""
        )
        im = "".join(f'<img src="https://scontent.xx.fbcdn.net/v/{i}.jpg">' for i in imgs)
        return (
            f"<article data-ft='{ft}'><header><h3><strong><a href=\"/profile.php?id=9\">{author}</a>"
            f'</strong></h3></header><div class="story_body_container"><p>{text}</p>{im}'
            f'<img src="https://static.xx.fbcdn.net/rsrc.php/emoji.png"></div>'
            f"<footer><abbr>לפני 3 שעות</abbr>{link}</footer></article>"
        )

    kite = art(
        "1001", "דני כהן", f"מוכר קייט North Orbit 12 מטר 2021, 3200 ש{Q}ח. הרצליה", ["a1", "a2"]
    )
    wanted = art("1002", "נועה לוי", "מחפשת טרפז מידה M")
    evo = art("1003", "Eli", "Selling Duotone Evo 10m 2021, 3,400 NIS", ["b1"], permalink=False)
    board = art("1004", "רון", f"גלשן טווין טיפ 138x41 ב-1100 ש{Q}ח")
    chat = art("1005", "שיר", "איזה יום היה היום!")
    more = (
        '<div><a href="/groups/123456/?bacr=1690000000%3A1003&amp;refid=18">'
        "<span>הצג פוסטים נוספים</span></a></div>"
    )
    write(
        "fb_group_p1.html",
        f'<html><body><div id="m_group_stories_container">{kite}{wanted}{evo}</div>{more}</body></html>',
    )
    write("fb_group_p2.html", f"<html><body>{evo}{board}{chat}</body></html>")
    write(
        "fb_login.html",
        '<html><body><form id="login_form" action="/login/device-based/regular/login/">'
        '<input name="email"></form>Log in to Facebook</body></html>',
    )
    write(
        "fb_empty_group.html",
        '<html><body><div id="root"></div><script>require("x")</script></body></html>',
    )
    write(
        "fb_share.html",
        '<html><head><meta property="og:url" content="https://www.facebook.com/groups/987654/">'
        "</head><body></body></html>",
    )

    def listing(i, title, amount, city=None, photo=None, sold=False):
        d = {"id": i, "marketplace_listing_title": title, "listing_price": {"amount": amount}}
        if city:
            d["location"] = {"reverse_geocode": {"city": city}}
        if photo:
            d["primary_listing_photo"] = {"image": {"uri": photo}}
        d["is_sold"] = sold
        return {"node": {"listing": d}}

    edges = [
        listing(
            "555001",
            "קייט Cabrinha Moto 9 מטר 2021",
            "3600.00",
            "Givatayim",
            "https://scontent.xx.fbcdn.net/mp1.jpg",
        ),
        listing(
            "555002",
            "Kite board 139cm",
            "1000.00",
            "Haifa",
            "https://scontent.xx.fbcdn.net/mp2.jpg",
            sold=True,
        ),
        listing("555001", "קייט Cabrinha Moto 9 מטר 2021", "3600.00"),  # repeated in the feed
    ]
    mp = {
        "require": [
            [
                "ScheduledServerJS",
                "handle",
                None,
                [
                    {
                        "__bbox": {
                            "result": {
                                "data": {"marketplace_search": {"feed_units": {"edges": edges}}}
                            }
                        }
                    }
                ],
            ]
        ]
    }
    write(
        "fb_marketplace.html",
        '<html><body><script type="application/json" data-sjs>'
        f"{json.dumps(mp, ensure_ascii=False)}</script><div>Marketplace</div></body></html>",
    )
    write(
        "fb_cookies_editor.json",
        json.dumps(
            [
                {"domain": ".facebook.com", "name": "c_user", "value": "100001"},
                {"domain": ".facebook.com", "name": "xs", "value": "12%3Aabc"},
                {"domain": ".google.com", "name": "NID", "value": "x"},
                {"domain": ".facebook.com", "name": "datr", "value": "d"},
            ]
        ),
    )
    write(
        "fb_cookies.txt",
        "# Netscape HTTP Cookie File\n.facebook.com\tTRUE\t/\tTRUE\t0\tc_user\t100001\n"
        ".facebook.com\tTRUE\t/\tTRUE\t0\txs\tabc\n.example.com\tTRUE\t/\tTRUE\t0\tz\t1\n",
    )


if __name__ == "__main__":
    shops()
    yad2()
    facebook()
