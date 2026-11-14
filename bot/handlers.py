from __future__ import annotations

import logging
from html import escape

from aiogram import Router, types
from aiogram.filters import Command, CommandObject

from bot.channel_reader import ChannelReader
from bot.config import Config
from bot.database import Database
from bot.digest import DigestBusyError, DigestService, send_report

logger = logging.getLogger(__name__)

router = Router()

# Injected at startup via inject()
_COMPONENTS: dict = {}


def inject(
    config: Config,
    db: Database,
    reader: ChannelReader,
    digest: DigestService,
) -> None:
    _COMPONENTS["config"] = config
    _COMPONENTS["db"] = db
    _COMPONENTS["reader"] = reader
    _COMPONENTS["digest"] = digest


def _is_owner(message: types.Message) -> bool:
    return bool(
        message.from_user and message.from_user.id == _COMPONENTS["config"].owner_id
    )


def _schedule_text(config: Config) -> str:
    if config.digest_time is None:
        return "Daily digest is off."
    return (
        f"Daily digest is sent at {config.digest_time.strftime('%H:%M')} "
        f"({config.timezone.key})."
    )


# --- /start ---

@router.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    if not _is_owner(message):
        return
    await message.answer(
        "<b>Telegram Aggregator Bot</b>\n\n"
        "I collect messages from public Telegram channels and deliver a "
        "topic-clustered, fact-checked digest.\n\n"
        f"{_schedule_text(_COMPONENTS['config'])}\n"
        "Use /help to see available commands.",
    )


# --- /help ---

@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    if not _is_owner(message):
        return
    config: Config = _COMPONENTS["config"]
    await message.answer(
        "<b>Available commands:</b>\n\n"
        f"/summary — Generate a digest of the last {config.summary_hours} hours now\n"
        "/add_channel &lt;username&gt; — Subscribe to a public channel\n"
        "/remove_channel &lt;username&gt; — Unsubscribe from a channel\n"
        "/list_channels — Show all subscribed channels\n"
        "/help — Show this message\n\n"
        f"{_schedule_text(config)}",
    )


# --- /add_channel ---

@router.message(Command("add_channel"))
async def cmd_add_channel(message: types.Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return

    username = (command.args or "").strip().lstrip("@")
    if not username:
        await message.answer("Usage: /add_channel &lt;username&gt;")
        return

    reader: ChannelReader = _COMPONENTS["reader"]
    canonical = await reader.resolve_channel(username)
    if canonical is None:
        await message.answer(
            f"Couldn't find a public channel @{username}. Check the spelling."
        )
        return

    db: Database = _COMPONENTS["db"]
    if await db.add_channel(canonical):
        await message.answer(f"Subscribed to @{canonical}")
    else:
        await message.answer(f"Already subscribed to @{canonical}")


# --- /remove_channel ---

@router.message(Command("remove_channel"))
async def cmd_remove_channel(message: types.Message, command: CommandObject) -> None:
    if not _is_owner(message):
        return

    username = (command.args or "").strip().lstrip("@")
    if not username:
        await message.answer("Usage: /remove_channel &lt;username&gt;")
        return

    db: Database = _COMPONENTS["db"]
    if await db.remove_channel(username):
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
        f"<b>Subscribed channels ({len(channels)}):</b>\n" + "\n".join(lines),
    )


# --- /summary ---

@router.message(Command("summary"))
async def cmd_summary(message: types.Message) -> None:
    if not _is_owner(message):
        return

    digest: DigestService = _COMPONENTS["digest"]
    if not await digest.has_channels():
        await message.answer("No channels subscribed. Use /add_channel first.")
        return
    if digest.busy:
        await message.answer("A digest is already being generated — hang tight.")
        return

    status_msg = await message.answer("Starting digest...")

    async def progress(text: str) -> None:
        await status_msg.edit_text(text)

    try:
        texts = await digest.build(progress)
    except DigestBusyError:
        await status_msg.edit_text("A digest is already being generated — hang tight.")
        return
    except Exception as exc:
        logger.exception("Digest generation failed")
        await status_msg.edit_text(
            f"Digest generation failed: {escape(str(exc))[:500]}\nCheck logs for details."
        )
        return

    await status_msg.delete()
    await send_report(message.bot, message.chat.id, texts)
