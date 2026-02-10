from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command

from bot.channel_reader import ChannelReader
from bot.config import Config
from bot.database import Database
from bot.fact_checker import FactChecker
from bot.formatter import format_report
from bot.summarizer import Summarizer

logger = logging.getLogger(__name__)

router = Router()

# These are injected at startup via router["key"] = value
_COMPONENTS: dict = {}


def inject(
    config: Config,
    db: Database,
    reader: ChannelReader,
    summarizer: Summarizer,
    fact_checker: FactChecker,
) -> None:
    _COMPONENTS["config"] = config
    _COMPONENTS["db"] = db
    _COMPONENTS["reader"] = reader
    _COMPONENTS["summarizer"] = summarizer
    _COMPONENTS["fact_checker"] = fact_checker


def _is_owner(message: types.Message) -> bool:
    return message.from_user and message.from_user.id == _COMPONENTS["config"].owner_id


# --- /start ---

@router.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    if not _is_owner(message):
        return
    await message.answer(
        "<b>Telegram Aggregator Bot</b>\n\n"
        "I collect messages from public Telegram channels and deliver a "
        "topic-clustered, fact-checked summary on demand.\n\n"
        "Use /help to see available commands.",
        parse_mode=ParseMode.HTML,
    )


# --- /help ---

@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    if not _is_owner(message):
        return
    await message.answer(
        "<b>Available commands:</b>\n\n"
        "/summary — Generate a digest of the last 24 hours\n"
        "/add_channel &lt;username&gt; — Subscribe to a public channel\n"
        "/remove_channel &lt;username&gt; — Unsubscribe from a channel\n"
        "/list_channels — Show all subscribed channels\n"
        "/help — Show this message",
        parse_mode=ParseMode.HTML,
    )


# --- /add_channel ---

@router.message(Command("add_channel"))
async def cmd_add_channel(message: types.Message) -> None:
    if not _is_owner(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /add_channel &lt;username&gt;", parse_mode=ParseMode.HTML)
        return

    username = parts[1].strip().lstrip("@")
    if not username:
        await message.answer("Please provide a channel username.")
        return

    db: Database = _COMPONENTS["db"]
    added = await db.add_channel(username)
    if added:
        await message.answer(f"Subscribed to @{username}")
    else:
        await message.answer(f"Already subscribed to @{username}")


# --- /remove_channel ---

@router.message(Command("remove_channel"))
async def cmd_remove_channel(message: types.Message) -> None:
    if not _is_owner(message):
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Usage: /remove_channel &lt;username&gt;", parse_mode=ParseMode.HTML)
        return

    username = parts[1].strip().lstrip("@")
    db: Database = _COMPONENTS["db"]
    removed = await db.remove_channel(username)
    if removed:
        await message.answer(f"Unsubscribed from @{username}")
    else:
        await message.answer(f"Channel @{username} not found in subscriptions.")


# --- /list_channels ---

@router.message(Command("list_channels"))
async def cmd_list_channels(message: types.Message) -> None:
    if not _is_owner(message):
        return

    db: Database = _COMPONENTS["db"]
    channels = await db.list_channels()
    if not channels:
        await message.answer("No channels subscribed yet. Use /add_channel to add one.")
        return

    lines = [f"  @{ch}" for ch in channels]
    await message.answer(
        "<b>Subscribed channels:</b>\n" + "\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# --- /summary ---

@router.message(Command("summary"))
async def cmd_summary(message: types.Message) -> None:
    if not _is_owner(message):
        return

    config: Config = _COMPONENTS["config"]
    db: Database = _COMPONENTS["db"]
    reader: ChannelReader = _COMPONENTS["reader"]
    summarizer: Summarizer = _COMPONENTS["summarizer"]
    fact_checker: FactChecker = _COMPONENTS["fact_checker"]

    channels = await db.list_channels()
    if not channels:
        await message.answer("No channels subscribed. Use /add_channel first.")
        return

    status_msg = await message.answer("Fetching messages from channels...")

    since = datetime.now(timezone.utc) - timedelta(hours=config.summary_hours)

    try:
        all_messages = await reader.fetch_all_channels(since)
    except Exception:
        logger.exception("Failed to fetch messages")
        await status_msg.edit_text("Failed to fetch messages. Check logs for details.")
        return

    if not all_messages:
        await status_msg.edit_text("No messages found in the last 24 hours.")
        return

    await status_msg.edit_text(
        f"Fetched {len(all_messages)} messages. Summarising..."
    )

    try:
        topics = await summarizer.summarize(all_messages)
    except Exception:
        logger.exception("Summarisation failed")
        await status_msg.edit_text("Summarisation failed. Check logs for details.")
        return

    if not topics:
        await status_msg.edit_text("No meaningful topics found to summarise.")
        return

    await status_msg.edit_text(
        f"Found {len(topics)} topics. Fact-checking claims..."
    )

    try:
        topics = await fact_checker.check_claims(topics)
    except Exception:
        logger.exception("Fact-checking failed, proceeding without")

    await status_msg.delete()

    report_messages = format_report(topics)
    for text in report_messages:
        await message.answer(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
