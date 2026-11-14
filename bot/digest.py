from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot
from aiogram.types import LinkPreviewOptions

from bot.channel_reader import ChannelReader
from bot.config import Config
from bot.database import Database
from bot.fact_checker import FactChecker
from bot.formatter import format_report
from bot.summarizer import Summarizer

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]


class DigestBusyError(RuntimeError):
    pass


class DigestService:
    """Runs the fetch -> summarise -> fact-check -> format pipeline, one run at a time."""

    def __init__(
        self,
        config: Config,
        db: Database,
        reader: ChannelReader,
        summarizer: Summarizer,
        fact_checker: FactChecker,
    ) -> None:
        self._config = config
        self._db = db
        self._reader = reader
        self._summarizer = summarizer
        self._fact_checker = fact_checker
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def has_channels(self) -> bool:
        return bool(await self._db.list_channels())

    async def build(self, progress: ProgressCallback | None = None) -> list[str]:
        """Build the digest and return the formatted Telegram messages.

        Raises DigestBusyError if another run is in progress.
        """
        if self._lock.locked():
            raise DigestBusyError("A digest is already being generated")

        async def report(text: str) -> None:
            if progress:
                try:
                    await progress(text)
                except Exception:
                    logger.debug("Progress update failed", exc_info=True)

        async with self._lock:
            hours = self._config.summary_hours
            now = datetime.now(timezone.utc)
            since = now - timedelta(hours=hours)

            await report("Fetching messages from channels...")
            messages, failed = await self._reader.fetch_all_channels(since)
            await self._db.delete_messages_before(
                now - timedelta(days=self._config.retention_days)
            )
            failed_note = (
                "⚠️ Could not read: " + ", ".join(f"@{escape(c)}" for c in failed)
                if failed else None
            )
            if not messages:
                return [
                    "\n\n".join(
                        filter(None, [f"No messages found in the last {hours} hours.", failed_note])
                    )
                ]

            await report(f"Fetched {len(messages)} messages. Summarising...")
            topics = await self._summarizer.summarize(messages)
            if not topics:
                return ["No meaningful topics found to summarise."]

            n_claims = sum(len(t.claims) for t in topics)
            await report(f"Found {len(topics)} topics. Fact-checking {n_claims} claims...")
            try:
                topics = await self._fact_checker.check_claims(topics)
            except Exception:
                # Partial digest beats no digest
                logger.exception("Fact-checking failed, proceeding without")

            texts = format_report(
                topics, hours, now=now.astimezone(self._config.timezone),
            )
            if failed_note:
                texts.append(failed_note)
            return texts


async def send_report(bot: Bot, chat_id: int, texts: list[str]) -> None:
    for text in texts:
        await bot.send_message(
            chat_id, text, link_preview_options=LinkPreviewOptions(is_disabled=True),
        )


def _seconds_until(config: Config) -> float:
    now = datetime.now(config.timezone)
    target = datetime.combine(now.date(), config.digest_time, tzinfo=config.timezone)
    if target <= now:
        target = datetime.combine(
            now.date() + timedelta(days=1), config.digest_time, tzinfo=config.timezone,
        )
    return (target - now).total_seconds()


async def run_daily_scheduler(bot: Bot, service: DigestService, config: Config) -> None:
    """Send the digest to the owner every day at DIGEST_TIME (DIGEST_TIMEZONE)."""
    if config.digest_time is None:
        logger.info("Daily digest disabled")
        return

    while True:
        delay = _seconds_until(config)
        logger.info(
            "Next daily digest at %s %s (in %.1f h)",
            config.digest_time.strftime("%H:%M"), config.timezone.key, delay / 3600,
        )
        await asyncio.sleep(delay)

        if not await service.has_channels():
            logger.info("No channels subscribed, skipping daily digest")
            continue
        try:
            texts = await service.build()
            await send_report(bot, config.owner_id, texts)
        except DigestBusyError:
            logger.info("Digest already running, skipping scheduled run")
        except Exception:
            logger.exception("Scheduled digest failed")
            try:
                await bot.send_message(
                    config.owner_id, "⚠️ Daily digest failed. Check the bot logs."
                )
            except Exception:
                logger.exception("Could not notify owner")
        # Avoid a double run if the job finished within the same minute
        await asyncio.sleep(60)
