from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot
from aiogram.types import LinkPreviewOptions

from bot.channel_reader import ChannelReader
from bot.config import Config
from bot.database import Database
from bot.fact_checker import FactChecker
from bot.formatter import DATE_FORMAT, TELEGRAM_MSG_LIMIT, format_report
from bot.summarizer import Summarizer, Topic, topic_from_dict, topic_to_dict
from bot.telegraph import TelegraphClient, build_digest_nodes

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str], Awaitable[None]]

# Headlines shown in the Telegram post above the "Read more" link
TEASER_HEADLINES = 3
# Daily digests included in a weekly roundup. Slightly under 7 days, so last
# week's same-weekday digest (created minutes earlier in the day) is excluded.
WEEKLY_WINDOW = timedelta(days=6, hours=12)
# How long stored daily digests are kept
DIGEST_RETENTION = timedelta(days=30)


class DigestBusyError(RuntimeError):
    pass


@dataclass
class Digest:
    texts: list[str]  # Telegram HTML messages to send, in order
    topics: list[Topic] = field(default_factory=list)  # empty = nothing to publish
    preview_url: str | None = None  # show a link preview for this URL (Telegraph page)
    failed: list[str] = field(default_factory=list)  # channels that couldn't be read


def _failed_note(failed: list[str]) -> str:
    return "⚠️ Could not read: " + ", ".join(f"@{escape(c)}" for c in failed)


def _teaser(icon: str, title: str, topics: list[Topic], url: str, overview: str | None) -> str:
    """Short Telegram post: title, top headlines, link to the full Telegraph page."""
    lines = [f"<b>{icon} {escape(title)}</b>"]
    if overview:
        lines.append(f"<i>{escape(overview)}</i>")
    lines.append("")
    lines += [f"• {escape(t.title)}" for t in topics[:TEASER_HEADLINES]]

    stats = []
    more = len(topics) - TEASER_HEADLINES
    if more > 0:
        stats.append(f"+{more} more stor{'ies' if more != 1 else 'y'}")
    n_checks = sum(len(t.fact_checks) for t in topics)
    if n_checks:
        stats.append(f"{n_checks} claim{'s' * (n_checks != 1)} fact-checked")
    if stats:
        lines.append("<i>" + " · ".join(stats) + "</i>")

    lines += ["", f'<a href="{escape(url, quote=True)}">Read the full digest →</a>']
    return "\n".join(lines)


class DigestService:
    """Runs the fetch -> summarise -> fact-check -> publish pipeline, one run at a time."""

    def __init__(
        self,
        config: Config,
        db: Database,
        reader: ChannelReader,
        summarizer: Summarizer,
        fact_checker: FactChecker,
        telegraph: TelegraphClient | None = None,
    ) -> None:
        self._config = config
        self._db = db
        self._reader = reader
        self._summarizer = summarizer
        self._fact_checker = fact_checker
        self._telegraph = telegraph
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def has_channels(self) -> bool:
        return bool(await self._db.list_channels())

    @contextlib.asynccontextmanager
    async def _exclusive(self) -> AsyncIterator[None]:
        if self._lock.locked():
            raise DigestBusyError("A digest is already being generated")
        async with self._lock:
            yield

    async def _render(
        self,
        icon: str,
        title: str,
        topics: list[Topic],
        period: str,
        report: ProgressCallback,
        overview: str | None = None,
    ) -> Digest:
        """Publish to Telegraph and return a teaser; fall back to full messages."""
        if self._telegraph:
            await report("Publishing to Telegraph...")
            try:
                url = await self._telegraph.create_page(
                    title, build_digest_nodes(topics, period, overview),
                )
            except Exception:
                logger.exception("Telegraph publish failed, sending as messages")
            else:
                return Digest(
                    [_teaser(icon, title, topics, url, overview)], topics, preview_url=url,
                )
        return Digest(format_report(topics, title, period, overview), topics)

    @staticmethod
    def _reporter(progress: ProgressCallback | None) -> ProgressCallback:
        async def report(text: str) -> None:
            if progress:
                try:
                    await progress(text)
                except Exception:
                    logger.debug("Progress update failed", exc_info=True)
        return report

    async def build(self, progress: ProgressCallback | None = None, save: bool = False) -> Digest:
        """Build the daily digest. `save` stores its topics for the weekly roundup.

        Raises DigestBusyError if another run is in progress.
        """
        report = self._reporter(progress)
        async with self._exclusive():
            hours = self._config.summary_hours
            now = datetime.now(timezone.utc)
            since = now - timedelta(hours=hours)

            await report("Fetching messages from channels...")
            messages, failed = await self._reader.fetch_all_channels(since)
            await self._db.delete_messages_before(
                now - timedelta(days=self._config.retention_days)
            )
            if not messages:
                return Digest([f"No messages found in the last {hours} hours."], failed=failed)

            await report(f"Fetched {len(messages)} messages. Summarising...")
            topics = await self._summarizer.summarize(messages)
            if not topics:
                return Digest(["No relevant topics found to summarise."], failed=failed)

            n_claims = sum(len(t.claims) for t in topics)
            await report(f"Found {len(topics)} topics. Fact-checking {n_claims} claims...")
            try:
                topics = await self._fact_checker.check_claims(topics)
            except Exception:
                # Partial digest beats no digest
                logger.exception("Fact-checking failed, proceeding without")

            if save:
                await self._db.save_digest(
                    now, json.dumps([topic_to_dict(t) for t in topics], ensure_ascii=False),
                )
                await self._db.delete_digests_before(now - DIGEST_RETENTION)

            local_now = now.astimezone(self._config.timezone)
            title = f"{self._config.digest_title} — {local_now.strftime(DATE_FORMAT)}"
            digest = await self._render("📰", title, topics, f"last {hours} hours", report)
            digest.failed = failed
            return digest

    async def build_weekly(self, progress: ProgressCallback | None = None) -> Digest:
        """Build the weekly roundup from the stored daily digests of the past week.

        Raises DigestBusyError if another run is in progress.
        """
        report = self._reporter(progress)
        async with self._exclusive():
            now = datetime.now(timezone.utc)
            tz = self._config.timezone
            rows = await self._db.get_digests_since(now - WEEKLY_WINDOW)
            days = [
                (created.astimezone(tz).strftime("%a %d %b"),
                 [topic_from_dict(d) for d in json.loads(topics_json)])
                for created, topics_json in rows
            ]
            days = [(label, topics) for label, topics in days if topics]
            if not days:
                return Digest([
                    "No daily digests stored for the past week, nothing to round up. "
                    "(Only scheduled daily digests are stored.)"
                ])

            await report(f"Summarising {len(days)} daily digests...")
            overview, topics = await self._summarizer.summarize_week(days)
            if not topics:
                return Digest(["Nothing worth a weekly roundup this week."])

            start = (now - timedelta(days=6)).astimezone(tz)
            title = (
                f"{self._config.weekly_title} — "
                f"{start.strftime('%d %b')} to {now.astimezone(tz).strftime('%d %b %Y')}"
            )
            return await self._render("🗞", title, topics, "past 7 days", report, overview)


async def send_report(
    bot: Bot, chat_id: int | str, digest: Digest, *, owner_view: bool = True,
) -> None:
    """Send a digest. `owner_view` adds private notes (unreadable channels)."""
    texts = list(digest.texts)
    if owner_view and digest.failed:
        note = _failed_note(digest.failed)
        if len(texts[-1]) + len(note) + 2 <= TELEGRAM_MSG_LIMIT:
            texts[-1] += f"\n\n{note}"
        else:
            texts.append(note)

    preview = (
        LinkPreviewOptions(url=digest.preview_url)
        if digest.preview_url
        else LinkPreviewOptions(is_disabled=True)
    )
    for text in texts:
        await bot.send_message(chat_id, text, link_preview_options=preview)


async def publish(bot: Bot, config: Config, digest: Digest) -> None:
    """Deliver a scheduled digest to DIGEST_CHAT (if set) or the owner.

    Empty results ("no messages", errors) only ever go to the owner.
    """
    if config.digest_chat is not None and digest.topics:
        await send_report(bot, config.digest_chat, digest, owner_view=False)
        if digest.failed:
            await bot.send_message(config.owner_id, _failed_note(digest.failed))
    else:
        await send_report(bot, config.owner_id, digest)


def _seconds_until(config: Config) -> float:
    now = datetime.now(config.timezone)
    target = datetime.combine(now.date(), config.digest_time, tzinfo=config.timezone)
    if target <= now:
        target = datetime.combine(
            now.date() + timedelta(days=1), config.digest_time, tzinfo=config.timezone,
        )
    return (target - now).total_seconds()


async def _run_job(
    bot: Bot, config: Config, label: str, build: Callable[[], Awaitable[Digest]],
) -> None:
    try:
        await publish(bot, config, await build())
    except DigestBusyError:
        logger.info("%s: a digest is already running, skipping", label)
    except Exception as exc:
        logger.exception("%s failed", label)
        try:
            await bot.send_message(
                config.owner_id,
                f"⚠️ {label} failed: {escape(str(exc))[:300]}\nCheck the bot logs.",
            )
        except Exception:
            logger.exception("Could not notify owner")


async def run_scheduler(bot: Bot, service: DigestService, config: Config) -> None:
    """Daily digest at DIGEST_TIME; on WEEKLY_DAY the weekly roundup follows it."""
    if config.digest_time is None:
        logger.info("Scheduled digests disabled")
        return

    while True:
        delay = _seconds_until(config)
        logger.info(
            "Next daily digest at %s %s (in %.1f h)",
            config.digest_time.strftime("%H:%M"), config.timezone.key, delay / 3600,
        )
        await asyncio.sleep(delay)

        weekday = datetime.now(config.timezone).weekday()
        if not await service.has_channels():
            logger.info("No channels subscribed, skipping scheduled digest")
        else:
            await _run_job(bot, config, "Daily digest", lambda: service.build(save=True))
            if config.weekly_day == weekday:
                await _run_job(bot, config, "Weekly roundup", service.build_weekly)
        # Avoid a double run if the job finished within the same minute
        await asyncio.sleep(60)
