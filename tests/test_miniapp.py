"""The Mini App: Telegram's signature check, the JSON API over a real local server, the tunnel."""

import io
import json
import time
import urllib.error
import urllib.request

import pytest
from world import World

from kitefinder import pipeline
from kitefinder.bot import miniapp, tunnel
from kitefinder.bot.miniapp import AuthError, sign_init_data, verify_init_data
from kitefinder.db import Database
from kitefinder.models import Profile
from kitefinder.sizing import quiver

TOKEN = "123:TOKEN"
OWNER = "42"


def init_data(user_id=42, age=0, token=TOKEN, **extra):
    fields = {
        "auth_date": str(int(time.time()) - age),
        "user": json.dumps({"id": user_id}),
        **extra,
    }
    return sign_init_data(fields, token)


# --- the signature check ------------------------------------------------------------------------


def test_valid_init_data_returns_the_user():
    assert verify_init_data(init_data(query_id="AAH"), TOKEN, OWNER)["id"] == 42


@pytest.mark.parametrize(
    "data, owner, error",
    [
        ("", OWNER, "missing signature"),
        (init_data().replace("auth_date=", "auth_date=1"), OWNER, "bad signature"),  # tampered
        (init_data(token="999:OTHER"), OWNER, "bad signature"),  # signed by another bot
        (init_data(age=25 * 3600), OWNER, "session expired"),
        (init_data(user_id=7), OWNER, "this app is private"),
        (init_data(), None, "this app is private"),  # no owner yet: nobody gets in
    ],
)
def test_bad_init_data_is_refused(data, owner, error):
    with pytest.raises(AuthError, match=error):
        verify_init_data(data, TOKEN, owner)


def test_signature_matches_telegrams_documented_example():
    """HMAC_SHA256(check_string, HMAC_SHA256(bot_token, "WebAppData")) — checked by hand."""
    import hashlib
    import hmac

    fields = {"auth_date": "1700000000", "user": '{"id":42}'}
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, b'auth_date=1700000000\nuser={"id":42}', hashlib.sha256).hexdigest()
    assert f"hash={digest}" in sign_init_data(fields, TOKEN)
    assert verify_init_data(sign_init_data(fields, TOKEN), TOKEN, OWNER, now=1700000000)["id"] == 42


# --- the API over a real server -----------------------------------------------------------------


@pytest.fixture
def server(tmp_path):
    path = tmp_path / "kf.db"
    with Database(path) as db:
        world = World()
        profile = Profile(80, 86, 12, 25)
        db.save_profile(profile)
        db.save_recommendation(quiver.recommend_set(profile, [], "minimum"))
        pipeline.run(db, world.settings(), world.fetchers(), None)
    srv = miniapp.make_server(path, TOKEN, OWNER, port=0)
    miniapp.serve_in_background(srv)
    yield f"http://127.0.0.1:{srv.server_address[1]}", path
    srv.shutdown()
    srv.server_close()


def call(base, path, body=None, auth=True):
    headers = {"X-Telegram-Init-Data": init_data()} if auth else {}
    data = (
        None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
    )
    req = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.headers["Content-Type"], r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def test_static_files_need_no_login(server):
    base, _ = server
    for path, ctype, marker in [
        ("/", "text/html", b"telegram-web-app.js"),
        ("/app.js", "text/javascript", b"X-Telegram-Init-Data"),
        ("/style.css", "text/css", b"--tg-theme-bg-color"),
    ]:
        status, got_type, body = call(base, path, auth=False)
        assert (status, got_type.split(";")[0]) == (200, ctype) and marker in body


def test_api_needs_telegrams_signature(server):
    base, _ = server
    status, _, body = call(base, "/api/state", auth=False)
    assert status == 401 and json.loads(body) == {"error": "missing signature"}
    assert call(base, "/nowhere", auth=False)[0] == 404


def test_state_lists_the_matched_listings(server):
    base, path = server
    status, _, body = call(base, "/api/state")
    state = json.loads(body)
    assert status == 200 and state["profile"]["weight_kg"] == 80
    assert (
        len(state["recommendations"]) == 1
        and state["active_id"] == state["recommendations"][0]["id"]
    )
    with Database(path) as db:
        matched = {r["listing_id"] for r in db.conn.execute("SELECT listing_id FROM matches")}
    assert {c["id"] for c in state["listings"]} == matched
    for card in state["listings"]:
        assert card["price"] and card["location"] and card["description"] and card["condition"]
    assert state["pending_alerts"] > 0


def test_marks_hide_dismissed_listings(server):
    base, path = server
    first, second = json.loads(call(base, "/api/state")[2])["listings"][:2]
    state = json.loads(
        call(base, "/api/mark", {"listing_id": first["id"], "status": "favorite"})[2]
    )
    assert next(c for c in state["listings"] if c["id"] == first["id"])["mark"] == "favorite"
    state = json.loads(
        call(base, "/api/mark", {"listing_id": second["id"], "status": "dismissed"})[2]
    )
    assert second["id"] not in [c["id"] for c in state["listings"]]
    state = json.loads(call(base, "/api/mark", {"listing_id": first["id"], "status": "clear"})[2])
    assert next(c for c in state["listings"] if c["id"] == first["id"])["mark"] is None
    status, _, body = call(base, "/api/mark", {"listing_id": 1, "status": "maybe"})
    assert status == 400 and "favorite, dismissed or clear" in json.loads(body)["error"]
    assert call(base, "/api/mark", {"listing_id": "x", "status": "favorite"})[0] == 400


def test_profile_recommend_and_assemble(server):
    base, path = server
    status, _, body = call(base, "/api/profile", {"weight_kg": 90, "budget_ils": 7000, "areas": ""})
    assert status == 200 and json.loads(body)["state"]["profile"]["weight_kg"] == 90
    status, _, body = call(base, "/api/profile", {"weight_kg": 500})
    assert status == 400 and "weight" in json.loads(body)["error"]
    status, _, body = call(base, "/api/recommend", {"option": "one_kite"})
    assert status == 200 and "one kite" in json.loads(body)["text"].lower()
    status, _, body = call(base, "/api/recommend", {"under": "9000"})
    assert status == 200 and json.loads(body)["text"].startswith("Best set within ₪9,000:")
    status, _, body = call(base, "/api/recommend", {"option": "banana"})
    assert status == 400 and json.loads(body) == {"error": "invalid request"}
    status, _, body = call(base, "/api/assemble?brands=same")
    assert status == 200 and json.loads(body)["text"]
    assert call(base, "/api/profile", b"{not json")[0] == 400
    assert call(base, "/api/unknown")[0] == 404


# --- the tunnel ---------------------------------------------------------------------------------


def test_tunnel_url_is_read_from_cloudflared_logs():
    lines = [
        "2026-09-27T10:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...\n",
        "2026-09-27T10:00:02Z INF |  https://calm-river-demo-42.trycloudflare.com  |\n",
    ]

    class Proc:
        stdout = io.StringIO("".join(lines))
        terminated = False

        def terminate(self):
            self.terminated = True

    seen = {}

    def popen(argv, **kw):
        seen["argv"] = argv
        return Proc()

    proc, url = tunnel.start_tunnel(8787, popen=popen)
    assert url == "https://calm-river-demo-42.trycloudflare.com"
    assert seen["argv"][-2:] == ["--url", "http://localhost:8787"]
    assert (
        tunnel.parse_tunnel_url("https://api.trycloudflare.com/x")
        == "https://api.trycloudflare.com"
    )
    assert tunnel.parse_tunnel_url("no address here") is None


def test_tunnel_without_an_address_fails_clearly():
    class Proc:
        stdout = io.StringIO("ERR failed to request quick Tunnel\n")

        def terminate(self):
            self.terminated = True

    with pytest.raises(RuntimeError, match="did not report a tunnel address"):
        tunnel.start_tunnel(8787, popen=lambda argv, **kw: Proc())


def test_missing_cloudflared_is_explained(monkeypatch):
    monkeypatch.setattr(tunnel.shutil, "which", lambda name: None)
    with pytest.raises(RuntimeError, match="pkg install cloudflared"):
        tunnel.start_tunnel(8787)


def test_malformed_requests_get_a_clear_error_not_a_crash(server):
    base, _ = server
    bad_user = sign_init_data({"auth_date": str(int(time.time())), "user": "{oops"}, TOKEN)
    with pytest.raises(AuthError, match="bad user field"):
        verify_init_data(bad_user, TOKEN, OWNER)
    with pytest.raises(AuthError, match="private"):
        verify_init_data(
            sign_init_data({"auth_date": str(int(time.time())), "user": "[1]"}, TOKEN), TOKEN, OWNER
        )
    status, _, body = call(base, "/api/mark", {"listing_id": "x", "status": "clear"})
    assert status == 400 and json.loads(body) == {"error": "listing_id must be a listing's number"}
    status, _, body = call(base, "/api/mark", [1, 2])
    assert status == 400 and json.loads(body) == {"error": "body must be a JSON object"}
    status, _, body = call(base, "/api/profile", b"{" + b" " * (miniapp.MAX_BODY + 1) + b"}")
    assert status == 413
    assert call(base, "/api/state")[0] == 200  # the server is still up


def test_non_ascii_signature_is_refused_not_a_crash(server):
    with pytest.raises(AuthError, match="bad signature"):
        verify_init_data("auth_date=1&hash=%C3%A9", TOKEN, OWNER)
    base, _ = server
    req = urllib.request.Request(
        base + "/api/state", headers={"X-Telegram-Init-Data": "hash=%C3%A9"}
    )
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=10)
    assert e.value.code == 401


def test_tunnel_keeps_reading_logs_after_the_address():
    import threading

    more = threading.Event()

    class Stream:
        def __init__(self):
            self.read = 0

        def __iter__(self):
            yield "INF |  https://a-b.trycloudflare.com  |\n"
            for _ in range(5000):  # far more than a pipe holds
                self.read += 1
                yield "INF connection registered\n"
            more.set()

    class Proc:
        stdout = Stream()

        def terminate(self):
            pass

    proc, url = tunnel.start_tunnel(1, popen=lambda argv, **kw: Proc())
    assert url == "https://a-b.trycloudflare.com"
    assert more.wait(5) and proc.stdout.read == 5000


def test_a_silent_cloudflared_times_out():
    import threading

    hang = threading.Event()

    class Stream:
        def __iter__(self):
            hang.wait(5)  # prints nothing (e.g. no network)
            return iter(())

    class Proc:
        stdout = Stream()
        terminated = False

        def terminate(self):
            Proc.terminated = True
            hang.set()

    start = time.monotonic()
    with pytest.raises(RuntimeError, match="did not report"):
        tunnel.start_tunnel(1, timeout=0.2, popen=lambda argv, **kw: Proc())
    assert time.monotonic() - start < 2 and Proc.terminated
