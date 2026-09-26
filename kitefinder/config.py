"""Settings loaded from .env, the environment, and config/sources.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE lines, # comments, optional quotes."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


@dataclass
class Settings:
    root: Path
    data_dir: Path
    gemini_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    fb_cookies_path: Path | None = None
    sources: dict[str, Any] = field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kitefinder.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


def load_settings(root: Path | None = None, env: dict[str, str] | None = None) -> Settings:
    """Environment variables override .env; relative paths resolve against the project root."""
    root = Path(root or os.environ.get("KITEFINDER_ROOT") or PROJECT_ROOT)
    merged = parse_env_file(root / ".env")
    merged.update(os.environ if env is None else env)

    def resolve(p: str) -> Path:
        path = Path(p).expanduser()
        return path if path.is_absolute() else root / path

    sources_file = root / "config" / "sources.yaml"
    sources = {}
    if sources_file.is_file():
        sources = yaml.safe_load(sources_file.read_text(encoding="utf-8")) or {}

    cookies = merged.get("FB_COOKIES_PATH", "")
    return Settings(
        root=root,
        data_dir=resolve(merged.get("KITEFINDER_DATA_DIR") or "data"),
        gemini_api_key=merged.get("GEMINI_API_KEY", ""),
        telegram_bot_token=merged.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=merged.get("TELEGRAM_CHAT_ID", ""),
        fb_cookies_path=resolve(cookies) if cookies else None,
        sources=sources,
    )
