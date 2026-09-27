"""The Telegram bot: commands, the questionnaire, alert sending and button presses.

Commands reuse the CLI (`kitefinder …`) so the bot and the terminal always say the same
thing. Only your own chat may use it: TELEGRAM_CHAT_ID (your choice: no chat id, nobody).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable

from .. import cli
from ..db import Database
from ..models import ValidationError
from .formatter import Alert, duplicate_key, format_alert, html_to_text
from .questionnaire import Questionnaire, Reply
from .telegram import TelegramAPI, TelegramError, button

OFFSET_KEY = "tg_offset"
ALERTS_PER_RUN = 20  # the rest stay pending for the next round (Telegram flood limits)
ALERT_GAP_S = 1.0  # Telegram allows about one message a second per chat
PRICE_DROP = 0.10  # re-alert an alerted listing when its price falls this much (your choice)

COMMANDS = [
    ("setup", "answer a few questions to set up your profile"),
    ("recommend", "the three quiver options with prices"),
    ("under", "best set under a price, e.g. /under 9000"),
    ("assemble", "cheapest set from real listings: /assemble mixed|same|kites_bar"),
    ("search", "search listings, e.g. /search kite 12m"),
    ("watch", "get alerts for a search, e.g. /watch harness M"),
    ("favorites", "your favorite listings"),
    ("history", "past recommendations"),
    ("profile", "your profile"),
    ("gear", "gear you own"),
    ("sites", "websites searched: /sites, /sites add <link>, /sites rm <id>"),
    ("run", "search all sources now"),
    ("report", "what the last run found and whether anything was missed"),
    ("cancel", "stop the setup questions"),
    ("help", "all commands"),
]

# /command → CLI arguments (text after the command is appended where it makes sense)
CLI_COMMANDS: dict[str, Callable[[str], list[str]]] = {
    "recommend": lambda rest: ["recommend"] + (["--option", rest] if rest else []),
    "under": lambda rest: ["recommend", "--under"] + ([rest.replace(",", "")] if rest else []),
    "assemble": lambda rest: ["assemble"] + (["--brands", rest] if rest else []),
    "search": lambda rest: ["search", rest],
    "watch": lambda rest: ["watch", "add", rest] if rest else ["watch", "list"],
    "unwatch": lambda rest: ["watch", "rm", rest],
    "history": lambda rest: ["history"],
    "profile": lambda rest: ["profile", "show"],
    "gear": lambda rest: ["gear", "list"],
    "sites": lambda rest: ["sites"] + (rest.split() if rest else ["list"]),
    "report": lambda rest: ["report"],
}


class BotApp:
    def __init__(
        self,
        db: Database,
        api: TelegramAPI,
        owner_chat: str | None = None,
        runner: Callable[[], object] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.db, self.api, self.runner, self.sleep = db, api, runner, sleep
        self.is_busy: Callable[[], bool] = lambda: False  # a search running in the background
        self.setup = Questionnaire(db)
        self._owner = str(owner_chat) if owner_chat else None

    # --- who may talk to the bot --------------------------------------------------------------

    @property
    def owner(self) -> str | None:
        return self._owner

    def _allowed(self, chat_id) -> bool:
        return self.owner is not None and self.owner == str(chat_id)

    # --- sending ------------------------------------------------------------------------------

    def say(self, text: str, buttons: list[list[dict]] | None = None, html: bool = False) -> None:
        text = text if text.strip() else "(nothing to show)"  # Telegram rejects empty messages
        for i in range(0, max(len(text), 1), 4000):  # Telegram's limit is 4096 per message
            last = i + 4000 >= len(text)
            self.api.send_message(self.owner, text[i : i + 4000], buttons if last else None, html)

    def _replies(self, replies: list[Reply], message_id: int | None = None) -> None:
        for r in replies:
            if r.edit and message_id is not None:
                try:
                    self.api.edit_message(self.owner, message_id, r.text, r.buttons)
                    continue
                except TelegramError:  # e.g. the message is too old to edit: send it anew
                    pass
            self.say(r.text, r.buttons or None)

    def _cli(self, argv: list[str]) -> str:
        try:
            return cli.run(argv, db=self.db)
        except ValidationError as e:
            return f"⚠ {e}"
        except SystemExit:  # argparse: bad arguments
            return "⚠ I didn't understand that. Try /help"

    # --- updates ------------------------------------------------------------------------------

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            return self._on_button(update["callback_query"])
        msg = update.get("message") or {}
        chat = (msg.get("chat") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if chat is None or not text:
            return None
        if not self._allowed(chat):
            self.api.send_message(chat, "This bot is private.")
            return None
        if text.startswith("/"):
            return self._command(text)
        if self.setup.active:
            return self._replies(self.setup.answer(text))
        self.say("Send /help for what I can do — or e.g. /search kite 12m")
        return None

    def _command(self, text: str) -> None:
        name, _, rest = text[1:].partition(" ")
        name, rest = name.split("@")[0].lower(), rest.strip()
        if name in ("start", "setup"):
            if name == "start" and self.db.get_profile() is not None:
                return self.say("Welcome back! /help lists what I can do.")
            return self._replies([self.setup.start()])
        if name == "cancel":
            return self._replies([self.setup.cancel()])
        if name == "help":
            return self.say("\n".join(f"/{c} — {d}" for c, d in COMMANDS))
        if name == "favorites":
            return self.say(self.favorites_text())
        if name == "run":
            return self.run_now()
        if name in CLI_COMMANDS:
            if name in ("search", "unwatch") and not rest:
                return self.say(f"What should I {name}? e.g. /{name} kite 12m")
            return self.say(self._cli(CLI_COMMANDS[name](rest)))
        return self.say("Unknown command. /help lists what I can do.")

    def _on_button(self, cq: dict) -> None:
        chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
        data = cq.get("data") or ""
        if str(chat) != self.owner:
            self.api.answer_callback(cq["id"], "This bot is private.")
            return
        kind, _, value = data.partition(":")
        if kind in ("fav", "dis"):
            status = "favorite" if kind == "fav" else "dismissed"
            try:
                self.db.set_mark("listing", int(value), status)
            except (ValidationError, ValueError):
                self.api.answer_callback(cq["id"], "That listing is gone.")
                return
            self.api.answer_callback(
                cq["id"], "⭐ Saved to favorites" if kind == "fav" else "Hidden"
            )
            done = "⭐ In favorites" if kind == "fav" else "🚫 Dismissed — won't show again"
            self.api.edit_buttons(chat, cq["message"]["message_id"], [[button(done, "noop:")]])
            return
        self.api.answer_callback(cq["id"])
        if kind == "q":
            message_id = (cq.get("message") or {}).get("message_id")
            self._replies(self.setup.answer(value, pressed=True), message_id)
        elif kind == "cmd" and value in CLI_COMMANDS:
            self.say(self._cli(CLI_COMMANDS[value]("")))
        elif kind == "run":
            self.run_now()

    # --- runs and alerts ----------------------------------------------------------------------

    def run_now(self) -> None:
        if self.runner is None:
            return self.say(
                "Searching isn't available here — run `kitefinder daemon` on the phone."
            )
        if self.is_busy():
            return self.say("A search is already running — I'll send what it finds.")
        self.say("🔎 Searching all sources — this can take a few minutes…")
        if self.runner() == "background":
            return None  # still answering you; the report comes when the search is done
        return self.report_run()

    def report_run(self) -> None:
        """After a search: its alerts, then the report."""
        sent = self.send_alerts()
        self.say(self._cli(["report"]) + f"\n\n📨 {sent} new alert{'s' if sent != 1 else ''} sent.")

    def build_alerts(self) -> list[Alert]:
        """One alert per item: likely duplicates on other sources are folded into it."""
        from ..pipeline import alert_targets

        items, queries = alert_targets(self.db)
        groups: dict[object, list[tuple]] = {}
        for match in self.db.pending_matches(items, queries):
            listing = self.db.get_listing(match["listing_id"])
            key = duplicate_key(listing) or ("single", listing.id)
            groups.setdefault(key, []).append((listing, match))
        alerts = []
        for members in groups.values():
            listing, match = members[0]
            photos = [u for u in self.db.listing_images(listing.id) if u.startswith("http")]
            others = [other for other, _ in members[1:]]
            alert = format_alert(listing, match, photos, self.db.get_assessment(listing.id), others)
            alert.match_ids = [m["id"] for _, m in members]
            alerts.append(alert)
        folded: set = set()  # the same item cross-posted and cheaper on both: one card
        for drop in self.db.price_drops(PRICE_DROP, items, queries):  # alerted, now cheaper
            listing, match = (
                self.db.get_listing(drop["listing_id"]),
                self.db.get_match(drop["match_id"]),
            )
            key = duplicate_key(listing)
            if key is not None and key in folded:
                continue
            folded.add(key)
            photos = [u for u in self.db.listing_images(listing.id) if u.startswith("http")]
            assessment = self.db.get_assessment(listing.id)
            alerts.append(format_alert(listing, match, photos, assessment, (), drop["old_price"]))
        return alerts

    def send_alerts(self, limit: int = ALERTS_PER_RUN) -> int:
        """The card first (it is what counts as sent), then its photos as a reply to it."""
        sent = 0
        for n, alert in enumerate(self.build_alerts()[:limit]):
            if n:
                self.sleep(ALERT_GAP_S)
            try:
                card = self.api.send_message(self.owner, alert.text, alert.buttons, html=True)
            except TelegramError:
                try:  # e.g. markup Telegram refused: the same card as plain text
                    card = self.api.send_message(
                        self.owner, html_to_text(alert.text), alert.buttons
                    )
                except TelegramError:
                    continue  # not recorded: it is tried again next round
            photos = alert.photos
            if photos:
                try:
                    self.api.send_media_group(self.owner, photos, (card or {}).get("message_id"))
                except TelegramError:
                    photos = []  # a photo link Telegram can't fetch: the card is enough
            payload = {
                "text": alert.text,
                "photos": photos,
                "buttons": alert.buttons,
                "price": alert.price_ils,  # a later drop of 10%+ from this price alerts again
            }
            for match_id in alert.match_ids:
                try:
                    self.db.record_notification(match_id, payload)
                except sqlite3.IntegrityError:  # the search just replaced this match
                    self.db.record_notification(None, {**payload, "listing_id": alert.listing_id})
            sent += 1
        return sent

    def favorites_text(self) -> str:
        marks = self.db.list_marks("favorite", "listing")
        if not marks:
            return "No favorites yet — tap ✅ Favorite on an alert."
        lines = ["⭐ Your favorites:"]
        for m in marks:
            listing = self.db.get_listing(m["target_id"])
            if listing is None:
                continue
            lines.append(
                f"• {cli.listing_summary(listing)}" + (f"\n  {listing.url}" if listing.url else "")
            )
        return "\n".join(lines)

    # --- polling ------------------------------------------------------------------------------

    def poll_once(self, timeout: int = 50) -> int:
        offset = self.db.meta_get(OFFSET_KEY)
        updates = self.api.get_updates(int(offset) if offset else None, timeout)
        for upd in updates:
            self.db.meta_set(OFFSET_KEY, str(upd["update_id"] + 1))  # never handle one twice
            try:
                self.handle_update(upd)
            except TelegramError:
                continue
        return len(updates)

    def register_commands(self) -> None:
        self.api.set_commands(COMMANDS)
