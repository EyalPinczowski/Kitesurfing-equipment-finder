import pytest
from fakes import FakeTransport, gemini_reply

from kitefinder.llm import gemini as gm


def ok(obj=None):
    return lambda body: (200, gemini_reply(obj or {"x": 1}), {})


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def client(transport, db=None, **kw):
    clock = Clock()
    c = gm.GeminiClient("KEY", model="gemini-test", db=db, transport=transport,
                        sleep=clock.sleep, clock=clock, **kw)  # fmt: skip
    return c, clock


SCHEMA = {"type": "OBJECT", "properties": {"x": {"type": "NUMBER"}}}


def test_request_shape_and_key_in_header_not_url():
    t = FakeTransport(ok())
    c, _ = client(t)
    img = gm.Image(b"\xff\xd8\xffdata", "image/jpeg")
    assert c.generate_json("hello", SCHEMA, images=[img]) == {"x": 1}
    (req,) = t.requests
    assert req["url"] == f"{gm.API_ROOT}/models/gemini-test:generateContent"
    assert "KEY" not in req["url"] and req["headers"]["x-goog-api-key"] == "KEY"
    parts = req["body"]["contents"][0]["parts"]
    assert parts[0] == {"text": "hello"}
    assert parts[1]["inlineData"]["mimeType"] == "image/jpeg"
    cfg = req["body"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["responseSchema"] == SCHEMA
    assert cfg["temperature"] == 0


def test_cache_avoids_second_call(db):
    t = FakeTransport(ok())
    c, _ = client(t, db=db)
    c.generate_json("same", SCHEMA)
    c.generate_json("same", SCHEMA)
    assert len(t.requests) == 1
    c.generate_json("different", SCHEMA)
    assert len(t.requests) == 2
    # a new client (e.g. after a restart) still hits the cache
    c2, _ = client(t, db=db)
    c2.generate_json("same", SCHEMA)
    assert len(t.requests) == 2


def test_images_are_part_of_cache_key(db):
    t = FakeTransport(ok())
    c, _ = client(t, db=db)
    c.generate_json("p", SCHEMA, images=[gm.Image(b"a")])
    c.generate_json("p", SCHEMA, images=[gm.Image(b"b")])
    assert len(t.requests) == 2


def test_daily_budget_counts_and_stops(db):
    t = FakeTransport(ok())
    c, _ = client(t, db=db, rpd=2)
    c.generate_json("a", SCHEMA)
    c.generate_json("b", SCHEMA)
    assert c.calls_today() == 2
    with pytest.raises(gm.QuotaExceeded, match="daily budget"):
        c.generate_json("c", SCHEMA)
    assert len(t.requests) == 2
    assert client(t, db=db, rpd=2)[0].calls_today() == 2  # persisted in the DB


def test_per_minute_pacing_sleeps():
    t = FakeTransport(ok())
    c, clock = client(t, rpm=2)
    for i in range(3):
        c.generate_json(str(i), SCHEMA)
    assert len(clock.sleeps) == 1 and 59 < clock.sleeps[0] <= 60.2


def test_429_retries_using_server_delay():
    replies = [
        (429, {"error": {"message": "slow down", "details": [{"retryDelay": "7s"}]}}, {}),
        (429, {"error": {"message": "slow down"}}, {"Retry-After": "3"}),
        (200, gemini_reply({"x": 2}), {}),
    ]
    t = FakeTransport(lambda body: replies.pop(0))
    c, clock = client(t)
    assert c.generate_json("p", SCHEMA) == {"x": 2}
    assert clock.sleeps == [7.0, 3.0]
    assert c.calls == 3


def test_429_gives_up_as_quota():
    t = FakeTransport(lambda body: (429, {"error": {"message": "quota"}}, {}))
    c, clock = client(t, max_retries=2)
    with pytest.raises(gm.QuotaExceeded, match="rate limit"):
        c.generate_json("p", SCHEMA)
    assert len(t.requests) == 3 and clock.sleeps == [2.0, 4.0]  # exponential backoff


def test_500_retried_then_ok():
    replies = [(503, None, {}), (200, gemini_reply({"x": 3}), {})]
    t = FakeTransport(lambda body: replies.pop(0))
    c, _ = client(t)
    assert c.generate_json("p", SCHEMA) == {"x": 3}


@pytest.mark.parametrize("status", [400, 403, 404])
def test_client_errors_not_retried(status):
    t = FakeTransport(lambda body: (status, {"error": {"message": "API key not valid"}}, {}))
    c, _ = client(t)
    with pytest.raises(gm.LLMError, match=f"{status}: API key not valid") as e:
        c.generate_json("p", SCHEMA)
    assert not isinstance(e.value, gm.QuotaExceeded)
    assert len(t.requests) == 1


@pytest.mark.parametrize(
    "reply, msg",
    [
        ({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked"),
        ({"candidates": []}, "no answer"),
        ({"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}, "MAX_TOKENS"),
        ({"candidates": [{"content": {"parts": [{"text": "not json"}]}}]}, "not valid JSON"),
        ({"candidates": [{"content": {"parts": [{"text": "[1, 2]"}]}}]}, "not an object"),
    ],
)
def test_bad_answers(reply, msg):
    c, _ = client(FakeTransport(lambda body: (200, reply, {})))
    with pytest.raises(gm.LLMError, match=msg):
        c.generate_json("p", SCHEMA)


def test_fenced_json_tolerated():
    reply = {"candidates": [{"content": {"parts": [{"text": '```json\n{"x": 5}\n```'}]}}]}
    c, _ = client(FakeTransport(lambda body: (200, reply, {})))
    assert c.generate_json("p", SCHEMA) == {"x": 5}


def test_list_models_uses_no_quota(db):
    data = {"models": [
        {"name": "models/gemini-b", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
        {"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent", "countTokens"]},
    ]}  # fmt: skip
    t = FakeTransport(lambda body: (200, data, {}))
    c, _ = client(t, db=db)
    assert c.list_models() == ["gemini-a", "gemini-b"]
    assert t.requests[0]["body"] is None and c.calls_today() == 0


def test_needs_key_and_defaults():
    with pytest.raises(gm.LLMError, match="no Gemini API key"):
        gm.GeminiClient("")
    c = gm.GeminiClient("k")
    assert (c.model, c.rpd, c.rpm) == (gm.DEFAULT_MODEL, gm.DEFAULT_RPD, gm.DEFAULT_RPM)


def test_model_and_limits_from_env_file(tmp_path):
    """Review regression: GEMINI_MODEL/RPM/RPD in .env were ignored."""
    from kitefinder.config import load_settings

    (tmp_path / ".env").write_text(
        "GEMINI_API_KEY=abc\nGEMINI_MODEL=gemini-x\nGEMINI_RPM=4\nGEMINI_RPD=50\n"
    )
    c = gm.client_from_settings(load_settings(tmp_path, env={}))
    assert (c.model, c.rpm, c.rpd) == ("gemini-x", 4, 50)


@pytest.mark.parametrize("bad", ["fast", "0", "-3"])
def test_bad_limit_in_env_is_a_clear_error(tmp_path, bad):
    from kitefinder.config import load_settings
    from kitefinder.models import ValidationError

    with pytest.raises(ValidationError, match="GEMINI_RPM in .env must be a whole number"):
        load_settings(tmp_path, env={"GEMINI_RPM": bad})


def test_network_errors_retried_then_llm_error(monkeypatch):
    """Review regression: offline / timeout must end as LLMError so the rules take over."""
    import requests

    def boom(*a, **k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(requests, "post", boom)
    clock = Clock()
    c = gm.GeminiClient("K", sleep=clock.sleep, clock=clock, max_retries=1)
    with pytest.raises(gm.LLMError, match="network error: no route to host") as e:
        c.generate_json("p", SCHEMA)
    assert not isinstance(e.value, gm.QuotaExceeded) and clock.sleeps == [2.0]


def test_client_from_settings(tmp_path):
    from kitefinder.config import load_settings

    assert gm.client_from_settings(load_settings(tmp_path, env={})) is None
    s = load_settings(tmp_path, env={"GEMINI_API_KEY": "abc"})
    assert gm.client_from_settings(s).api_key == "abc"


def test_retry_delay_parsing():
    assert gm._retry_delay(None, {"retry-after": "2.5"}, 0) == 2.5
    assert gm._retry_delay({"error": {"details": [{"retryDelay": "1.5s"}]}}, {}, 0) == 1.5
    assert gm._retry_delay({"error": {"details": [{"retryDelay": "soon"}]}}, {}, 1) == 4.0


def test_requests_transport(monkeypatch):
    import requests

    class Resp:
        status_code = 200
        headers = {"a": "b"}

        def json(self):
            return {"ok": True}

    class BadResp(Resp):
        def json(self):
            raise ValueError

    monkeypatch.setattr(requests, "post", lambda *a, **k: Resp())
    monkeypatch.setattr(requests, "get", lambda *a, **k: BadResp())
    assert gm.requests_transport("u", {}, {"x": 1}, 1) == (200, {"ok": True}, {"a": "b"})
    assert gm.requests_transport("u", {}, None, 1) == (200, None, {"a": "b"})
