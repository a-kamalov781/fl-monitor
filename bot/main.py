from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, CallbackQuery
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from bot import handlers
from bot.config import Settings, get_settings
from bot.db import Database
from bot.services.feed import FeedClient
from bot.services.monitor import Monitor
from bot.services.retry import RetryRequests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# Иначе в логе строка на каждый запрос к лентам и на каждый такт
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("main")


class CallbackGuard(BaseMiddleware):
    """Обработчики отвечают на нажатие сами и сразу. Это страховка: на ошибку и на кнопку без обработчика."""

    @staticmethod
    async def _answer(event: CallbackQuery) -> None:
        try:
            await event.answer()
        except TelegramAPIError:
            pass

    async def __call__(self, handler, event: CallbackQuery, data: dict):
        try:
            result = await handler(event, data)
        except Exception:
            await self._answer(event)
            raise
        if result is UNHANDLED:
            await self._answer(event)
        return result


def bot_defaults() -> DefaultBotProperties:
    return DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True)


def build_dispatcher(settings: Settings, db: Database, monitor: Monitor) -> Dispatcher:
    dp = Dispatcher()
    dp["settings"], dp["db"], dp["monitor"] = settings, db, monitor
    dp.callback_query.outer_middleware(CallbackGuard())
    dp.include_router(handlers.router)
    return dp


async def main() -> None:
    settings = get_settings()
    db = Database(settings.db_path)
    await db.init()

    bot = Bot(settings.bot_token, default=bot_defaults())
    bot.session.middleware(RetryRequests())
    feeds = FeedClient(settings.feed_urls, settings.user_agent, settings.feed_pause_seconds)
    monitor = Monitor(bot, db, settings, feeds)
    dp = build_dispatcher(settings, db, monitor)

    await bot.set_my_commands([
        BotCommand(command="start", description="Что умеет бот"),
        BotCommand(command="stats", description="Статистика заказов"),
        BotCommand(command="top", description="Частые формулировки"),
        BotCommand(command="export", description="Все заказы в CSV"),
    ])

    scheduler = AsyncIOScheduler(timezone=settings.tz)
    scheduler.add_job(monitor.tick, "interval", seconds=settings.poll_seconds, max_instances=1, coalesce=True,
                      next_run_time=datetime.now(timezone.utc))
    scheduler.start()

    log.info("Bot started: feeds=%s poll=%ss llm=%s", len(settings.feed_urls), settings.poll_seconds, settings.llm_enabled)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.shutdown(wait=False)
        await feeds.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped")
