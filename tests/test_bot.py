"""Family 2 (full bot messages): every message the bot sends is compared in full."""

import golden
import pytest
from fakebot import FakeTelegram, msg, press
from world import CATEGORY, World

from kitefinder import pipeline
from kitefinder.bot import formatter
from kitefinder.bot.app import BotApp
from kitefinder.bot.telegram import TelegramAPI, TelegramError, button
from kitefinder.models import Listing, Profile
from kitefinder.sizing import quiver

OWNER = 42


@pytest.fixture(autouse=True)
def no_alert_gap(monkeypatch):
    monkeypatch.setattr("kitefinder.bot.app.ALERT_GAP_S", 0)


@pytest.fixture
def tg():
    return FakeTelegram()


@pytest.fixture
def app(db, tg):
    return BotApp(db, tg.api(), owner_chat=str(OWNER))


def send(app, *items):
    """Messages (str) and button presses (('press', data)) in order."""
    for i, item in enumerate(items, 1):
        if isinstance(item, tuple):
            app.handle_update(press(item[1], update_id=i))
        else:
            app.handle_update(msg(item, update_id=i))


# --- the questionnaire ------------------------------------------------------------------------

SETUP = [
    "/setup", "80", "86", ("press", "q:intermediate"), ("press", "q:twintip"), ("press", "q:freeride"),
    "בת גלים, Sdot Yam", ("press", "q:summer"),
    ("press", "q:add"), ("press", "q:kite"), "12", "North Orbit 2021",
    ("press", "q:add"), ("press", "q:harness"), ("press", "q:skip"),
    ("press", "q:done"), "8000", ("press", "q:used"), "2018", "100",
    "https://kitelab.co.il/", "laguna.co.il/product-category/kites/", ("press", "q:done"),
]  # fmt: skip


def test_full_setup_conversation(app, tg, db):
    send(app, *SETUP)
    golden.check("setup_flow", tg.transcript())
    p = db.get_profile()
    assert (p.weight_kg, p.waist_cm, p.skill, p.style) == (80, 86, "intermediate", "twintip")
    assert (p.spots, p.season, p.wind_min_kn, p.wind_max_kn) == (
        ["Bat Galim", "Sdot Yam / Caesarea"],
        "summer",
        10,
        18,
    )
    assert (p.budget_ils, p.condition_pref, p.min_year, p.travel_km) == (8000, "used", 2018, 100)
    gear = db.list_owned()
    assert [(g.type, g.brand, g.model, g.size, g.year) for g in gear] == [
        ("harness", "", "", None, None),
        ("kite", "North", "Orbit", 12, 2021),
    ]
    assert [s["url"] for s in db.list_sites()] == [
        "https://kitelab.co.il/",
        "https://laguna.co.il/product-category/kites/",
    ]
    assert not app.setup.active


def test_setup_then_recommendation_button(app, tg):
    send(app, *SETUP)
    tg.clear()
    app.handle_update(press("cmd:recommend", update_id=99))
    text = tg.sent()[0]["text"]
    assert (
        text.startswith("Recommendation #1 (set, minimum)")
        and "Using your North Orbit 12 m² kite" in text
    )


def test_wind_range_instead_of_areas_skips_season(app, tg, db):
    send(app, "/setup", "70", "80", ("press", "q:beginner"), ("press", "q:foil"), "15-25")
    assert tg.sent()[-1]["text"].startswith("Do you own any gear?")
    assert app.setup.state()["draft"]["wind_source"] == "manual"


def test_bad_answers_are_explained_and_asked_again(app, tg):
    send(
        app,
        "/setup",
        "heavy",
        "500",
        "80",
        "86",
        "maybe",
        ("press", "q:advanced"),
        ("press", "q:twintip"),
        ("press", "q:freeride"),
        "bat galm",
    )
    texts = [b["text"] for b in tg.sent()]
    assert (
        texts[1] == "⚠ please send a number for weight\nLet's set you up. What's your weight in kg?"
    )
    assert texts[2].startswith("⚠ weight should be between 30 and 150")
    assert texts[5].startswith("⚠ tap one of the buttons\nYour level?")
    assert "did you mean Bat Galim?" in texts[-1]
    assert app.setup.state()["step"] == "areas"


def test_setup_survives_a_restart_and_can_be_cancelled(db, tg):
    first = BotApp(db, tg.api(), owner_chat=str(OWNER))
    send(first, "/setup", "80")
    second = BotApp(db, tg.api(), owner_chat=str(OWNER))  # e.g. the phone restarted the bot
    second.handle_update(msg("86", update_id=10))
    assert second.setup.state()["step"] == "skill"
    second.handle_update(msg("/cancel", update_id=11))
    assert not second.setup.active and db.get_profile() is None
    assert tg.sent()[-1]["text"] == "Setup stopped. Nothing was changed. Start again with /setup"


def test_invalid_site_link_asks_again(app, tg):
    send(app, *SETUP[:-3], "not a link")
    assert tg.sent()[-1]["text"].startswith("⚠ not a valid web address: https://not a link")


# --- who may use the bot ------------------------------------------------------------------------


def test_without_a_chat_id_nobody_may_use_the_bot(db, tg):
    """Your choice (#25): no TELEGRAM_CHAT_ID, no access — not even the first /start."""
    app = BotApp(db, tg.api())
    app.handle_update(msg("/start", chat=42))
    app.handle_update(press("fav:1", chat=42, update_id=2))
    assert app.owner is None
    assert tg.sent()[0]["text"] == "This bot is private."
    assert tg.sent("answerCallbackQuery")[0]["text"] == "This bot is private."
    assert db.get_profile() is None and not app.setup.active


def test_only_the_configured_chat_is_answered(db, tg):
    app = BotApp(db, tg.api(), owner_chat="42")
    app.handle_update(msg("/start", chat=7))
    app.handle_update(press("fav:1", chat=7, update_id=2))
    app.handle_update(msg("/help", chat=42, update_id=3))
    assert [str(b["chat_id"]) for b in tg.sent()] == ["7", "42"]
    assert tg.sent()[0]["text"] == "This bot is private."
    assert tg.sent("answerCallbackQuery")[0]["text"] == "This bot is private."
    assert tg.sent()[1]["text"].startswith("/setup — ")


def test_start_with_profile_welcomes_back(app, tg, db):
    db.save_profile(Profile(80, 86, 12, 25))
    send(app, "/start")
    assert tg.sent()[0]["text"] == "Welcome back! /help lists what I can do."


# --- commands -------------------------------------------------------------------------------------


def test_help_and_command_list(app, tg):
    send(app, "/help")
    golden.check("help", tg.transcript())
    app.register_commands()
    (cmds,) = tg.sent("setMyCommands")
    assert [c["command"] for c in cmds["commands"]][:3] == ["setup", "recommend", "under"]


def test_commands_reuse_the_cli(app, tg, db):
    db.save_profile(Profile(80, 86, 12, 25, budget_ils=9000))
    send(app, "/profile", "/recommend minimum", "/under 9,000", "/history", "/gear", "/search", "/watch harness M",
         "/watch", "/unwatch harness M", "/sites add https://kitelab.co.il/", "/sites", "/report", "/nonsense",
         "hello there", "/recommend banana")  # fmt: skip
    texts = [b["text"] for b in tg.sent()]
    assert texts[0].startswith("Weight: 80 kg")
    assert texts[1].startswith("Recommendation #1 (set, minimum)")
    assert texts[2].startswith("Best set within ₪9,000:")
    assert texts[3].startswith("#2 ") and texts[4] == "No gear yet."
    assert texts[5] == "What should I search? e.g. /search kite 12m"
    assert texts[6:9] == ["Watching", "harness M", "Stopped watching"]
    assert texts[9] == "Added site #1" and texts[10] == "#1 https://kitelab.co.il/"
    assert texts[11] == "no run yet — run: kitefinder run"
    assert texts[12] == "Unknown command. /help lists what I can do."
    assert texts[13] == "Send /help for what I can do — or e.g. /search kite 12m"
    assert texts[14] == "⚠ I didn't understand that. Try /help"


def test_long_answers_are_split(app, tg, monkeypatch):
    monkeypatch.setattr("kitefinder.cli.run", lambda argv, db=None: "x" * 9000)
    send(app, "/history")
    assert [len(b["text"]) for b in tg.sent()] == [4000, 4000, 1000]


# --- alerts ---------------------------------------------------------------------------------------


@pytest.fixture
def world_app(db, tg):
    world = World()
    profile = Profile(80, 86, 12, 25)
    db.save_profile(profile)
    db.save_recommendation(quiver.recommend_set(profile, [], "minimum"))
    db.add_site(CATEGORY)
    db.add_watch("harness M")
    runner = lambda: pipeline.run(db, world.settings(), world.fetchers(), None, "bot")  # noqa: E731
    return BotApp(db, tg.api(), owner_chat=str(OWNER), runner=runner)


def test_run_now_sends_every_alert_once(world_app, tg, db):
    world_app.handle_update(msg("/run"))
    golden.check("alerts_after_run", tg.transcript())
    cards = [b for b in tg.sent() if b.get("parse_mode") == "HTML"]
    items, queries = pipeline.alert_targets(db)
    assert db.pending_matches(items, queries) == []
    assert len(db.notifications()) == len({m for n in db.notifications() for m in [n["match_id"]]})
    assert tg.sent()[-1]["text"].endswith(f"📨 {len(cards)} new alerts sent.")
    tg.clear()
    world_app.handle_update(msg("/run", update_id=2))  # a second run: nothing new to alert
    assert [b for b in tg.sent() if b.get("parse_mode") == "HTML"] == []


def test_every_alert_has_description_price_location_and_photos(world_app, db):
    world_app.runner()
    alerts = world_app.build_alerts()
    assert alerts
    for alert in alerts:
        lines = alert.text.splitlines()
        assert (
            lines[1].startswith("💰 ") and lines[2].startswith("📍 ") and lines[3].startswith("📝 ")
        )
        assert lines[3] != "📝 " and lines[4].startswith("🔍 Condition: ")
        has_images = bool(db.listing_images(alert.listing_id))
        assert bool(alert.photos) == has_images
        assert alert.buttons[0][0]["callback_data"] == f"fav:{alert.listing_id}"


def test_same_item_on_two_sources_is_one_alert(world_app, db):
    world_app.runner()
    alerts = world_app.build_alerts()
    keys = [formatter.duplicate_key(db.get_listing(a.listing_id)) for a in alerts]
    assert keys.count(("kite", "North", 12.0, "", 3200)) == 1  # Yad2 + Facebook: one alert
    (orbit,) = [
        a for a, k in zip(alerts, keys, strict=True) if k == ("kite", "North", 12.0, "", 3200)
    ]
    assert len(orbit.match_ids) == 2 and "🔁 Also posted on: Facebook" in orbit.text
    assert sum(len(a.match_ids) for a in alerts) == len({m for a in alerts for m in a.match_ids})


def test_alert_card_golden_with_and_without_photos(db):
    rich = Listing("kite", 3200, "North", "Orbit", 12, year=2021, is_new=False, location="Herzliya",
                   description="מצב מצוין, ללא תיקונים <נקי>", source="facebook", url="https://fb.com/p/1",
                   seller="דני", id=5)  # fmt: skip
    match = {"id": 9, "why": "fits your kite 12–14m²; ₪300 below the market ₪3,500", "query": ""}
    a = formatter.format_alert(rich, match, ["https://img/1.jpg"] * 6, {"score": 8.0, "verdict": "small dings"},
                               [Listing("kite", 3200, "North", size=12, source="yad2")])  # fmt: skip
    bare = Listing(
        "board", None, "", size=138, subtype="twintip", source="shop.example.co.il", id=6
    )
    b = formatter.format_alert(
        bare,
        {
            "id": 10,
            "why": "fits your board 138–141 cm; no price stated — ask the seller; size not stated",
            "query": "twin tip 138",
        },
        [],
    )
    golden.check(
        "alert_cards",
        "\n\n=====\n\n".join(
            [a.text, repr(a.photos), repr(a.buttons), b.text, repr(b.photos), repr(b.buttons)]
        ),
    )
    assert len(a.photos) == 4 and "&lt;נקי&gt;" in a.text  # HTML is escaped
    assert "📍 location unknown" in b.text and "price not stated — ask the seller" in b.text
    assert b.buttons == [
        [button("✅ Favorite", "fav:6"), button("❌ Dismiss", "dis:6")]
    ]  # no link: no Open


def test_bundle_and_unassessed_lines():
    listing = Listing("kite", None, size=10, bundle_price_ils=4500, id=1)
    a = formatter.format_alert(
        listing, {"id": 1, "why": ""}, [], {"score": None, "verdict": "stock photo"}
    )
    assert (
        "sold as a bundle for ₪4,500" in a.text
        and "photos don't show the condition — stock photo" in a.text
    )
    assert "(no description in the post)" in a.text and "✅" not in a.text.split("\n")[-1]


def test_duplicate_key():
    assert formatter.duplicate_key(Listing("kite", 3200, "north", size=12)) == (
        "kite",
        "North",
        12,
        "",
        3200,
    )
    assert (
        formatter.duplicate_key(Listing("kite", 3200, "", size=12)) is None
    )  # no brand: can't tell
    assert formatter.duplicate_key(Listing("harness", 500, "ION", size_label="M")) == (
        "harness",
        "ION",
        None,
        "M",
        500,
    )


def test_photo_failure_still_sends_the_card(db):
    tg = FakeTelegram(fail_methods={"sendMediaGroup", "sendPhoto"})
    app = BotApp(db, tg.api(), owner_chat=str(OWNER))
    world = World()
    db.save_profile(Profile(80, 86, 12, 25))
    db.save_recommendation(quiver.recommend_set(db.get_profile(), [], "minimum"))
    pipeline.run(db, world.settings(), world.fetchers(), None)
    sent = app.send_alerts()
    assert sent == len([b for b in tg.sent() if b.get("parse_mode") == "HTML"]) > 0
    assert all(n["payload"]["photos"] == [] for n in db.notifications())


def test_alert_limit_leaves_the_rest_for_later(world_app, db, monkeypatch):
    world_app.runner()
    total = len(world_app.build_alerts())
    assert world_app.send_alerts(limit=2) == 2
    assert len(world_app.build_alerts()) == total - 2


# --- buttons --------------------------------------------------------------------------------------


def test_favorite_and_dismiss_buttons(world_app, tg, db):
    world_app.runner()
    first, second = world_app.build_alerts()[:2]
    world_app.handle_update(press(f"fav:{first.listing_id}", update_id=1))
    world_app.handle_update(press(f"dis:{second.listing_id}", update_id=2))
    golden.check("buttons", tg.transcript())
    assert db.get_mark("listing", first.listing_id) == "favorite"
    assert db.get_mark("listing", second.listing_id) == "dismissed"
    assert second.listing_id not in [a.listing_id for a in world_app.build_alerts()]
    tg.clear()
    world_app.handle_update(msg("/favorites", update_id=3))
    text = tg.sent()[0]["text"]
    assert (
        text.startswith("⭐ Your favorites:\n• ")
        and str(first.listing_id) not in text.split("\n")[0]
    )
    world_app.handle_update(press("fav:99999", update_id=4))
    assert tg.sent("answerCallbackQuery")[-1]["text"] == "That listing is gone."


def test_favorites_empty(app, tg):
    send(app, "/favorites")
    assert tg.sent()[0]["text"] == "No favorites yet — tap ✅ Favorite on an alert."


def test_run_without_runner(app, tg):
    send(app, "/run")
    assert (
        tg.sent()[0]["text"]
        == "Searching isn't available here — run `kitefinder daemon` on the phone."
    )


# --- polling and the API client -------------------------------------------------------------------


def test_poll_handles_each_update_once(app, tg, db):
    tg.updates = [msg("/help", update_id=5), msg("/help", update_id=6)]
    assert app.poll_once() == 2
    assert db.meta_get("tg_offset") == "7"
    assert app.poll_once() == 0  # offset 7: both already handled
    assert len(tg.sent()) == 2


def test_api_errors_and_markup():
    tg = FakeTelegram(fail_methods={"sendMessage"})
    api = tg.api()
    with pytest.raises(TelegramError, match="sendMessage failed: Bad Request"):
        api.send_message(1, "hi")
    ok = FakeTelegram().api()
    ok.send_message(1, "pick", keyboard=[["a", "b"]])
    ok.set_menu_button(1, "https://x.trycloudflare.com")
    calls = ok.transport.calls
    assert calls[0][1]["reply_markup"]["keyboard"] == [[{"text": "a"}, {"text": "b"}]]
    assert calls[1][1]["menu_button"] == {
        "type": "web_app",
        "text": "Open app",
        "web_app": {"url": "https://x.trycloudflare.com"},
    }
    with pytest.raises(TelegramError, match="no bot token"):
        TelegramAPI("")


def test_requests_transport(monkeypatch):
    import requests

    from kitefinder.bot import telegram

    monkeypatch.setattr(
        requests, "post", lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError("down"))
    )
    assert telegram.requests_transport("u", {}, 1)[1]["description"].startswith("network error")


# --- regressions found in the Step 6 self-review --------------------------------------------------


def test_one_photo_goes_by_send_photo_and_albums_by_media_group():
    tg = FakeTelegram()
    api = tg.api()
    api.send_media_group(1, ["https://img/a.jpg"])
    api.send_media_group(1, [f"https://img/{i}.jpg" for i in range(12)])
    (single,) = tg.sent("sendPhoto")
    (album,) = tg.sent("sendMediaGroup")
    assert single["photo"] == "https://img/a.jpg" and len(album["media"]) == 10


def test_long_poll_waits_longer_than_telegram_holds_the_request():
    timeouts = []

    def transport(url, body, timeout):
        timeouts.append((body.get("timeout"), timeout))
        return 200, {"ok": True, "result": []}

    TelegramAPI("1:T", transport=transport, timeout=30).get_updates(timeout=50)
    TelegramAPI("1:T", transport=transport, timeout=30).send_message(1, "hi")
    assert timeouts == [(50, 80), (None, 30)]


def test_a_refused_card_is_sent_as_plain_text_and_the_rest_still_go(world_app, db, monkeypatch):
    world_app.runner()
    tg = world_app.api.transport
    tg.clear()
    first = world_app.build_alerts()[0].text
    real = tg.__call__

    def picky(url, body, timeout):
        if body.get("parse_mode") == "HTML" and body.get("text") == first:
            return 400, {"ok": False, "description": "Bad Request: can't parse entities"}
        return real(url, body, timeout)

    world_app.api.transport = picky
    total = len(world_app.build_alerts())
    assert world_app.send_alerts() == total
    plain = [b for m, b in tg.calls if m == "sendMessage" and "parse_mode" not in b]
    assert plain[0]["text"].startswith("🪁 Kite ") and "<b>" not in plain[0]["text"]
    assert world_app.build_alerts() == []


def test_a_card_telegram_never_takes_stays_pending(world_app, db):
    world_app.runner()
    total = len(world_app.build_alerts())
    world_app.api.transport.fail_methods = {"sendMessage"}
    assert world_app.send_alerts() == 0
    assert len(world_app.build_alerts()) == total and db.notifications() == []


def test_empty_output_is_not_sent_empty(app, tg, monkeypatch):
    monkeypatch.setattr("kitefinder.cli.run", lambda argv, db=None: "")
    send(app, "/history")
    assert tg.sent()[0]["text"] == "(nothing to show)"


def test_typed_button_labels_count_as_presses(app, tg):
    send(
        app,
        "/setup",
        "80",
        "86",
        "Intermediate",
        "twin tip",
        "Big air",
        "north",
        "All year",
        "done",
    )
    state = app.setup.state()
    assert state["draft"]["skill"] == "intermediate" and state["draft"]["style"] == "twintip"
    assert state["draft"]["discipline"] == "bigair"
    assert state["draft"]["season"] == "all" and "Bat Galim" in state["draft"]["spots"]
    assert state["step"] == "budget"


# --- regressions found by the Step 6 code review ------------------------------------------------


def test_setup_refuses_a_wind_range_the_profile_cannot_hold(app, tg):
    send(
        app,
        "/setup",
        "80",
        "86",
        ("press", "q:advanced"),
        ("press", "q:twintip"),
        ("press", "q:freeride"),
        "52-58",
    )
    assert tg.sent()[-1]["text"].startswith("⚠ wind range should look like 12-25")
    assert app.setup.state()["step"] == "areas"


def test_a_setup_that_cannot_be_saved_says_so(app, tg, monkeypatch):
    send(app, *SETUP[:-1])
    state = app.setup.state()
    state["draft"]["weight_kg"] = 500  # e.g. an answer saved by an older version
    app.setup._save(state)
    app.handle_update(press("q:done", update_id=99))
    assert tg.sent()[-1]["text"].startswith("⚠ Couldn't save: weight")


def test_a_quote_in_a_site_link_is_fine(app, tg, db):
    send(app, "/sites add https://shop.co.il/it's-kites")
    assert tg.sent()[0]["text"] == "Added site #1"


def test_photos_follow_the_card_as_a_reply(world_app, db):
    tg = world_app.api.transport
    world_app.runner()
    tg.clear()
    tg._next_id = 999  # the card gets message 1000
    world_app.send_alerts(limit=1)
    (card, photo) = tg.calls
    assert card[0] == "sendMessage" and photo[0] in ("sendPhoto", "sendMediaGroup")
    assert photo[1]["reply_parameters"] == {"message_id": 1000}


def test_flood_limit_waits_and_retries_once():
    waits, replies = (
        [],
        [
            (429, {"ok": False, "parameters": {"retry_after": 99}}),
            (200, {"ok": True, "result": {}}),
        ],
    )
    api = TelegramAPI("1:T", transport=lambda u, b, t: replies.pop(0), sleep=waits.append)
    api.send_message(1, "hi")
    assert waits == [30]  # capped
    api = TelegramAPI(
        "1:T",
        transport=lambda u, b, t: (429, {"ok": False, "description": "Too Many Requests"}),
        sleep=waits.append,
    )
    with pytest.raises(TelegramError, match="Too Many Requests"):
        api.send_message(1, "hi")


def test_alerts_are_spaced_out(world_app, monkeypatch):
    monkeypatch.setattr("kitefinder.bot.app.ALERT_GAP_S", 1.0)
    gaps = []
    world_app.sleep = gaps.append
    world_app.runner()
    assert world_app.send_alerts(limit=3) == 3 and gaps == [1.0, 1.0]


# --- your choice #23: re-alert on a price drop of 10% or more ------------------------------------


def _set_price(db, listing_id, price):
    with db.conn:
        db.conn.execute("UPDATE listings SET price_ils = ? WHERE id = ?", (price, listing_id))


def test_a_price_drop_of_10_percent_alerts_again(world_app, db):
    world_app.runner()
    world_app.send_alerts(limit=100)
    assert world_app.build_alerts() == []
    target = next(n for n in db.notifications() if n["payload"].get("price"))
    listing_id = db.get_match(target["match_id"])["listing_id"]
    price = target["payload"]["price"]
    _set_price(db, listing_id, int(price * 0.91))  # 9% cheaper: not enough
    assert world_app.build_alerts() == []
    _set_price(db, listing_id, int(price * 0.8))  # 20% cheaper
    (drop,) = world_app.build_alerts()
    assert drop.listing_id == listing_id
    assert (
        drop.text.splitlines()[0] == f"📉 <b>Price dropped</b> ₪{price:,} → ₪{int(price * 0.8):,}"
    )
    assert drop.text.splitlines()[1].startswith(("🪁", "🦺", "🏄", "🎚️"))
    assert world_app.send_alerts() == 1
    assert world_app.build_alerts() == []  # each drop is sent once
    _set_price(db, listing_id, int(price * 0.8 * 0.85))  # a further 15% drop
    assert len(world_app.build_alerts()) == 1


def test_dismissed_sold_or_unpriced_listings_are_not_realerted(world_app, db):
    world_app.runner()
    world_app.send_alerts(limit=100)
    priced = [n for n in db.notifications() if n["payload"].get("price")]
    ids = [db.get_match(n["match_id"])["listing_id"] for n in priced[:3]]
    for lid in ids:
        _set_price(db, lid, 100)
    db.set_mark("listing", ids[0], "dismissed")
    with db.conn:
        db.conn.execute("UPDATE listings SET sold = 1 WHERE id = ?", (ids[1],))
        db.conn.execute("UPDATE listings SET price_ils = NULL WHERE id = ?", (ids[2],))
    assert world_app.build_alerts() == []


def test_alerts_recorded_before_prices_were_stored_are_not_realerted(world_app, db):
    world_app.runner()
    world_app.send_alerts(limit=100)
    with db.conn:
        db.conn.execute("UPDATE notifications SET payload = json_remove(payload, '$.price')")
        db.conn.execute("UPDATE listings SET price_ils = 1 WHERE price_ils IS NOT NULL")
    assert world_app.build_alerts() == []


# --- regressions from the Step 8 code review ---------------------------------------------------


def test_price_drops_only_for_what_you_still_track(world_app, db):
    world_app.runner()
    world_app.send_alerts(limit=100)
    kite = next(
        n for n in db.notifications()
        if n["payload"].get("price") and db.get_match(n["match_id"])["rec_item_id"]
    )  # fmt: skip
    lid = db.get_match(kite["match_id"])["listing_id"]
    _set_price(db, lid, 100)
    assert [a.listing_id for a in world_app.build_alerts()] == [lid]
    db.set_listing_status([lid], "unmatched")  # e.g. the post was edited and no longer fits
    assert world_app.build_alerts() == []
    db.set_listing_status([lid], "matched")
    from kitefinder.models import Profile
    from kitefinder.sizing import quiver

    db.save_recommendation(quiver.recommend_set(Profile(55, 70, 25, 35), [], "minimum"))
    assert lid not in [a.listing_id for a in world_app.build_alerts()]  # a new, different set


def test_a_cross_posted_price_drop_is_one_card(world_app, db):
    world_app.runner()
    world_app.send_alerts(limit=100)
    twins = [
        x
        for x in db.candidate_listings()
        if formatter.duplicate_key(x) == ("kite", "North", 12.0, "", 3200)
    ]
    assert len(twins) == 2
    for x in twins:
        _set_price(db, x.id, 2500)  # the seller lowered it on both sites
    drops = world_app.build_alerts()
    assert len(drops) == 1 and drops[0].text.startswith("📉 <b>Price dropped</b> ₪3,200 → ₪2,500")


def test_a_match_replaced_while_its_card_goes_out_is_still_recorded(world_app, db, monkeypatch):
    world_app.runner()
    (alert,) = world_app.build_alerts()[:1]
    with db.conn:
        db.conn.execute("PRAGMA foreign_keys = ON")
        db.conn.execute(
            "DELETE FROM matches WHERE id = ?", (alert.match_ids[0],)
        )  # the search pruned it
    monkeypatch.setattr(world_app, "build_alerts", lambda: [alert])
    assert world_app.send_alerts() == 1
    (note,) = db.notifications(1)
    assert note["match_id"] is None and note["payload"]["listing_id"] == alert.listing_id


# --- choosing several spots from the list ------------------------------------------------------


def test_pick_spots_from_several_regions(app, tg, db):
    from kitefinder.sizing import spots

    names = [s.name for s in spots.load_spots()[0]]
    idx = {n: i for i, n in enumerate(names)}
    send(app, "/setup", "80", "86", ("press", "q:intermediate"), ("press", "q:surfboard"))
    tg.clear()
    send(app, ("press", "q:north"), ("press", f"q:spot:{idx['Bat Galim']}"),
         ("press", f"q:spot:{idx['Atlit']}"), ("press", f"q:spot:{idx['Atlit']}"),  # untick
         ("press", "q:regions"), ("press", "q:center"), ("press", f"q:spot:{idx['Herzliya']}"),
         ("press", "q:done"))  # fmt: skip
    golden.check("setup_spot_picker", tg.transcript())
    edits = tg.sent("editMessageText")
    assert len(edits) == 7 and all(e["message_id"] == 7 for e in edits)  # one message, updated
    assert tg.sent()[-1]["text"].startswith("Which season's wind")  # a new question after Done
    assert app.setup.state()["draft"]["spots"] == ["Bat Galim", "Herzliya"]


def test_all_of_a_region_and_typed_spots_add_to_the_ticked_ones(app, tg):
    send(app, "/setup", "80", "86", ("press", "q:intermediate"), ("press", "q:surfboard"),
         ("press", "q:all:eilat"), "Bat Galim and Herzliya")  # fmt: skip
    assert app.setup.state()["draft"]["spots"] == ["Eilat North Beach", "Bat Galim", "Herzliya"]


def test_done_with_nothing_picked_and_a_typo_keep_the_ticks(app, tg):
    send(
        app,
        "/setup",
        "80",
        "86",
        ("press", "q:intermediate"),
        ("press", "q:foil"),
        ("press", "q:done"),
    )
    assert tg.sent()[-1]["text"].startswith("⚠ pick at least one spot — or type them")
    send(app, ("press", "q:all:kinneret"), "bat galm")
    assert "did you mean Bat Galim?" in tg.sent()[-1]["text"]
    from kitefinder.sizing import spots

    kinneret = [s.name for s in spots.load_spots()[0] if s.region == "kinneret"]
    assert kinneret and app.setup.state()["draft"]["picked"] == kinneret  # typo kept the ticks
    send(app, ("press", "q:done"))
    assert app.setup.state()["step"] == "season"


def test_an_old_spot_list_that_cannot_be_edited_is_sent_again(db):
    tg = FakeTelegram(fail_methods={"editMessageText"})
    app = BotApp(db, tg.api(), owner_chat=str(OWNER))
    send(
        app,
        "/setup",
        "80",
        "86",
        ("press", "q:intermediate"),
        ("press", "q:foil"),
        ("press", "q:north"),
    )
    assert tg.sent()[-1]["text"].startswith("North (צפון) — tap the spots you ride")
