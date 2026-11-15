from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

import anthropic
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.channel_reader import ChannelReader
from bot.config import Config
from bot.database import Database
from bot.digest import DigestService, run_scheduler
from bot.fact_checker import FactChecker
from bot.handlers import inject, router
from bot.summarizer import Summarizer
from bot.telegraph import TelegraphClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
for _noisy in ("httpx", "httpx2"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = Config.from_env()
    logger.info("Data directory: %s", config.data_dir)

    # Database
    db = Database(str(config.db_path))
    await db.connect()

    # Telethon channel reader
    reader = ChannelReader(
        api_id=config.telegram_api_id,
        api_hash=config.telegram_api_hash,
        db=db,
        session_path=config.session_path,
    )
    await reader.start()

    # AI components (share one Anthropic client)
    claude = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)
    telegraph = TelegraphClient(
        config.data_dir, author_name=config.brand_name, author_url=config.brand_url,
    )
    digest = DigestService(
        config=config,
        db=db,
        reader=reader,
        summarizer=Summarizer(
            claude,
            model=config.claude_model,
            focus=config.digest_focus,
            categories=config.digest_categories,
        ),
        fact_checker=FactChecker(claude, model=config.claude_model),
        telegraph=telegraph,
    )

    inject(config=config, db=db, reader=reader, digest=digest)

    # aiogram bot
    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    scheduler = asyncio.create_task(run_scheduler(bot, digest, config))

    logger.info(
        "Bot starting (model=%s, brand=%s, digest_chat=%s)",
        config.claude_model, config.brand_name, config.digest_chat or "owner DMs",
    )
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        await reader.stop()
        await claude.close()
        await telegraph.close()
        await db.close()
        await bot.session.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
