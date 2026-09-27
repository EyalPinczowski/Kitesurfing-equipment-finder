"""People asking to buy are never shown as listings (Hebrew and English phrasings)."""

import pytest
from fakes import FakeTransport, gemini_reply

from kitefinder import pipeline
from kitefinder.llm import extract as ex
from kitefinder.llm import gemini as gm
from kitefinder.llm import normalize as nz

WANTED = [
    'מחפש קייט 9 מטר במצב טוב עד 2000 ש"ח, באזור המרכז',
    'מחפשת בר 50 ס"מ, עד 1000 ש"ח',
    "מחפשים ציוד לילד, קייט קטן וגלשן",
    "קונה בר של אוזון במצב סביר, מי שיש שישלח בפרטי",
    "מישהו מוכר טרפז מידה L? מחפשת משהו במצב טוב",
    "יש מישהו שמוכר קייט 12?",
    "יש למישהו קייט 12 למכור?",
    "למישהו יש גלשן 136 שהוא לא צריך?",
    "מעוניין לקנות גלשן טווין טיפ 138",
    "רוצה לקנות קייט North Orbit",
    "אשמח לקנות טרפז M במחיר טוב",
    'דרוש בר 50 ס"מ',
    "WTB North Orbit 12m",
    "Want to buy a Duotone Evo 10",
    "Wanted: twin tip 138-140",
    "Anyone selling a 9m kite?",
    "ISO harness size M",
    "In search of a foil board",
    "Looking for a used twin tip, budget 1500 NIS",
    "Who's selling a bar around here?",
    "Buying kites, any condition",
]
FOR_SALE = [
    'מוכר קייט 12 מטר, 3200 ש"ח. מחפש קונה רציני',
    "למכירה קייט North 12, לקונה רציני אפשר לרדת במחיר",
    "Selling my Duotone Evo 10m, looking for a buyer in the center",
    'קייט 14 מטר ישן אבל עובד, 800 ש"ח למי שרוצה להתחיל',
    "טרפז ION מידה L | מחיר: 450 ₪ | נתניה",
    "נמכר! קייט F-One Bandit 11 מטר",
    'גלשן צריך תיקון קטן, 900 ש"ח',
    "Kite for sale, perfect for anyone looking to start",
]


@pytest.mark.parametrize("text", WANTED)
def test_asking_to_buy_is_never_a_listing(text):
    assert nz.sale_intent(text) == "wanted"
    result = ex.extract_post(text, None)
    assert (result.status, result.reason, result.listings) == ("not_listing", "looking to buy", [])


@pytest.mark.parametrize("text", FOR_SALE)
def test_sellers_phrases_are_not_mistaken_for_buyers(text):
    assert nz.sale_intent(text) in ("offer", "mixed")


@pytest.mark.parametrize("text", WANTED[:6])
def test_gemini_is_not_even_asked_about_a_wanted_post(db, text):
    """Even if Gemini would call it a sale, a wanted post is rejected first (no quota spent)."""
    sale = {"is_sale_post": True, "items": [{"type": "kite", "size": 9, "sold": False}]}
    transport = FakeTransport(lambda body: (200, gemini_reply(sale), {}))
    client = gm.GeminiClient("K", db=db, transport=transport, sleep=lambda s: None)
    result = ex.extract_post(text, client)
    assert result.status == "not_listing" and transport.requests == []


def test_a_mixed_post_lists_only_what_is_offered(db):
    text = 'מוכר קייט Duotone Evo 9 מטר 2500 ש"ח. מחפש קייט 12 מטר בהחלפה'
    assert nz.sale_intent(text) == "mixed"
    rules = ex.extract_post(text, None)
    assert [(x.type, x.size) for x in rules.listings] == [("kite", 9)]
    seen = []

    def responder(body):
        seen.append(body["contents"][0]["parts"][0]["text"])
        items = [{"type": "kite", "size": 9, "sold": False, "brand": "Duotone", "price_ils": 2500}]
        return 200, gemini_reply({"is_sale_post": True, "items": items}), {}

    client = gm.GeminiClient("K", db=db, transport=FakeTransport(responder), sleep=lambda s: None)
    result = ex.extract_post(text, client)
    assert [(x.type, x.size) for x in result.listings] == [("kite", 9)]
    post = seen[0].split("<post>")[-1]
    assert "מחפש" not in post and "Evo 9" in post  # Gemini only sees the part that's for sale


def test_wanted_posts_in_a_group_never_reach_listings(db):
    from world import World

    world = World()
    pipeline.run(db, world.settings(), world.fetchers(), None)
    rows = db.conn.execute("SELECT text, stage_status, stage_reason FROM raw_posts").fetchall()
    wanted = [r for r in rows if nz.sale_intent(r["text"]) == "wanted"]
    assert wanted  # the sample group page has a "looking for" post
    for r in wanted:
        assert r["stage_status"] in ("not_listing", "prefilter_rejected"), r["text"]
    assert not any(nz.sale_intent(x.description or "") == "wanted" for x in db.candidate_listings())
