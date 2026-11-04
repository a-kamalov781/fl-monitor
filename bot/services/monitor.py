"""Один такт: прочитать ленты, сохранить новое, доставить. Тихие часы и утренняя сводка живут здесь же."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from bot.config import Settings
from bot.db import PENDING, SKIPPED, Database, Order
from bot.services.cards import card_keyboard, card_text, digest_pages
from bot.services.drafts import build_draft
from bot.services.feed import FeedClient, FeedItem
from bot.services.filters import check, classify, rate
from bot.services.llm import llm_draft

log = logging.getLogger(__name__)

FAIL_ALERT_TICKS = 20  # примерно час, если все ленты подряд не отвечают
SEND_PAUSE = 1.0  # Telegram разрешает примерно одно сообщение в секунду в один чат


class Monitor:
    def __init__(self, bot, db: Database, settings: Settings, feeds: FeedClient, *, sleep=asyncio.sleep,
                 now=lambda: datetime.now(timezone.utc), llm_transport=None):
        self.bot, self.db, self.settings, self.feeds = bot, db, settings, feeds
        self.sleep, self.now, self.llm_transport = sleep, now, llm_transport
        self.failed_ticks = 0
        self.alerted = False
        self.last_poll: datetime | None = None

    def is_quiet(self, moment: datetime | None = None) -> bool:
        local = (moment or self.now()).astimezone(ZoneInfo(self.settings.tz))
        return self.settings.quiet_start_hour <= local.hour < self.settings.quiet_end_hour

    def analyze(self, item: FeedItem, now: datetime) -> Order:
        verdict = check(item.title, item.description, item.label, item.budget, self.settings.min_budget)
        category = classify(item.title, item.description, item.label)
        return Order(
            id=item.id, title=item.title, description=item.description, url=item.url, label=item.label,
            budget=item.budget, for_all=item.for_all, pub_date=item.pub_date, found_at=now,
            matched=verdict.matched, reject_reason=verdict.reason, category=category,
            score=rate(category, item.budget, item.title, item.description, item.label), delivery=SKIPPED,
        )

    async def poll(self) -> None:
        result = await self.feeds.fetch_all()
        await self._track_health(result.nothing_works)
        if result.nothing_works:
            return
        now = self.now()
        self.last_poll = now
        unique = {item.id: item for item in result.items}
        known = await self.db.known_ids(list(unique))
        first_run = await self.db.is_empty()
        new = [self.analyze(item, now) for item_id, item in unique.items() if item_id not in known]

        if first_run:
            # В лентах заказы за недели. В статистику идут все, в чат только несколько самых свежих
            fresh = sorted((o for o in new if o.matched), key=lambda o: o.pub_date, reverse=True)
            for order in fresh[:self.settings.first_run_send]:
                order.delivery = PENDING
        else:
            limit = now - timedelta(hours=self.settings.max_age_hours)
            quiet = self.is_quiet(now)
            for order in new:
                if order.matched and order.pub_date >= limit:
                    order.delivery, order.held = PENDING, quiet
        await self.db.insert_many(new)
        if new:
            log.info("new orders: %s, to deliver: %s", len(new), sum(o.delivery == PENDING for o in new))

    async def _track_health(self, broken: bool) -> None:
        if broken:
            self.failed_ticks += 1
            if self.failed_ticks >= FAIL_ALERT_TICKS and not self.alerted:
                self.alerted = await self._say("⚠️ Ленты FL.ru не отвечают уже около часа. Заказы сейчас не приходят.")
            return
        self.failed_ticks = 0
        if self.alerted and await self._say("✅ Ленты FL.ru снова отвечают."):
            self.alerted = False

    async def _say(self, text: str) -> bool:
        try:
            await self.bot.send_message(self.settings.admin_id, text)
            return True
        except TelegramAPIError as e:
            log.warning("service message not sent: %s", type(e).__name__)
            return False

    async def make_draft(self, order: Order, variant: int = 0) -> str:
        if self.settings.llm_enabled:
            text = await llm_draft(self.settings, order.title, order.description, order.category, order.budget,
                                   transport=self.llm_transport)
            if text:
                return text
        return build_draft(order.title, order.description, order.category, order.budget, variant)

    async def ensure_draft(self, order: Order) -> Order:
        if not order.draft:
            order.draft = await self.make_draft(order, order.variant)
            await self.db.save_draft(order.id, order.draft, order.variant)
        return order

    async def send_card(self, order: Order) -> None:
        await self.ensure_draft(order)
        await self.bot.send_message(self.settings.admin_id, card_text(order, self.now()),
                                    reply_markup=card_keyboard(order))

    async def deliver(self) -> None:
        """Заказ остаётся pending, пока Telegram его не принял, поэтому при сбое уйдёт в следующий такт."""
        if self.is_quiet():
            return
        pending = await self.db.pending()
        night = [o for o in pending if o.held]
        if len(night) > 1:
            await self._send_digest(night)
            pending = [o for o in pending if not o.held]
        for index, order in enumerate(pending):
            if index:
                await self.sleep(SEND_PAUSE)
            try:
                await self.send_card(order)
            except TelegramBadRequest as e:
                # Этот заказ не отправится и при повторе. Он не должен держать очередь
                log.error("order %s rejected by Telegram: %s", order.id, e)
                await self.db.mark_failed(order.id)
                continue
            except TelegramAPIError as e:
                log.warning("delivery stopped: %s", type(e).__name__)
                return
            await self.db.mark_sent(order.id, self.now())

    async def _send_digest(self, orders: list[Order]) -> None:
        now = self.now()
        try:
            for index, (text, keyboard) in enumerate(digest_pages(orders, now)):
                if index:
                    await self.sleep(SEND_PAUSE)
                await self.bot.send_message(self.settings.admin_id, text, reply_markup=keyboard)
        except TelegramAPIError as e:
            log.warning("digest not sent: %s", type(e).__name__)
            return
        for order in orders:
            await self.db.mark_sent(order.id, now)

    async def tick(self) -> None:
        # Сбой чтения лент не должен мешать доставке уже найденного
        try:
            await self.poll()
        except Exception:
            log.exception("poll failed")
        await self.deliver()
