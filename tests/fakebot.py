"""A fake Telegram Bot API that records every call (for the bot tests)."""

from kitefinder.bot.telegram import TelegramAPI


class FakeTelegram:
    def __init__(self, fail_methods=()):
        self.calls: list[tuple[str, dict]] = []
        self.fail_methods = set(fail_methods)
        self.updates: list[dict] = []
        self._next_id = 100

    def __call__(self, url, body, timeout):
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, body))
        if method in self.fail_methods:
            return 400, {"ok": False, "description": "Bad Request: wrong file identifier"}
        if method == "getUpdates":
            offset = body.get("offset") or 0
            return 200, {
                "ok": True,
                "result": [u for u in self.updates if u["update_id"] >= offset],
            }
        self._next_id += 1
        return 200, {"ok": True, "result": {"message_id": self._next_id}}

    def api(self):
        return TelegramAPI("123:TOKEN", transport=self)

    def sent(self, method="sendMessage"):
        return [b for m, b in self.calls if m == method]

    def transcript(self) -> str:
        """Every message the bot sent, with its buttons — what the golden files store."""
        out = []
        for method, body in self.calls:
            if method == "sendMessage":
                out.append(body["text"])
                markup = body.get("reply_markup") or {}
                for row in markup.get("inline_keyboard", []):
                    out.append("  [" + "] [".join(b["text"] for b in row) + "]")
            elif method == "sendPhoto":
                out.append("<photo>")
            elif method == "sendMediaGroup":
                out.append(f"<album: {len(body['media'])} photos>")
            elif method == "editMessageText":
                out.append("<message edited to:>")
                out.append(body["text"])
                for row in body["reply_markup"]["inline_keyboard"]:
                    out.append("  [" + "] [".join(b["text"] for b in row) + "]")
            elif method == "editMessageReplyMarkup":
                rows = body["reply_markup"]["inline_keyboard"]
                out.append("<buttons now: " + " | ".join(b["text"] for r in rows for b in r) + ">")
            elif method == "answerCallbackQuery":
                if not body.get("text"):
                    continue  # a silent acknowledgement: nothing the user sees
                out.append(f"<toast: {body['text']}>")
            out.append("---")
        return "\n".join(out)

    def clear(self):
        self.calls.clear()


def msg(text, chat=42, update_id=1):
    return {"update_id": update_id, "message": {"chat": {"id": chat}, "text": text}}


def press(data, chat=42, update_id=1, message_id=7):
    return {
        "update_id": update_id,
        "callback_query": {
            "id": f"cb{update_id}",
            "data": data,
            "message": {"chat": {"id": chat}, "message_id": message_id},
        },
    }
