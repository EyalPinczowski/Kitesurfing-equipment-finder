"""The always-on agent for Termux: scheduled searches, the Telegram bot, daily backups.

One process does everything, so the phone only runs one thing:
- each source (websites, Yad2, Facebook) runs on its own interval from config/sources.yaml;
  a source that failed is retried sooner (RETRY_AFTER_MIN)
- searches run in a background thread (your choice, #31), so the bot keeps answering while
  a long Facebook run is going; new matches are sent as alerts when it finishes
- a problem with a source (expired cookies, a changed page format, a site down) is told to
  you once, and again when it works again
- a daily database backup, keeping the last BACKUPS_KEPT
- the Mini App server and its tunnel, when a bot token is set
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from . import pipeline
from .db import Database

log = logging.getLogger("kitefinder.daemon")

SOURCES = ("sites", "yad2", "facebook")
DEFAULT_EVERY_MIN = {"sites": 180, "yad2": 60, "facebook": 120}
RETRY_AFTER_MIN = 15
BACKUP_EVERY_S = 24 * 3600
BACKUPS_KEPT = 7
POLL_TIMEOUT_S = 25  # Telegram holds the request open this long when there's nothing new
MAX_BACKOFF_S = 300
TUNNEL_RETRY_S = 30 * 60


PROBLEMS_KEY = "daemon_problems"


def family_of(name: str) -> str:
    """Which scheduled source a report belongs to ('yad2: קייט' → yad2)."""
    if name.startswith(("facebook", "marketplace")):
        return "facebook"
    return "yad2" if name.startswith("yad2") else "sites"


def _last_key(source: str) -> str:
    return f"daemon_last:{source}"


class Daemon:
    def __init__(
        self,
        db: Database,
        settings,
        fetchers,
        client=None,
        bot=None,
        backup_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        background: bool = False,
        client_factory: Callable[[Database], object] | None = None,
    ):
        """`background`: searches run in a worker thread with its own database connection
        (and its own Gemini client from `client_factory`, which is bound to that connection)."""
        self.db, self.settings, self.fetchers, self.client = db, settings, fetchers, client
        self.background, self.client_factory = background, client_factory
        self.worker: threading.Thread | None = None
        self.worker_result: tuple[str, object] | None = None
        self.worker_sources: list[str] = []
        self.report_when_done = False  # /run asked: send the report when the run finishes
        self.bot, self.clock, self.sleep = bot, clock, sleep
        self.backup_dir = Path(backup_dir or Path(settings.data_dir) / "backups" / "daily")
        self.stopping = False
        self.backoff = 0.0
        self.tunnel = None  # the cloudflared process, when the Mini App is on
        self.start_tunnel: Callable[[], None] | None = None
        self.tunnel_retry_at = 0.0
        self.tunnel_told = False  # "the Mini App is off" is said once
        if bot is not None:
            bot.runner = self.run_all
            bot.is_busy = lambda: self.busy

    # --- scheduling -----------------------------------------------------------------------

    def interval_s(self, source: str) -> float:
        schedule = (self.settings.sources or {}).get("schedule") or {}
        minutes = schedule.get(f"{source}_every_min", DEFAULT_EVERY_MIN[source])
        return float(minutes) * 60

    def enabled(self, source: str) -> bool:
        if source == "sites":
            return True
        return bool(((self.settings.sources or {}).get(source) or {}).get("enabled", True))

    def next_due(self, source: str) -> float:
        raw = self.db.meta_get(_last_key(source))
        if not raw:
            return 0.0  # never ran: due now
        last, ok = raw.split(":") if ":" in raw else (raw, "1")
        wait = self.interval_s(source) if ok == "1" else RETRY_AFTER_MIN * 60
        return float(last) + wait

    def due(self, now: float | None = None) -> list[str]:
        now = self.clock() if now is None else now
        return [s for s in SOURCES if self.enabled(s) and self.next_due(s) <= now]

    # --- runs -----------------------------------------------------------------------------

    def _run(self, db: Database, client, sources: list[str], trigger: str) -> list[tuple]:
        """The searches themselves, on `db` (the worker's own connection in the background)."""
        done = []
        for source in sources:
            only = None if source == "all" else source
            families = list(SOURCES) if only is None else [only]
            log.info("run %s", source)
            report = pipeline.run(db, self.settings, self.fetchers, client, trigger, only)
            for family in families:
                parts = [s for s in report.sources if family_of(s.name) == family]
                # retry sooner only when the whole source failed for a reason a retry can fix
                # (one broken shop, or expired cookies, wait for the normal interval)
                broken = bool(report.errors) or (
                    bool(parts) and all(s.errors and not s.login_required for s in parts)
                )
                db.meta_set(_last_key(family), f"{self.clock()}:{int(not broken)}")
            done.append((report, families))
        return done

    def run_sources(self, sources: list[str], trigger: str = "schedule") -> list:
        """Run now, in this thread (`--once`, and the daemon without background mode)."""
        done = self._run(self.db, self.client, sources, trigger)
        for report, families in done:
            self.report_problems(report, families)
        return [report for report, _ in done]

    @property
    def busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def start_run(self, sources: list[str], trigger: str = "schedule") -> bool:
        """Start the searches in the background; False if a run is already going."""
        if self.busy:
            return False
        self.worker_result = None
        self.worker_sources = list(SOURCES) if "all" in sources else list(sources)

        def work():
            db = Database(self.db.path)  # SQLite connections stay in their own thread
            try:
                client = self.client_factory(db) if self.client_factory else self.client
                self.worker_result = ("ok", self._run(db, client, sources, trigger))
            except Exception as e:  # noqa: BLE001 — reported by the main thread
                self.worker_result = ("error", e)
            finally:
                db.close()

        self.worker = threading.Thread(target=work, name="kitefinder-search", daemon=True)
        self.worker.start()
        return True

    def finish_run(self) -> bool:
        """On the main thread: once the background run is over, tell its news. True if one
        just finished."""
        if self.worker is None or self.worker.is_alive():
            return False
        self.worker = None
        status, value = self.worker_result or ("error", RuntimeError("the search stopped"))
        if status == "ok":
            for report, families in value:
                self.report_problems(report, families)
        else:  # counted as a failed run, so it's retried after RETRY_AFTER_MIN, not at once
            for family in self.worker_sources:
                self.db.meta_set(_last_key(family), f"{self.clock()}:0")
            log.error("search failed: %s", value)
            self._tell(f"⚠ The search failed: {value}\nI'll try again at the next round.")
        if self.report_when_done and self.bot is not None:
            self.report_when_done = False
            self.bot.report_run()
        return True

    def run_all(self):
        """What /run in Telegram does: every source now ("background" when it runs there)."""
        if not self.background:
            return self.run_sources(["all"], trigger="bot")
        if self.start_run(["all"], trigger="bot"):
            self.report_when_done = True
        return "background"

    def searches(self) -> None:
        """The searches step of a round: collect a finished run, start the due ones."""
        self.finish_run()
        if self.busy:
            return
        due = self.due()
        if not due:
            return
        if self.background:
            self.start_run(due)
        else:
            self.run_sources(due)

    def report_problems(self, report, families=SOURCES) -> None:
        """Tell the user about each source problem once, and once more when it's gone."""
        old = json.loads(self.db.meta_get(PROBLEMS_KEY) or "{}")
        now = {s.name: "; ".join(s.errors) for s in report.sources if s.errors}
        if report.errors:
            now["run"] = "; ".join(report.errors)
        covered = {n for n in old if n == "run" or family_of(n) in families}
        for name in sorted(covered - now.keys()):
            self._tell(f"✓ {name} works again.")
        for name, problem in now.items():
            if old.get(name) != problem:
                self._tell(f"⚠ {name}: {problem}\nI'll keep trying. /report shows the details.")
        kept = {n: p for n, p in old.items() if n not in covered}
        self.db.meta_set(PROBLEMS_KEY, json.dumps({**kept, **now}, ensure_ascii=False))

    def _tell(self, text: str) -> None:
        log.warning(text)
        if self.bot is not None and self.bot.owner:
            try:
                self.bot.say(text)
            except Exception:  # noqa: BLE001 — a message that can't go out must not stop runs
                log.exception("could not send a message")

    # --- backups --------------------------------------------------------------------------

    def backup_if_due(self, now: float | None = None) -> Path | None:
        now = self.clock() if now is None else now
        last = float(self.db.meta_get("daemon_backup") or 0)
        if now - last < BACKUP_EVERY_S:
            return None
        stamp = datetime.fromtimestamp(now).strftime("%Y%m%d-%H%M%S")
        path = self.db.backup(self.backup_dir / f"kitefinder-{stamp}.db")
        self.db.meta_set("daemon_backup", str(now))
        for old in sorted(self.backup_dir.glob("kitefinder-*.db"))[:-BACKUPS_KEPT]:
            old.unlink()
        log.info("backup %s", path)
        return path

    def keep_tunnel(self) -> None:
        """Restart the Mini App tunnel if it died, or retry one that couldn't start."""
        if self.tunnel is not None:
            if self.tunnel.poll() is None:
                return  # running
            log.warning("the Mini App tunnel stopped — starting a new one")
        elif self.clock() < self.tunnel_retry_at:
            return
        self.start_tunnel()  # a new address; the menu button follows it

    # --- the loop -------------------------------------------------------------------------

    def tick(self) -> None:
        """One round: due searches, pending alerts, backup, tunnel. Never raises."""
        steps = [("searches", self.searches)]
        if self.bot is not None:
            steps.append(("alerts", lambda: self.bot.owner and self.bot.send_alerts()))
        steps.append(("backup", self.backup_if_due))
        if self.start_tunnel is not None:
            steps.append(("tunnel", self.keep_tunnel))
        for name, step in steps:  # one failing step doesn't block the others
            try:
                step()
            except Exception:  # noqa: BLE001 — keep the agent alive; the log has the details
                log.exception("%s failed", name)

    def poll(self) -> None:
        """Answer Telegram; waits on the long poll (or just sleeps without a bot)."""
        if self.bot is None:
            self.sleep(POLL_TIMEOUT_S)
            return
        try:
            self.bot.poll_once(POLL_TIMEOUT_S)
            self.backoff = 0.0
        except Exception as e:  # noqa: BLE001 — e.g. no network: wait longer each time
            self.backoff = min(max(self.backoff * 2, 5.0), MAX_BACKOFF_S)
            log.warning("telegram: %s — retrying in %.0fs", e, self.backoff)
            self.sleep(self.backoff)

    def loop(self, rounds: int | None = None) -> None:
        n = 0
        while not self.stopping and (rounds is None or n < rounds):
            self.tick()
            self.poll()
            n += 1

    def stop(self, *_):
        self.stopping = True

    def shutdown(self) -> None:
        """Stop the tunnel (no orphaned cloudflared) and close the database. A background
        search is a daemon thread: it stops with the process (its writes are atomic)."""
        if self.tunnel is not None:
            try:
                self.tunnel.terminate()
            except Exception:  # noqa: BLE001 — already gone
                pass
            self.tunnel = None
        self.db.close()


# --- wiring it together (used by `kitefinder daemon`) --------------------------------------


def build(
    settings,
    port: int = 8787,
    miniapp: bool = True,
    tunnel_start=None,
    api=None,
    background: bool = True,
) -> Daemon:
    """`background=False` for `--once`: the round runs to the end before the process exits."""
    from .bot.app import BotApp
    from .bot.telegram import TelegramAPI
    from .llm.gemini import client_from_settings

    db = Database(settings.db_path)
    db.seed_sites(settings.sources.get("sites") or [])
    fetchers = pipeline.Fetchers(settings)
    client = client_from_settings(settings, db=db)
    bot = None
    if settings.telegram_bot_token:
        api = api or TelegramAPI(settings.telegram_bot_token)
        bot = BotApp(db, api, settings.telegram_chat_id)
        if not settings.telegram_chat_id:
            log.warning(
                "TELEGRAM_CHAT_ID is not set: the bot answers nobody. "
                "Ask @userinfobot for your id and add it to .env"
            )
        try:
            bot.register_commands()
        except Exception as e:  # noqa: BLE001 — e.g. no network yet: the menu is cosmetic
            log.warning("could not register the bot's commands: %s", e)
    daemon = Daemon(
        db,
        settings,
        fetchers,
        client,
        bot,
        background=background,
        client_factory=lambda d: client_from_settings(settings, db=d),
    )
    if bot is not None and miniapp:
        from .bot import miniapp as mini
        from .bot import tunnel

        try:
            server = mini.make_server(
                settings.db_path, settings.telegram_bot_token, bot.owner, port=port
            )
        except OSError as e:  # e.g. the port is taken by another copy still running
            daemon._tell(f"ℹ️ The Mini App is off: port {port} is busy ({e.strerror}).")
            return daemon
        mini.serve_in_background(server)

        def start_tunnel():
            proc = None
            try:
                proc, url = (tunnel_start or tunnel.start_tunnel)(server.server_address[1])
                bot.api.set_menu_button(None, url)
                daemon.tunnel = proc
                log.info("Mini App at %s", url)
            except Exception as e:  # noqa: BLE001 — the bot works without the Mini App
                if proc is not None:
                    proc.terminate()
                daemon.tunnel = None
                daemon.tunnel_retry_at = daemon.clock() + TUNNEL_RETRY_S
                if not daemon.tunnel_told:
                    daemon._tell(f"ℹ️ The Mini App is off: {e}\nI'll try again every 30 minutes.")
                    daemon.tunnel_told = True

        daemon.start_tunnel = start_tunnel
        start_tunnel()
    return daemon


def _exit_now(signum, frame):
    # SQLite writes are atomic: stopping mid-run loses nothing (the run is simply redone)
    raise SystemExit(0)


def main(settings, once: bool = False, miniapp: bool = True, port: int = 8787) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    daemon = build(settings, port=port, miniapp=miniapp and not once, background=not once)
    try:
        if once:
            daemon.tick()
            return 0
        signal.signal(signal.SIGTERM, _exit_now)  # `sv stop` / `sv restart`: stop at once
        log.info("kitefinder is running — Ctrl+C to stop")
        daemon.loop()
    except KeyboardInterrupt:
        pass
    finally:
        daemon.shutdown()
    return 0
