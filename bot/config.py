from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {key}")
    return value


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

    # Tavily
    tavily_api_key: str

    # Database
    db_path: str

    # Summary window in hours
    summary_hours: int = 24

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            bot_token=_require("BOT_TOKEN"),
            owner_id=int(_require("OWNER_ID")),
            telegram_api_id=int(_require("TELEGRAM_API_ID")),
            telegram_api_hash=_require("TELEGRAM_API_HASH"),
            anthropic_api_key=_require("ANTHROPIC_API_KEY"),
            tavily_api_key=_require("TAVILY_API_KEY"),
            db_path=os.getenv("DB_PATH", "data.db"),
        )
