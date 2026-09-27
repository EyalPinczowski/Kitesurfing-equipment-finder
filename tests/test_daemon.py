"""The always-on agent: schedule, retries, problem messages, backups, the loop."""

import pytest
from fakebot import FakeTelegram, msg
from world import CATEGORY, World

from kitefinder import cli, daemon
from kitefinder.bot.app import BotApp
from kitefinder.daemon import Daemon
from kitefinder.models import Profile
from kitefinder.sizing import quiver


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def setup(db, tmp_path, monkeypatch):
    monkeypatch.setattr("kitefinder.bot.app.ALERT_GAP_S", 0)
    world = World()
    profile = Profile(80, 86, 12, 25)
    db.save_profile(profile)
    db.save_recommendation(quiver.recommend_set(profile, [], "minimum"))
    db.add_site(CATEGORY)
    tg = FakeTelegram()
    bot = BotApp(db, tg.api(), owner_chat="42")
    clock, sleeps = Clock(), []
    d = Daemon(
        db, world.settings(), world.fetchers(), None, bot, tmp_path / "bk", clock, sleeps.append
    )
    return d, tg, clock, sleeps, world


def test_first_round_runs_every_source_then_waits_for_each_interval(setup, db):
    d, tg, clock, _, _ = setup
    assert d.due() == ["sites", "yad2", "facebook"]
    d.tick()
    assert d.due() == []
    assert [r["trigger"] for r in db.conn.execute("SELECT trigger FROM runs")] == ["schedule"] * 3
    clock.t += 61 * 60  # yad2 every 60 min
    assert d.due() == ["yad2"]
    clock.t += 60 * 60  # facebook every 120
    assert d.due() == ["yad2", "facebook"]
    clock.t += 60 * 60  # sites every 180
    assert d.due() == ["sites", "yad2", "facebook"]


def test_alerts_go_out_after_the_runs_and_only_once(setup, db):
    d, tg, clock, _, _ = setup
    d.tick()
    cards = [b for b in tg.sent() if b.get("parse_mode") == "HTML"]
    assert cards and len(db.notifications()) >= len(cards)
    tg.clear()
    clock.t += 4 * 3600
    d.tick()  # everything runs again: nothing new to alert
    assert [b for b in tg.sent() if b.get("parse_mode") == "HTML"] == []


def test_a_broken_source_is_told_once_retried_sooner_and_reported_fixed(setup, db):
    d, tg, clock, _, world = setup
    saved = {u: t for u, t in world.texts.items() if u.startswith("https://y2.example")}
    for u in saved:
        del world.texts[u]  # Yad2 is down
    d.tick()
    problems = [b["text"] for b in tg.sent() if b["text"].startswith("⚠ yad2")]
    assert len(problems) == 1 and problems[0].endswith(
        "I'll keep trying. /report shows the details."
    )
    clock.t += 16 * 60
    assert d.due() == ["yad2"]  # retried after 15 min, not the full hour
    tg.clear()
    d.tick()  # still broken: no second message
    assert not [b for b in tg.sent() if b["text"].startswith("⚠")]
    world.texts.update(saved)
    clock.t += 16 * 60
    d.tick()
    assert "✓ yad2: קייט works again." in [b["text"] for b in tg.sent()]


def test_disabled_sources_are_skipped(setup):
    d, *_ = setup
    d.settings.sources["facebook"]["enabled"] = False
    assert d.due() == ["sites", "yad2"]


def test_daily_backup_keeps_the_last_seven(setup, tmp_path):
    d, _, clock, _, _ = setup
    made = []
    for _ in range(9):
        made.append(d.backup_if_due())
        assert d.backup_if_due() is None  # not twice a day
        clock.t += 24 * 3600
    kept = sorted(p.name for p in (tmp_path / "bk").glob("*.db"))
    assert len(kept) == 7 and kept == sorted(p.name for p in made[-7:])


def test_run_from_telegram_runs_everything(setup, db):
    d, tg, _, _, _ = setup
    d.bot.handle_update(msg("/run"))
    assert [r["trigger"] for r in db.conn.execute("SELECT trigger FROM runs")] == ["bot"]
    assert d.due() == []  # every source counts as just run
    assert tg.sent()[-1]["text"].startswith("Run #1: ✓ all accounted for")


def test_poll_backs_off_when_telegram_is_unreachable(setup):
    d, tg, _, sleeps, _ = setup
    tg.fail_methods = {"getUpdates"}
    for _ in range(8):
        d.poll()
    assert sleeps == [5, 10, 20, 40, 80, 160, 300, 300]
    tg.fail_methods = set()
    d.poll()
    assert d.backoff == 0


def test_a_crash_in_one_round_does_not_stop_the_agent(setup, monkeypatch):
    d, tg, _, _, _ = setup
    monkeypatch.setattr(d, "due", lambda: 1 / 0)
    d.loop(rounds=2)  # no exception
    assert tg.sent("getUpdates")


def test_without_a_bot_the_loop_just_waits(db, tmp_path):
    world = World()
    sleeps = []
    d = Daemon(
        db,
        world.settings(),
        world.fetchers(),
        backup_dir=tmp_path,
        clock=Clock(),
        sleep=sleeps.append,
    )
    d.loop(rounds=1)
    assert (
        sleeps == [daemon.POLL_TIMEOUT_S]
        and db.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
    )
    d.stop()
    d.loop()  # returns at once


def test_build_wires_the_bot_the_miniapp_and_the_tunnel(tmp_path, monkeypatch):
    world = World()
    settings = world.settings()
    settings.data_dir = tmp_path
    settings.telegram_bot_token = "123:TOKEN"
    settings.telegram_chat_id = "42"
    tg = FakeTelegram()
    started = {}

    def fake_tunnel(port):
        started["port"] = port
        return "proc", "https://x-y.trycloudflare.com"

    d = daemon.build(settings, port=0, tunnel_start=fake_tunnel, api=tg.api())
    assert d.bot.owner == "42" and started["port"] > 0 and d.tunnel == "proc"
    (menu,) = tg.sent("setChatMenuButton")
    assert (
        "chat_id" not in menu
        and menu["menu_button"]["web_app"]["url"] == "https://x-y.trycloudflare.com"
    )
    assert tg.sent("setMyCommands")

    def no_tunnel(port):
        raise RuntimeError("cloudflared is not installed — in Termux: pkg install cloudflared")

    tg.clear()
    daemon.build(settings, port=0, tunnel_start=no_tunnel, api=tg.api())
    assert (
        tg.sent()[-1]["text"]
        == "ℹ️ The Mini App is off: cloudflared is not installed — in Termux: pkg install cloudflared"
        "\nI'll try again every 30 minutes."
    )


def test_daemon_command(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        daemon,
        "main",
        lambda settings, once, miniapp, port: (
            seen.update(once=once, miniapp=miniapp, port=port) or 0
        ),
    )
    assert cli.main(["daemon", "--once", "--no-miniapp", "--port", "9000"]) == 0
    assert seen == {"once": True, "miniapp": False, "port": 9000}
    assert cli.run(["daemon"]) == "The agent runs from the phone's shell: kitefinder daemon"


class Proc:
    def __init__(self):
        self.alive, self.terminated = True, False

    def poll(self):
        return None if self.alive else 1

    def terminate(self):
        self.terminated, self.alive = True, False


def test_a_dead_tunnel_is_restarted_and_a_failed_one_retried(tmp_path):
    world = World()
    settings = world.settings()
    settings.data_dir, settings.telegram_bot_token, settings.telegram_chat_id = (
        tmp_path,
        "123:TOKEN",
        "42",
    )
    tg, procs, fail = FakeTelegram(), [], {"on": False}

    def start(port):
        if fail["on"]:
            raise RuntimeError("no network")
        procs.append(Proc())
        return procs[-1], f"https://t{len(procs)}.trycloudflare.com"

    d = daemon.build(settings, port=0, tunnel_start=start, api=tg.api())
    d.clock = clock = Clock()
    d.keep_tunnel()
    assert len(procs) == 1  # running: left alone
    procs[0].alive = False
    fail["on"] = True
    d.keep_tunnel()  # died; restarting fails (no network)
    assert d.tunnel is None and tg.sent()[-1]["text"].startswith(
        "ℹ️ The Mini App is off: no network"
    )
    d.keep_tunnel()
    assert len(tg.sent("sendMessage")) == 1  # not retried before 30 min, said once
    fail["on"] = False
    clock.t += 31 * 60
    d.keep_tunnel()
    assert len(procs) == 2 and d.tunnel is procs[1]
    assert (
        tg.sent("setChatMenuButton")[-1]["menu_button"]["web_app"]["url"]
        == "https://t2.trycloudflare.com"
    )


def test_menu_button_failure_stops_the_new_tunnel(tmp_path):
    world = World()
    settings = world.settings()
    settings.data_dir, settings.telegram_bot_token = tmp_path, "123:TOKEN"
    tg = FakeTelegram(fail_methods={"setChatMenuButton", "setMyCommands"})  # e.g. offline at boot
    proc = Proc()
    d = daemon.build(
        settings,
        port=0,
        tunnel_start=lambda port: (proc, "https://a.trycloudflare.com"),
        api=tg.api(),
    )
    assert proc.terminated and d.tunnel is None  # no orphaned cloudflared; the daemon still starts


# --- regressions found by the Step 7 code review ------------------------------------------------


def test_one_broken_shop_does_not_rerun_every_shop_every_15_minutes(setup, db):
    d, tg, clock, _, world = setup
    db.add_site("https://gone.example/shop/")  # a second site that is down
    d.tick()
    assert any(b["text"].startswith("⚠ https://gone.example/shop/") for b in tg.sent())
    clock.t += 16 * 60
    assert "sites" not in d.due()  # the other shop worked: normal 3 h interval
    clock.t += 3 * 3600
    assert "sites" in d.due()


def test_expired_cookies_wait_the_normal_interval_and_are_told_again_next_time(
    setup, db, monkeypatch
):
    from kitefinder.pipeline import SourceReport

    d, tg, clock, _, _ = setup
    real_collect = daemon.pipeline.collect
    expired = {"on": True}

    def collect(db_, settings, fetchers, run_id, only=None):
        if only == "facebook" and expired["on"]:
            return [
                SourceReport(
                    "facebook",
                    complete=False,
                    errors=["Facebook: log in required — cookies expired"],
                    login_required=True,
                )
            ]
        return real_collect(db_, settings, fetchers, run_id, only)

    monkeypatch.setattr(daemon.pipeline, "collect", collect)
    said = lambda: [b["text"] for b in tg.sent() if b["text"][:1] in "⚠✓"]  # noqa: E731
    d.tick()
    assert said() == [
        "⚠ facebook: Facebook: log in required — cookies expired\nI'll keep trying. /report shows the details."
    ]
    clock.t += 16 * 60
    assert "facebook" not in d.due()  # retrying won't fix cookies
    expired["on"] = False
    clock.t += 2 * 3600
    tg.clear()
    d.tick()
    assert said() == ["✓ facebook works again."]
    expired["on"] = True
    clock.t += 2 * 3600
    tg.clear()
    d.tick()
    assert said()[0].startswith("⚠ facebook: ")  # expired again: told again


def test_a_busy_port_leaves_the_bot_running(tmp_path):
    import socket

    taken = socket.socket()
    taken.bind(("127.0.0.1", 0))
    taken.listen()
    port = taken.getsockname()[1]
    try:
        world = World()
        settings = world.settings()
        settings.data_dir, settings.telegram_bot_token, settings.telegram_chat_id = (
            tmp_path,
            "123:TOKEN",
            "42",
        )
        tg = FakeTelegram()
        d = daemon.build(settings, port=port, tunnel_start=lambda p: 1 / 0, api=tg.api())
        assert d.bot is not None and d.start_tunnel is None
        assert tg.sent()[-1]["text"].startswith(f"ℹ️ The Mini App is off: port {port} is busy")
    finally:
        taken.close()


def test_shutdown_stops_the_tunnel(setup):
    d, *_ = setup
    d.tunnel = proc = Proc()
    d.shutdown()
    assert proc.terminated and d.tunnel is None


def test_a_failing_step_does_not_skip_the_others(setup, monkeypatch):
    d, *_ = setup
    monkeypatch.setattr(d.bot, "send_alerts", lambda: 1 / 0)
    d.tick()
    assert d.db.meta_get("daemon_backup")  # the backup still ran after alerts failed


def test_main_stops_cleanly(monkeypatch, tmp_path):
    world = World()
    settings = world.settings()
    settings.data_dir = tmp_path
    built = {}

    def fake_build(settings, port, miniapp):
        d = daemon.Daemon(__import__("kitefinder.db", fromlist=["Database"]).Database(tmp_path / "x.db"),
                          settings, world.fetchers(), sleep=lambda s: None)  # fmt: skip
        d.tunnel = Proc()
        d.loop = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
        built["d"] = d
        return d

    monkeypatch.setattr(daemon, "build", fake_build)
    monkeypatch.setattr(daemon.signal, "signal", lambda *a: None)
    assert daemon.main(settings) == 0
    assert built["d"].tunnel is None  # cloudflared stopped
    with pytest.raises(SystemExit):
        daemon._exit_now(15, None)
    assert daemon.main(settings, once=True) == 0
