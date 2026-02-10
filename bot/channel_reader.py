from __future__ import annotations

import logging
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.tl.types import (
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
)

from bot.database import Database

logger = logging.getLogger(__name__)


class ChannelReader:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        db: Database,
        session_name: str = "user_session",
    ) -> None:
        self._client = TelegramClient(session_name, api_id, api_hash)
        self._db = db

    async def start(self) -> None:
        await self._client.start()
        logger.info("Telethon client started")

    async def stop(self) -> None:
        await self._client.disconnect()

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
    ) -> list[dict]:
        """Fetch messages from a single channel since the given datetime."""
        messages: list[dict] = []
        try:
            entity = await self._client.get_entity(channel_username)
            async for msg in self._client.iter_messages(
                entity, offset_date=None, reverse=False,
            ):
                if msg.date.replace(tzinfo=timezone.utc) < since.replace(
                    tzinfo=timezone.utc
                ):
                    break

                media_type, media_caption = self._classify_media(msg.media)
                text = msg.text or ""
                if msg.message and msg.message != text:
                    text = msg.message

                messages.append(
                    {
                        "channel_username": channel_username,
                        "telegram_msg_id": msg.id,
                        "date": msg.date.isoformat(),
                        "text": text if text else None,
                        "media_type": media_type,
                        "media_caption": media_caption
                        or (msg.text if media_type and not text else None),
                    }
                )
        except Exception:
            logger.exception("Failed to fetch messages from %s", channel_username)

        return messages

    async def fetch_all_channels(self, since: datetime) -> list[dict]:
        """Fetch recent messages from all subscribed channels and persist them."""
        channels = await self._db.list_channels()
        if not channels:
            return []

        all_messages: list[dict] = []
        for username in channels:
            logger.info("Fetching messages from @%s", username)
            msgs = await self.fetch_channel_messages(username, since)
            all_messages.extend(msgs)

        if all_messages:
            inserted = await self._db.upsert_messages(all_messages)
            logger.info(
                "Fetched %d messages total, %d new", len(all_messages), inserted
            )

        return all_messages
