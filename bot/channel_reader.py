from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.types import (
    Channel,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
)

from bot.database import Database

logger = logging.getLogger(__name__)

# Max channels fetched concurrently (keeps Telegram flood limits happy)
_FETCH_CONCURRENCY = 4


class ChannelReader:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        db: Database,
        session_path: Path,
    ) -> None:
        self._client = TelegramClient(str(session_path), api_id, api_hash)
        self._db = db

    async def start(self) -> None:
        await self._client.connect()
        if not await self._client.is_user_authorized():
            if not sys.stdin.isatty():
                raise RuntimeError(
                    "Telethon session is not authorised. Run the bot once "
                    "interactively (e.g. `docker compose run --rm -it bot`) to log in."
                )
            print(
                "\nTelethon needs to log in to your *personal* Telegram account "
                "(bots cannot read channels). Do NOT enter the bot token.\n"
            )
            await self._client.start(  # prompts for code / 2FA after the phone
                phone=lambda: input("Phone number (international format, e.g. +61400000000): ")
            )

        me = await self._client.get_me()
        if me.bot:
            session_file = self._client.session.filename
            await self._client.disconnect()
            raise RuntimeError(
                "The Telethon session is logged in as a bot, which cannot read channel "
                f"history. Delete {session_file} and restart, then log in with your "
                "phone number."
            )
        logger.info("Telethon client started as user id=%s", me.id)

    async def stop(self) -> None:
        await self._client.disconnect()

    async def resolve_channel(self, username: str) -> str | None:
        """Return the canonical username if it is a readable public channel, else None."""
        try:
            entity = await self._client.get_entity(username)
        except (ValueError, TypeError):
            return None
        if not isinstance(entity, Channel) or not entity.username:
            return None
        return entity.username

    def _classify_media(self, media) -> tuple[str | None, str | None]:
        """Return (media_type, caption) for a message's media."""
        if media is None:
            return None, None
        if isinstance(media, MessageMediaPhoto):
            return "photo", None
        if isinstance(media, MessageMediaDocument):
            doc = media.document
            if doc is None:
                return "document", None
            for attr in doc.attributes:
                cls_name = type(attr).__name__
                if cls_name == "DocumentAttributeVideo":
                    return "video", None
                if cls_name == "DocumentAttributeAudio":
                    return "audio", None
                if cls_name == "DocumentAttributeSticker":
                    return "sticker", None
                if cls_name == "DocumentAttributeAnimated":
                    return "gif", None
            return "document", None
        if isinstance(media, MessageMediaWebPage):
            page = media.webpage
            title = getattr(page, "title", None)
            return "webpage", title
        return "other", None

    async def fetch_channel_messages(
        self, channel_username: str, since: datetime,
    ) -> list[dict] | None:
        """Fetch messages from one channel since the given (tz-aware) datetime.

        Returns None if the channel could not be read.
        """
        messages: list[dict] = []
        try:
            entity = await self._client.get_entity(channel_username)
            async for msg in self._client.iter_messages(entity):
                # Telethon dates are tz-aware UTC; newest first
                if msg.date < since:
                    break

                media_type, media_caption = self._classify_media(msg.media)
                text = msg.message or None  # raw text / caption, no markdown

                if not text and not media_type:
                    continue  # service messages etc.

                messages.append(
                    {
                        "channel_username": channel_username,
                        "telegram_msg_id": msg.id,
                        "date": msg.date.isoformat(),
                        "text": text,
                        "media_type": media_type,
                        "media_caption": media_caption,
                    }
                )
        except Exception:
            logger.exception("Failed to fetch messages from %s", channel_username)
            return None

        return messages

    async def fetch_all_channels(self, since: datetime) -> tuple[list[dict], list[str]]:
        """Fetch recent messages from all subscribed channels and persist them.

        Returns (messages, failed_channel_usernames).
        """
        channels = await self._db.list_channels()
        if not channels:
            return [], []

        sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def fetch(username: str) -> list[dict] | None:
            async with sem:
                logger.info("Fetching messages from @%s", username)
                return await self.fetch_channel_messages(username, since)

        results = await asyncio.gather(*(fetch(u) for u in channels))
        failed = [u for u, msgs in zip(channels, results) if msgs is None]
        if failed and len(failed) == len(channels):
            raise RuntimeError(f"Could not read any of the {len(channels)} channels")

        all_messages = [m for msgs in results if msgs for m in msgs]
        all_messages.sort(key=lambda m: m["date"])

        if all_messages:
            inserted = await self._db.upsert_messages(all_messages)
            logger.info(
                "Fetched %d messages total, %d new", len(all_messages), inserted
            )

        return all_messages, failed
