from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


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


def _parse_weekday(value: str) -> int | None:
    """'mon' / 'Monday' -> 0 ... 'sun' -> 6; empty or 'off' -> None."""
    value = value.strip().lower()
    if value in ("", "off"):
        return None
    for i, day in enumerate(WEEKDAYS):
        if len(value) >= 3 and day.startswith(value):
            return i
    raise RuntimeError(f"WEEKLY_DAY must be a weekday like 'mon' or 'off', got {value!r}")


def _parse_chat(value: str) -> int | str | None:
    """'@channel' / 'channel' -> '@channel'; '-100123...' -> int; empty -> None."""
    value = value.strip()
    if not value:
        return None
    if value.lstrip("-").isdigit():
        return int(value)
    return "@" + value.lstrip("@")


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

    # Where scheduled digests go: a channel (bot must be admin) or None = owner's DMs
    digest_chat: int | str | None = None
    # Weekly roundup after the daily digest on this weekday (0 = Monday; None = off)
    weekly_day: int | None = None

    # Profile: what this bot covers and how its pages are branded
    digest_focus: str = ""
    digest_categories: tuple[str, ...] | None = None  # None = summarizer default
    digest_title: str = "Daily Digest"
    weekly_title: str = "Weekly Roundup"
    brand_name: str = "News Aggregator Bot"  # Telegraph author name
    brand_url: str = ""  # Telegraph author link

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
        categories = tuple(
            c.strip() for c in os.getenv("DIGEST_CATEGORIES", "").split(",") if c.strip()
        )
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
            digest_chat=_parse_chat(os.getenv("DIGEST_CHAT", "")),
            weekly_day=_parse_weekday(os.getenv("WEEKLY_DAY", "")),
            digest_focus=os.getenv("DIGEST_FOCUS", "").strip(),
            digest_categories=categories or None,
            digest_title=os.getenv("DIGEST_TITLE", "").strip() or "Daily Digest",
            weekly_title=os.getenv("WEEKLY_TITLE", "").strip() or "Weekly Roundup",
            brand_name=os.getenv("BRAND_NAME", "").strip() or "News Aggregator Bot",
            brand_url=os.getenv("BRAND_URL", "").strip(),
        )
