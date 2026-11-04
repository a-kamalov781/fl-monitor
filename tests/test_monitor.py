"""Весь путь заказа: лента → база → Telegram. Дедупликация, первый запуск, тихие часы, сбои."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage

from bot.db import FAILED, PENDING, SENT, SKIPPED
from bot.services.monitor import FAIL_ALERT_TICKS, Monitor
from tests.harness import NOON, make, rss_item

BOT = "Телеграм-бот для записи (Бюджет: 20 000  &#8381;)"
# 02:00 МСК и 08:00 МСК
NIGHT = datetime(2026, 11, 4, 23, 0, tzinfo=timezone.utc)
MORNING = datetime(2026, 11, 5, 5, 0, tzinfo=timezone.utc)


async def started(tmp_path, **kw):
    h = await make(tmp_path, **kw)
    await h.start()
    return h


async def test_first_run_saves_everything_and_sends_three_newest(tmp_path):
    h = await make(tmp_path)
    items = [rss_item(i, f"Телеграм-бот номер {i}", pub=NOON - timedelta(days=10 - i)) for i in range(1, 7)]
    await h.tick(*items, rss_item(50, "Логотип для кафе"))

    assert len(await h.db.all_orders()) == 7
    assert len(h.session.messages) == 3
    assert ["номер 4" in h.session.messages[0].text, "номер 5" in h.session.messages[1].text,
            "номер 6" in h.session.messages[2].text] == [True] * 3
    assert (await h.db.get(1)).delivery == SKIPPED and (await h.db.get(6)).delivery == SENT


async def test_new_matching_order_is_sent_once(tmp_path):
    h = await started(tmp_path)
    await h.tick(rss_item(10, BOT))
    await h.tick(rss_item(10, BOT))
    await h.tick(rss_item(10, BOT))
    assert len(h.session.messages) == 1
    assert "Телеграм-бот для записи" in h.session.messages[0].text


async def test_same_order_in_two_feeds_is_sent_once(tmp_path):
    h = await started(tmp_path)
    await h.tick(rss_item(10, BOT), rss_item(10, BOT))
    assert len(h.session.messages) == 1


async def test_dedup_survives_restart(tmp_path):
    h = await started(tmp_path)
    await h.tick(rss_item(10, BOT))
    h.monitor = Monitor(h.bot, h.db, h.settings, h.feeds, now=h.clock)
    await h.tick(rss_item(10, BOT))
    assert len(h.session.messages) == 1


async def test_changed_order_is_not_overwritten(tmp_path):
    h = await started(tmp_path)
    await h.tick(rss_item(10, BOT))
    await h.tick(rss_item(10, "Совсем другой заголовок"))
    assert "Телеграм-бот" in (await h.db.get(10)).title


async def test_rejected_order_is_saved_with_reason_but_not_sent(tmp_path):
    h = await started(tmp_path)
    await h.tick(rss_item(10, "Телеграм-бот (Бюджет: 1 000  &#8381;)"), rss_item(11, "Логотип"),
                 rss_item(12, "Телеграм-бот бесплатно"))
    assert h.session.messages == []
    assert "бюджет" in (await h.db.get(10)).reject_reason
    assert (await h.db.get(11)).reject_reason == "нет ключевых слов"
    assert "минус-слово" in (await h.db.get(12)).reject_reason


async def test_negotiable_order_is_sent_and_marked(tmp_path):
    h = await started(tmp_path)
    await h.tick(rss_item(10, "Телеграм-бот"))
    assert "по договорённости" in h.session.messages[0].text


async def test_old_order_goes_to_stats_only(tmp_path):
    """Например, после добавления новой ленты: заказы за месяц не должны прийти разом."""
    h = await started(tmp_path)
    await h.tick(rss_item(10, BOT, pub=NOON - timedelta(hours=25)))
    assert h.session.messages == []
    assert (await h.db.get(10)).matched is True and (await h.db.get(10)).delivery == SKIPPED


async def test_scores_and_category_are_saved(tmp_path):
    h = await started(tmp_path)
    await h.tick(rss_item(10, BOT))
    order = await h.db.get(10)
    assert order.category == "booking" and order.score == 5 and order.draft and order.notified_at == NOON


async def test_quiet_hours_hold_and_morning_digest_arrives(tmp_path):
    h = await started(tmp_path)
    h.clock.moment = NIGHT
    await h.tick(rss_item(10, BOT, pub=NIGHT), rss_item(11, "Интеграция amoCRM", pub=NIGHT))
    assert h.session.messages == []
    assert (await h.db.get(10)).delivery == PENDING

    h.clock.moment = NIGHT + timedelta(hours=3)  # 05:00 МСК, всё ещё тихо
    await h.tick()
    assert h.session.messages == []

    h.clock.moment = MORNING
    await h.tick(rss_item(10, BOT, pub=NIGHT), rss_item(11, "Интеграция amoCRM", pub=NIGHT))
    assert len(h.session.messages) == 1
    digest = h.session.messages[0]
    assert "Заказы за ночь: 2" in digest.text and "Телеграм-бот для записи" in digest.text and "amoCRM" in digest.text
    assert (await h.db.get(10)).delivery == SENT and (await h.db.get(10)).notified_at == MORNING

    await h.tick()
    assert len(h.session.messages) == 1  # сводка второй раз не приходит


async def test_digest_keeps_best_orders_on_top_and_has_buttons(tmp_path):
    h = await started(tmp_path)
    h.clock.moment = NIGHT
    await h.tick(rss_item(10, "Логотип с ботом", pub=NIGHT), rss_item(11, BOT, pub=NIGHT))
    h.clock.moment = MORNING
    await h.monitor.deliver()
    digest = h.session.messages[0]
    assert digest.text.index("Телеграм-бот для записи") < digest.text.index("Логотип с ботом")
    numbers = [b.callback_data for row in digest.reply_markup.inline_keyboard for b in row]
    assert numbers == ["open:11", "open:10"]


async def test_long_digest_is_split_within_telegram_limit(tmp_path):
    h = await started(tmp_path)
    h.clock.moment = NIGHT
    await h.tick(*[rss_item(100 + i, f"Телеграм-бот с очень длинным названием для проверки сводки {i} " * 3, pub=NIGHT)
                   for i in range(45)])
    h.clock.moment = MORNING
    await h.monitor.deliver()
    assert len(h.session.messages) == 3
    assert all(len(m.text) <= 4096 for m in h.session.messages)
    numbers = [b.callback_data for m in h.session.messages for row in m.reply_markup.inline_keyboard for b in row]
    assert len(numbers) == 45 and len(set(numbers)) == 45


async def test_one_night_order_comes_as_a_regular_card(tmp_path):
    h = await started(tmp_path)
    h.clock.moment = NIGHT
    await h.tick(rss_item(10, BOT, pub=NIGHT))
    h.clock.moment = MORNING
    await h.monitor.deliver()
    assert "<code>" in h.session.messages[0].text


async def test_orders_arriving_at_the_quiet_border(tmp_path):
    h = await started(tmp_path)
    assert h.monitor.is_quiet(datetime(2026, 11, 4, 22, 0, tzinfo=timezone.utc))  # 01:00 МСК
    assert not h.monitor.is_quiet(datetime(2026, 11, 4, 21, 59, tzinfo=timezone.utc))  # 00:59 МСК
    assert h.monitor.is_quiet(datetime(2026, 11, 5, 4, 59, tzinfo=timezone.utc))  # 07:59 МСК
    assert not h.monitor.is_quiet(MORNING)  # 08:00 МСК


async def test_day_order_stays_a_card_when_sent_along_with_digest(tmp_path):
    h = await started(tmp_path)
    h.clock.moment = NIGHT
    await h.tick(rss_item(10, BOT, pub=NIGHT), rss_item(11, "Интеграция amoCRM", pub=NIGHT))
    h.clock.moment = MORNING
    await h.tick(rss_item(12, "Парсер цен", pub=MORNING))
    assert len(h.session.messages) == 2
    assert "Заказы за ночь: 2" in h.session.messages[0].text and "Парсер цен" in h.session.messages[1].text


async def test_network_failure_keeps_order_for_next_tick(tmp_path):
    h = await started(tmp_path)
    h.session.fail_with = TelegramNetworkError(method=None, message="down")
    await h.tick(rss_item(10, BOT))
    assert (await h.db.get(10)).delivery == PENDING

    h.session.fail_with = None
    await h.tick(rss_item(10, BOT))
    assert len(h.session.messages) == 1 and (await h.db.get(10)).delivery == SENT


async def test_broken_order_does_not_block_the_queue(tmp_path):
    h = await started(tmp_path)
    h.session.fail_with = TelegramBadRequest(method=None, message="can't parse entities")
    h.session.fail_on = lambda m: isinstance(m, SendMessage) and "Сломанный" in m.text
    await h.tick(rss_item(10, "Сломанный бот", pub=NOON - timedelta(minutes=5)), rss_item(11, BOT))
    assert (await h.db.get(10)).delivery == FAILED
    assert (await h.db.get(11)).delivery == SENT


async def test_owner_who_has_not_started_the_bot_gets_orders_later(tmp_path):
    from aiogram.exceptions import TelegramForbiddenError
    h = await started(tmp_path)
    h.session.fail_with = TelegramForbiddenError(method=None, message="bot can't initiate conversation")
    await h.tick(rss_item(10, BOT))
    assert (await h.db.get(10)).delivery == PENDING
    h.session.fail_with = None
    await h.monitor.deliver()
    assert (await h.db.get(10)).delivery == SENT


async def test_cards_are_paced(tmp_path):
    h = await started(tmp_path)
    h.slept.clear()
    await h.tick(rss_item(10, BOT), rss_item(11, BOT), rss_item(12, BOT))
    assert h.slept == [1.0, 1.0]


async def test_alert_after_an_hour_of_broken_feeds_and_recovery_message(tmp_path):
    h = await started(tmp_path)
    h.feed_status = 500
    for _ in range(FAIL_ALERT_TICKS - 1):
        await h.tick()
    assert h.session.messages == []
    for _ in range(3):
        await h.tick()
    assert len(h.session.messages) == 1 and "не отвечают" in h.session.messages[0].text

    h.feed_status = 200
    await h.tick(rss_item(5, "Логотип"))
    assert "снова отвечают" in h.session.messages[-1].text
    assert len(h.session.messages) == 2


async def test_poll_error_does_not_stop_delivery(tmp_path, monkeypatch):
    h = await started(tmp_path)
    await h.tick(rss_item(10, BOT))
    h.session.calls.clear()
    await h.db._update(10, delivery=PENDING)

    async def explode():
        raise RuntimeError("feed parser bug")

    monkeypatch.setattr(h.feeds, "fetch_all", explode)
    await h.monitor.tick()
    assert len(h.session.messages) == 1


async def test_network_error_while_reading_feed_changes_nothing(tmp_path):
    h = await started(tmp_path)

    def handle(request):
        raise httpx.ConnectError("no route")

    h.feeds.http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    await h.tick(rss_item(10, BOT))
    assert await h.db.get(10) is None and h.session.messages == []


async def test_llm_draft_is_used_when_configured(tmp_path):
    h = await started(tmp_path, llm_base_url="https://llm.test/v1", llm_api_key="k", llm_model="m")
    h.monitor.llm_transport = httpx.MockTransport(lambda r: httpx.Response(
        200, json={"choices": [{"message": {"content": "Текст от модели"}}]}))
    await h.tick(rss_item(10, BOT))
    assert "<code>Текст от модели</code>" in h.session.messages[0].text


async def test_template_is_used_when_llm_fails(tmp_path):
    h = await started(tmp_path, llm_base_url="https://llm.test/v1", llm_api_key="k", llm_model="m")
    h.monitor.llm_transport = httpx.MockTransport(lambda r: httpx.Response(500))
    await h.tick(rss_item(10, BOT))
    assert "Делал почти такого же бота" in h.session.messages[0].text
