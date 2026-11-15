from __future__ import annotations

import logging
from datetime import datetime

import aiosqlite

logger = logging.getLogger(__name__)

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS channels (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT    NOT NULL UNIQUE,
    added_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_username TEXT    NOT NULL,
    telegram_msg_id  INTEGER NOT NULL,
    date            TEXT    NOT NULL,
    text            TEXT,
    media_type      TEXT,
    media_caption   TEXT,
    UNIQUE(channel_username, telegram_msg_id)
);

-- Scheduled daily digests (topics as JSON), source for the weekly roundup
CREATE TABLE IF NOT EXISTS digests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT    NOT NULL,
    topics      TEXT    NOT NULL
);
"""


class Database:
    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_CREATE_TABLES)
        await self._db.commit()
        logger.info("Database initialised at %s", self._path)

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    # -- channels --

    async def add_channel(self, username: str) -> bool:
        """Add a channel. Returns True if newly added, False if already exists."""
        username = username.lstrip("@").lower()
        try:
            await self._db.execute(
                "INSERT INTO channels (username) VALUES (?)", (username,)
            )
            await self._db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

    async def remove_channel(self, username: str) -> bool:
        """Remove a channel. Returns True if removed, False if not found."""
        username = username.lstrip("@").lower()
        cursor = await self._db.execute(
            "DELETE FROM channels WHERE username = ?", (username,)
        )
        await self._db.commit()
        return cursor.rowcount > 0

    async def list_channels(self) -> list[str]:
        cursor = await self._db.execute(
            "SELECT username FROM channels ORDER BY added_at"
        )
        rows = await cursor.fetchall()
        return [row["username"] for row in rows]

    # -- messages --

    async def upsert_messages(
        self, messages: list[dict],
    ) -> int:
        """Bulk-upsert messages. Returns number of new rows inserted."""
        inserted = 0
        for msg in messages:
            try:
                await self._db.execute(
                    """INSERT INTO messages
                       (channel_username, telegram_msg_id, date, text, media_type, media_caption)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        msg["channel_username"],
                        msg["telegram_msg_id"],
                        msg["date"],
                        msg.get("text"),
                        msg.get("media_type"),
                        msg.get("media_caption"),
                    ),
                )
                inserted += 1
            except aiosqlite.IntegrityError:
                pass
        await self._db.commit()
        return inserted

    async def get_messages_since(
        self, since: datetime,
    ) -> list[dict]:
        """Return all messages with date >= since, ordered by date."""
        cursor = await self._db.execute(
            """SELECT channel_username, telegram_msg_id, date, text,
                      media_type, media_caption
               FROM messages
               WHERE date >= ?
               ORDER BY date""",
            (since.isoformat(),),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete_messages_before(self, before: datetime) -> int:
        cursor = await self._db.execute(
            "DELETE FROM messages WHERE date < ?", (before.isoformat(),)
        )
        await self._db.commit()
        return cursor.rowcount

    # -- digests --

    async def save_digest(self, created_at: datetime, topics_json: str) -> None:
        await self._db.execute(
            "INSERT INTO digests (created_at, topics) VALUES (?, ?)",
            (created_at.isoformat(), topics_json),
        )
        await self._db.commit()

    async def get_digests_since(self, since: datetime) -> list[tuple[datetime, str]]:
        """Return [(created_at, topics_json)] with created_at >= since, oldest first."""
        cursor = await self._db.execute(
            "SELECT created_at, topics FROM digests WHERE created_at >= ? ORDER BY created_at",
            (since.isoformat(),),
        )
        rows = await cursor.fetchall()
        return [(datetime.fromisoformat(row["created_at"]), row["topics"]) for row in rows]

    async def delete_digests_before(self, before: datetime) -> int:
        cursor = await self._db.execute(
            "DELETE FROM digests WHERE created_at < ?", (before.isoformat(),)
        )
        await self._db.commit()
        return cursor.rowcount
