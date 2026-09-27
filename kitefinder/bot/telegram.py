"""A small Telegram Bot API client (plain HTTPS, no framework — light on Termux, easy to test)."""

from __future__ import annotations

import json
import time
from collections.abc import Callable

API = "https://api.telegram.org"
MAX_RETRY_WAIT_S = 30

# transport(url, json_body, timeout) -> (status, parsed json or None)
Transport = Callable[[str, dict, float], tuple[int, dict | None]]


class TelegramError(RuntimeError):
    pass


def requests_transport(url: str, body: dict, timeout: float):
    import requests

    try:
        resp = requests.post(url, json=body, timeout=timeout)
    except requests.RequestException as e:
        return 0, {"ok": False, "description": f"network error: {e}"}
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, None


class TelegramAPI:
    def __init__(
        self,
        token: str,
        transport: Transport | None = None,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not token:
            raise TelegramError("no bot token: set TELEGRAM_BOT_TOKEN in .env (from @BotFather)")
        self.token = token
        self.transport = transport or requests_transport
        self.timeout = timeout
        self.sleep = sleep

    def call(self, method: str, _wait: float = 0, **params):
        """`_wait`: how long Telegram may hold the request open (long polling)."""
        body = {k: v for k, v in params.items() if v is not None}
        url = f"{API}/bot{self.token}/{method}"
        status, data = self.transport(url, body, self.timeout + _wait)
        if status == 429:  # flood limit: wait as long as Telegram asks (capped), try once more
            wait = ((data or {}).get("parameters") or {}).get("retry_after", 5)
            self.sleep(min(float(wait), MAX_RETRY_WAIT_S))
            status, data = self.transport(url, body, self.timeout + _wait)
        if not data or not data.get("ok"):
            desc = (data or {}).get("description", f"HTTP {status}")
            raise TelegramError(f"{method} failed: {desc}")
        return data.get("result")

    # --- the calls the bot uses -------------------------------------------------------------

    def get_updates(self, offset: int | None = None, timeout: int = 50) -> list[dict]:
        return (
            self.call(
                "getUpdates",
                _wait=timeout,
                offset=offset,
                timeout=timeout,
                allowed_updates=["message", "callback_query"],
            )
            or []
        )

    def send_message(
        self,
        chat_id,
        text: str,
        buttons: list[list[dict]] | None = None,
        html: bool = False,
        keyboard: list[list[str]] | None = None,
    ) -> dict:
        markup = None
        if buttons:
            markup = {"inline_keyboard": buttons}
        elif keyboard:
            markup = {
                "keyboard": [[{"text": t} for t in row] for row in keyboard],
                "resize_keyboard": True,
                "one_time_keyboard": True,
            }
        return self.call(
            "sendMessage",
            chat_id=chat_id,
            text=text[:4096],
            parse_mode="HTML" if html else None,
            reply_markup=markup,
            link_preview_options={"is_disabled": True},
        )

    def send_media_group(self, chat_id, photo_urls: list[str], reply_to: int | None = None) -> list:
        """An album; a single photo goes by sendPhoto (albums need 2–10 items)."""
        reply = {"message_id": reply_to} if reply_to else None
        if len(photo_urls) == 1:
            photo = self.call(
                "sendPhoto", chat_id=chat_id, photo=photo_urls[0], reply_parameters=reply
            )
            return [photo]
        media = [{"type": "photo", "media": u} for u in photo_urls[:10]]
        return self.call("sendMediaGroup", chat_id=chat_id, media=media, reply_parameters=reply)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.call("answerCallbackQuery", callback_query_id=callback_id, text=text or None)

    def edit_buttons(self, chat_id, message_id: int, buttons: list[list[dict]] | None) -> None:
        self.call(
            "editMessageReplyMarkup",
            chat_id=chat_id,
            message_id=message_id,
            reply_markup={"inline_keyboard": buttons or []},
        )

    def set_commands(self, commands: list[tuple[str, str]]) -> None:
        self.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])

    def set_menu_button(self, chat_id, url: str, text: str = "Open app") -> None:
        menu = {"type": "web_app", "text": text, "web_app": {"url": url}}
        self.call("setChatMenuButton", chat_id=chat_id, menu_button=menu)


def button(text: str, data: str | None = None, url: str | None = None) -> dict:
    b = {"text": text}
    if url:
        b["url"] = url
    else:
        b["callback_data"] = data or text
    return b


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)
