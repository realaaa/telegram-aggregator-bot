from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


def _parse_time(value: str) -> time:
    try:
        hours, minutes = value.strip().split(":")
        return time(int(hours), int(minutes))
    except ValueError as exc:
        raise RuntimeError(f"DIGEST_TIME must be HH:MM, got {value!r}") from exc


@dataclass(frozen=True)
class Config:
    # Telegram Bot API
    bot_token: str
    owner_id: int  # Telegram user ID of the single owner

    # Telethon (MTProto user client)
    telegram_api_id: int
    telegram_api_hash: str

    # Anthropic
    anthropic_api_key: str
    claude_model: str

    # Persistent state (SQLite DB + Telethon session file)
    data_dir: Path

    # Scheduled daily digest (None = disabled)
    digest_time: time | None
    timezone: ZoneInfo

    # Summary window in hours
    summary_hours: int = 24
    # How long stored messages are kept before pruning
    retention_days: int = 7

    @property
    def db_path(self) -> Path:
        return self.data_dir / "data.db"

    @property
    def session_path(self) -> Path:
        # Telethon appends ".session" itself
        return self.data_dir / "user_session"

    @classmethod
    def from_env(cls) -> Config:
        digest_raw = os.getenv("DIGEST_TIME", "07:00").strip()
        data_dir = Path(os.getenv("DATA_DIR", str(PROJECT_ROOT))).resolve()
        data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            bot_token=_require("BOT_TOKEN"),
            owner_id=int(_require("OWNER_ID")),
            telegram_api_id=int(_require("TELEGRAM_API_ID")),
            telegram_api_hash=_require("TELEGRAM_API_HASH"),
            anthropic_api_key=_require("ANTHROPIC_API_KEY"),
            claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
            data_dir=data_dir,
            digest_time=_parse_time(digest_raw) if digest_raw.lower() != "off" else None,
            timezone=ZoneInfo(os.getenv("DIGEST_TIMEZONE", "Australia/Sydney")),
        )
