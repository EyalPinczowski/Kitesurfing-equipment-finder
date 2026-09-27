"""The Telegram Mini App: a web page inside Telegram, served from the phone.

A small stdlib HTTP server serves the page (kitefinder/bot/web/) and a JSON API over the same
database. Every API call must carry Telegram's signed `initData`; the signature is checked
with the bot token (Telegram's documented HMAC scheme) and only your user id is accepted.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from .. import cli
from ..db import Database
from ..models import MARK_STATUSES, ValidationError
from .formatter import _condition_line, _price_line, source_label, title

WEB_DIR = Path(__file__).parent / "web"
MAX_AGE_S = 24 * 3600
MAX_BODY = 64 * 1024
STATIC = {
    "/": ("index.html", "text/html"),
    "/app.js": ("app.js", "text/javascript"),
    "/style.css": ("style.css", "text/css"),
}


class AuthError(Exception):
    pass


def verify_init_data(
    init_data: str, bot_token: str, owner_id: str | None, now: float | None = None
) -> dict:
    """Check Telegram's signature on Mini App initData; returns the Telegram user."""
    fields = dict(parse_qsl(init_data or "", keep_blank_values=True))
    received = fields.pop("hash", "")
    if not received:
        raise AuthError("missing signature")
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.encode(), received.encode()):  # bytes: any input is safe
        raise AuthError("bad signature")
    age = (now or time.time()) - int(fields.get("auth_date", "0") or 0)
    if age > MAX_AGE_S:
        raise AuthError("session expired — reopen the app")
    try:
        user = json.loads(fields.get("user", "{}") or "{}")
    except ValueError:
        raise AuthError("bad user field") from None
    if not isinstance(user, dict) or owner_id is None or str(user.get("id")) != str(owner_id):
        raise AuthError("this app is private")
    return user


def sign_init_data(fields: dict, bot_token: str) -> str:
    """What Telegram does — used by the tests (and handy for local debugging)."""
    from urllib.parse import urlencode

    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


# --- the data the page shows ------------------------------------------------------------------


def listing_card(db: Database, listing, match: dict | None) -> dict:
    why = (match or {}).get("why", "")
    return {
        "id": listing.id,
        "title": title(listing),
        "price": _price_line(listing, why),
        "location": listing.location or "location unknown",
        "description": listing.description or "(no description in the post)",
        "condition": _condition_line(db.get_assessment(listing.id)),
        "source": source_label(listing),
        "url": listing.url,
        "photos": [u for u in db.listing_images(listing.id) if u.startswith("http")][:4],
        "why": why,
        "mark": db.get_mark("listing", listing.id),
    }


def state(db: Database) -> dict:
    from ..pipeline import alert_targets

    profile = db.get_profile()
    active = db.latest_recommendation()
    recs = [r for r in db.list_recommendations(6) if r.kind == "set"][:3]
    items, queries = alert_targets(db)
    seen, cards = set(), []
    rows = db.conn.execute(
        "SELECT m.* FROM matches m JOIN listings l ON l.id = m.listing_id "
        "WHERE l.status = 'matched' ORDER BY m.score DESC LIMIT 60"
    ).fetchall()
    for row in rows:
        if row["listing_id"] in seen:
            continue
        seen.add(row["listing_id"])
        listing = db.get_listing(row["listing_id"])
        if listing is not None and db.get_mark("listing", listing.id) != "dismissed":
            cards.append(listing_card(db, listing, dict(row)))
    return {
        "profile": profile.to_dict() if profile else None,
        "profile_text": cli.format_profile(profile) if profile else "",
        "owned": [cli.format_owned(i) for i in db.list_owned()],
        "active_id": active.id if active else None,
        "recommendations": [
            {"id": r.id, "variant": r.variant, "text": cli.format_recommendation(r)} for r in recs
        ],
        "listings": cards[:30],
        "watches": queries,
        "pending_alerts": len(db.pending_matches(items, queries)),
        "spot_catalog": spot_catalog(),
    }


def spot_catalog() -> list[dict]:
    """Every known spot, by region — the Mini App's tick-box list."""
    from ..sizing import spots

    all_spots, _ = spots.load_spots()
    return [
        {"region": key, "label": label, "spots": [s.name for s in all_spots if s.region == key]}
        for key, label in spots.REGION_LABELS.items()
    ]


PROFILE_ARGS = {
    "weight_kg": "--weight",
    "waist_cm": "--waist",
    "wind": "--wind",
    "areas": "--areas",
    "season": "--season",
    "skill": "--skill",
    "style": "--style",
    "discipline": "--focus",
    "budget_ils": "--budget",
    "condition_pref": "--condition",
    "min_year": "--min-year",
    "travel_km": "--travel-km",
}


def api(db: Database, method: str, path: str, query: dict, body: dict) -> tuple[int, dict]:
    """The Mini App's JSON API (auth is checked before this is called)."""

    def run(argv):
        try:
            return 200, {"text": cli.run(argv, db=db), "state": state(db)}
        except ValidationError as e:
            return 400, {"error": str(e)}
        except SystemExit:
            return 400, {"error": "invalid request"}

    if method == "GET" and path == "/api/state":
        return 200, state(db)
    if method == "GET" and path == "/api/assemble":
        brands = query.get("brands", "mixed")
        return run(["assemble", "--brands", brands])
    if method == "POST" and path == "/api/mark":
        lid, status = body.get("listing_id"), body.get("status")
        if status != "clear" and status not in MARK_STATUSES:
            return 400, {"error": "status must be favorite, dismissed or clear"}
        try:
            if status == "clear":
                db.clear_mark("listing", int(lid))
            else:
                db.set_mark("listing", int(lid), status)
        except (ValidationError, TypeError, ValueError):
            return 400, {"error": "listing_id must be a listing's number"}
        return 200, state(db)
    if method == "POST" and path == "/api/profile":
        argv = ["profile", "set"]
        for key, flag in PROFILE_ARGS.items():
            if body.get(key) not in (None, ""):
                argv += [flag, str(body[key])]
        return run(argv)
    if method == "POST" and path == "/api/recommend":
        if body.get("under") not in (None, ""):
            return run(["recommend", "--under", str(body["under"])])
        option = body.get("option")
        return run(["recommend"] + (["--option", option] if option else []))
    return 404, {"error": "not found"}


# --- the server ---------------------------------------------------------------------------------


def make_server(
    db_path: Path,
    bot_token: str,
    owner_id: str | None | Callable[[], str | None],
    host: str = "127.0.0.1",
    port: int = 8787,
) -> ThreadingHTTPServer:
    """`owner_id` may be a function, read on every request (the owner can claim the bot later)."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet: Termux logs stay readable
            pass

        def _send(self, status: int, payload, content_type="application/json") -> None:
            data = (
                payload
                if isinstance(payload, bytes)
                else json.dumps(payload, ensure_ascii=False).encode()
            )
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method: str) -> None:
            parts = urlsplit(self.path)
            if method == "GET" and parts.path in STATIC:
                name, ctype = STATIC[parts.path]
                return self._send(200, (WEB_DIR / name).read_bytes(), ctype)
            if not parts.path.startswith("/api/"):
                return self._send(404, {"error": "not found"})
            try:
                owner = owner_id() if callable(owner_id) else owner_id
                verify_init_data(self.headers.get("X-Telegram-Init-Data", ""), bot_token, owner)
            except AuthError as e:
                return self._send(HTTPStatus.UNAUTHORIZED, {"error": str(e)})
            body = {}
            if method == "POST":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 <= length <= MAX_BODY:
                        return self._send(413, {"error": "request too large"})
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._send(400, {"error": "body must be JSON"})
                if not isinstance(body, dict):
                    return self._send(400, {"error": "body must be a JSON object"})
            with Database(db_path) as db:
                status, payload = api(db, method, parts.path, dict(parse_qsl(parts.query)), body)
            return self._send(status, payload)

        def do_GET(self):  # noqa: N802
            self._handle("GET")

        def do_POST(self):  # noqa: N802
            self._handle("POST")

    return ThreadingHTTPServer((host, port), Handler)


def serve_in_background(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread
