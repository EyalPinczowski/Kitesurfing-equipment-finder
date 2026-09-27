"""Minimal Gemini REST client for the free tier.

Plain `requests` (no SDK) so it installs on Termux without compiling anything. It adds what a
free-tier user needs: a response cache in SQLite, per-minute pacing, a per-day call budget,
retries on rate limits / server errors, and structured JSON output.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
# Used when GEMINI_MODEL is not set and the model list can't be fetched. Free-tier model names
# change over time, so `kitefinder llm models` lists what your key can use.
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_RPM = 8  # stay under the free-tier per-minute limit
DEFAULT_RPD = 200  # calls per UTC day before we stop and fall back to rules


class LLMError(RuntimeError):
    """The model could not give a usable answer (bad key, blocked, malformed output …)."""


class QuotaExceeded(LLMError):
    """The free-tier quota (per minute or per day) is used up; fall back and try later."""


@dataclass
class Image:
    data: bytes
    mime_type: str = "image/jpeg"

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


# transport(url, headers, json_body, timeout) -> (status_code, parsed_json_or_None, headers)
Transport = Callable[[str, dict, dict | None, float], tuple[int, dict | None, dict]]


def requests_transport(url: str, headers: dict, body: dict | None, timeout: float):
    import requests

    try:
        if body is None:
            resp = requests.get(url, headers=headers, timeout=timeout)
        else:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
    except requests.RequestException as e:  # offline, DNS, timeout …: retried, then LLMError
        return 0, {"error": {"message": f"network error: {e}"}}, {}
    try:
        data = resp.json()
    except ValueError:
        data = None
    return resp.status_code, data, dict(resp.headers)


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _retry_delay(data: dict | None, headers: dict, attempt: int) -> float:
    """Server-suggested wait (Retry-After header or RetryInfo detail), else backoff."""
    ra = {k.lower(): v for k, v in (headers or {}).items()}.get("retry-after")
    if ra and str(ra).replace(".", "", 1).isdigit():
        return float(ra)
    for d in ((data or {}).get("error") or {}).get("details") or []:
        m = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(d.get("retryDelay", "")))
        if m:
            return float(m.group(1))
    return float(2 ** (attempt + 1))


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        db=None,
        rpm: int | None = None,
        rpd: int | None = None,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        max_retries: int = 3,
        timeout: float = 60.0,
    ):
        if not api_key:
            raise LLMError("no Gemini API key: set GEMINI_API_KEY in .env")
        self.api_key = api_key
        self.model = model or DEFAULT_MODEL
        self.db = db
        self.rpm = rpm or DEFAULT_RPM
        self.rpd = rpd or DEFAULT_RPD
        self.transport = transport or requests_transport
        self.sleep, self.clock = sleep, clock
        self.max_retries, self.timeout = max_retries, timeout
        self._recent: deque[float] = deque()
        self.calls = 0  # real API calls made by this client (cache hits excluded)

    # --- bookkeeping ------------------------------------------------------------------------

    def _headers(self) -> dict:
        # key in a header, never in the URL, so it can't leak into logs of URLs
        return {"x-goog-api-key": self.api_key, "Content-Type": "application/json"}

    def _day_key(self) -> str:
        return f"gemini_calls:{_utc_day()}"

    def calls_today(self) -> int:
        if self.db is None:
            return self.calls
        row = self.db.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (self._day_key(),)
        ).fetchone()
        return int(row["value"]) if row else 0

    def _count_call(self) -> None:
        self.calls += 1
        if self.db is not None:
            with self.db.conn:
                self.db.conn.execute(
                    "INSERT INTO meta (key, value) VALUES (?, '1') ON CONFLICT(key) DO UPDATE "
                    "SET value = CAST(value AS INTEGER) + 1",
                    (self._day_key(),),
                )

    def _pace(self) -> None:
        """Wait so that no more than `rpm` calls start within any 60 s window."""
        now = self.clock()
        while self._recent and now - self._recent[0] >= 60:
            self._recent.popleft()
        if len(self._recent) >= self.rpm:
            self.sleep(60 - (now - self._recent[0]) + 0.1)
            now = self.clock()
            while self._recent and now - self._recent[0] >= 60:
                self._recent.popleft()
        self._recent.append(now)

    def _cache_get(self, key: str) -> dict | None:
        if self.db is None:
            return None
        row = self.db.conn.execute(
            "SELECT response FROM llm_cache WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row["response"]) if row else None

    def _cache_put(self, key: str, value: dict) -> None:
        if self.db is None:
            return
        with self.db.conn:
            self.db.conn.execute(
                "INSERT OR REPLACE INTO llm_cache (key, response, created_at) VALUES (?, ?, ?)",
                (
                    key,
                    json.dumps(value, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    # --- API ------------------------------------------------------------------------------

    def _request(self, url: str, body: dict | None) -> dict:
        for attempt in range(self.max_retries + 1):
            if body is not None:
                if self.calls_today() >= self.rpd:
                    raise QuotaExceeded(f"daily budget of {self.rpd} Gemini calls used up")
                self._pace()
                self._count_call()
            status, data, headers = self.transport(url, self._headers(), body, self.timeout)
            if status == 200 and data is not None:
                return data
            message = ((data or {}).get("error") or {}).get("message", "") or f"HTTP {status}"
            retryable = status == 429 or status >= 500 or status == 0  # 0 = network error
            if not retryable or attempt == self.max_retries:
                if status == 429:
                    raise QuotaExceeded(f"Gemini rate limit: {message}")
                raise LLMError(f"Gemini error {status}: {message}")
            self.sleep(_retry_delay(data, headers, attempt))
        raise AssertionError("unreachable")  # pragma: no cover

    def generate_json(
        self,
        prompt: str,
        schema: dict,
        images: Sequence[Image] = (),
        temperature: float = 0.0,
    ) -> dict:
        """Ask for JSON matching `schema`; cached by (model, prompt, schema, images)."""
        key = hashlib.sha256(
            json.dumps(
                [self.model, prompt, schema, [i.digest for i in images], temperature],
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        parts: list[dict] = [{"text": prompt}]
        for img in images:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": img.mime_type,
                        "data": base64.b64encode(img.data).decode("ascii"),
                    }
                }
            )
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseSchema": schema,
            },
        }
        data = self._request(f"{API_ROOT}/models/{self.model}:generateContent", body)
        result = self._parse(data)
        self._cache_put(key, result)
        return result

    @staticmethod
    def _parse(data: dict) -> dict:
        feedback = data.get("promptFeedback") or {}
        if feedback.get("blockReason"):
            raise LLMError(f"prompt blocked: {feedback['blockReason']}")
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMError("Gemini returned no answer")
        cand = candidates[0]
        texts = [p.get("text", "") for p in (cand.get("content") or {}).get("parts") or []]
        text = "".join(texts).strip()
        if not text:
            raise LLMError(f"empty answer (finishReason={cand.get('finishReason')})")
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)  # tolerate fenced JSON
        try:
            result = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMError(f"answer was not valid JSON: {e}") from e
        if not isinstance(result, dict):
            raise LLMError("answer was JSON but not an object")
        return result

    def list_models(self) -> list[str]:
        """Model names this key can call generateContent on (no quota used)."""
        data = self._request(f"{API_ROOT}/models?pageSize=200", None)
        return sorted(
            m["name"].removeprefix("models/")
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        )


def client_from_settings(settings, db=None, **kw) -> GeminiClient | None:
    """A client when a key is configured, else None (callers fall back to rules)."""
    if not settings.gemini_api_key:
        return None
    kw = {"model": settings.gemini_model, "rpm": settings.gemini_rpm,
          "rpd": settings.gemini_rpd, **kw}  # fmt: skip
    return GeminiClient(settings.gemini_api_key, db=db, **kw)
