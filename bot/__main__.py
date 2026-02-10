from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.channel_reader import ChannelReader
from bot.config import Config
from bot.database import Database
from bot.fact_checker import FactChecker
from bot.handlers import inject, router
from bot.summarizer import Summarizer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = Config.from_env()

    # Database
    db = Database(config.db_path)
    await db.connect()

    # Telethon channel reader
    reader = ChannelReader(
        api_id=config.telegram_api_id,
        api_hash=config.telegram_api_hash,
        db=db,
    )
    await reader.start()

    # AI components
    summarizer = Summarizer(api_key=config.anthropic_api_key)
    fact_checker = FactChecker(api_key=config.tavily_api_key)

    # Inject dependencies into handlers
    inject(
        config=config,
        db=db,
        reader=reader,
        summarizer=summarizer,
        fact_checker=fact_checker,
    )

    # aiogram bot
    bot = Bot(token=config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    logger.info("Bot starting — press Ctrl+C to stop")
    try:
        await dp.start_polling(bot)
    finally:
        await reader.stop()
        await db.close()
        await bot.session.close()
        logger.info("Shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
