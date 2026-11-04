"""Повтор запросов к Telegram при сетевых сбоях и ограничении частоты."""
from __future__ import annotations

import asyncio
import logging

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import GetUpdates

log = logging.getLogger(__name__)


class RetryRequests(BaseRequestMiddleware):
    """Всего 3 попытки. GetUpdates не трогаем: у цикла опроса свой повтор."""

    def __init__(self, attempts: int = 3, base_delay: float = 0.5, max_retry_after: int = 30, sleep=asyncio.sleep):
        self.attempts = attempts
        self.base_delay = base_delay
        self.max_retry_after = max_retry_after
        self.sleep = sleep

    async def __call__(self, make_request, bot, method):
        if isinstance(method, GetUpdates):
            return await make_request(bot, method)
        for attempt in range(1, self.attempts + 1):
            try:
                return await make_request(bot, method)
            except TelegramRetryAfter as e:
                if attempt == self.attempts or e.retry_after > self.max_retry_after:
                    raise
                delay = e.retry_after
            except TelegramNetworkError:
                if attempt == self.attempts:
                    raise
                delay = self.base_delay * 2 ** (attempt - 1)
            log.warning("%s failed, attempt %s of %s, next in %s s", type(method).__name__, attempt, self.attempts, delay)
            await self.sleep(delay)
