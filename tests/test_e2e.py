"""Family 4 (going through everything): a new user, from /start to the second day of alerts.

Everything real runs — the questionnaire, the recommendation, collection from every source,
reading, matching, alert sending, the buttons, the daemon's schedule and the audit — against
the fake world (shop, Yad2, Facebook group, Marketplace) and a fake Telegram.
"""

import golden
from fakebot import FakeTelegram, msg, press
from world import CATEGORY, World

from kitefinder import audit
from kitefinder.bot.app import BotApp
from kitefinder.daemon import Daemon
from kitefinder.db import Database


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


SETUP = [
    "/start", "80", "86", ("press", "q:intermediate"), ("press", "q:twintip"), ("press", "q:freeride"), "12-25",
    ("press", "q:done"), ("press", "q:skip"), ("press", "q:both"), ("press", "q:skip"),
    ("press", "q:skip"), CATEGORY, ("press", "q:done"),
]  # fmt: skip


def cards(tg):
    return [b for b in tg.sent() if b.get("parse_mode") == "HTML"]


def test_everything_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("kitefinder.bot.app.ALERT_GAP_S", 0)
    db = Database(tmp_path / "kitefinder.db")  # a fresh install
    world, tg, clock = World(), FakeTelegram(), Clock()
    bot = BotApp(db, tg.api(), owner_chat="42")  # TELEGRAM_CHAT_ID from .env
    agent = Daemon(
        db, world.settings(), world.fetchers(), None, bot, tmp_path / "bk", clock, lambda s: None
    )

    # 1. the questionnaire, then the recommendation from its button
    for i, step in enumerate(SETUP, 1):
        bot.handle_update(
            press(step[1], update_id=i) if isinstance(step, tuple) else msg(step, update_id=i)
        )
    bot.handle_update(press("cmd:recommend", update_id=50))
    assert bot.owner == "42" and db.get_profile().wind_max_kn == 25
    assert [s["url"] for s in db.list_sites()] == [CATEGORY]
    rec = db.latest_recommendation()
    assert rec is not None and {i.type for i in rec.items} >= {"kite", "bar", "board", "harness"}
    setup_transcript = tg.transcript()
    tg.clear()

    # 2. the first scheduled round: every source, then alerts
    agent.tick()
    runs = [r["id"] for r in db.conn.execute("SELECT id FROM runs ORDER BY id")]
    assert len(runs) == 3
    for run_id in runs:
        result = audit.audit(db, run_id, alerts_sent=True)
        assert result.ok, audit.format_audit(result)
        assert result.unaccounted == 0 and result.pending_alerts == 0
    first = cards(tg)
    assert len(first) >= 5
    notified = {n["match_id"] for n in db.notifications()}
    all_matches = {r["id"] for r in db.conn.execute("SELECT id FROM matches")}
    assert notified == all_matches  # every match was sent (folded duplicates included)
    for card in first:  # every card: price, location, description, condition, source, buttons
        lines = card["text"].splitlines()
        assert [ln[:1] for ln in lines[1:6]] == ["💰", "📍", "📝", "🔍", "🌐"]
        assert card["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("fav:")
    first_transcript = tg.transcript()

    # 3. favorite one, dismiss another
    fav_id = int(first[0]["reply_markup"]["inline_keyboard"][0][0]["callback_data"][4:])
    dis_id = int(first[1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"][4:])
    bot.handle_update(press(f"fav:{fav_id}", update_id=60))
    bot.handle_update(press(f"dis:{dis_id}", update_id=61))
    assert (
        db.get_mark("listing", fav_id) == "favorite"
        and db.get_mark("listing", dis_id) == "dismissed"
    )

    # 4. the next day: a Yad2 price drop, everything runs again — no repeated alerts
    for url, text in world.texts.items():
        if url.startswith("https://y2.example"):
            world.texts[url] = text.replace('"price": 2500', '"price": 2300')
    tg.clear()
    clock.t += 24 * 3600
    agent.tick()
    assert cards(tg) == []
    assert any(listing.price_ils == 2300 for listing in db.candidate_listings())
    assert dis_id not in {c.listing_id for c in bot.build_alerts()}
    assert db.listing_status(dis_id) == "dismissed"
    latest = audit.audit(db, None, alerts_sent=True)
    assert latest.ok, audit.format_audit(latest)
    assert (
        sorted(p.name for p in (tmp_path / "bk").glob("*.db"))
        and len(list((tmp_path / "bk").glob("*.db"))) == 2
    )

    # 5. favorites, history and the report through the bot
    tg.clear()
    for i, text in enumerate(["/favorites", "/history", "/report"], 70):
        bot.handle_update(msg(text, update_id=i))
    favs, history, report = (b["text"] for b in tg.sent())
    assert favs.startswith("⭐ Your favorites:\n• ") and favs.count("\n• ") == 1
    assert [ln.split("[")[1].split("]")[0] for ln in history.splitlines()] == [
        "set, one_kite", "set, comfortable", "set, minimum"]  # fmt: skip
    assert f"#{rec.id} " in history
    assert report.startswith(f"Run #{runs[-1] + 3}: ✓ all accounted for")

    golden.check("e2e_setup", setup_transcript)
    golden.check("e2e_first_alerts", first_transcript)
