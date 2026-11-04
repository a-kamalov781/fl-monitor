from __future__ import annotations

from datetime import timedelta

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from bot.config import Settings
from bot.db import Database
from bot.services.cards import ago, card_keyboard, card_text
from bot.services.monitor import Monitor
from bot.services.stats import export_csv, stats_text, top_text

router = Router()


async def _is_owner(event: Message | CallbackQuery, settings: Settings) -> bool:
    return bool(event.from_user) and event.from_user.id == settings.admin_id


router.message.filter(_is_owner)
router.callback_query.filter(_is_owner)


@router.message(CommandStart())
@router.message(Command("help"))
async def start(message: Message, monitor: Monitor, settings: Settings) -> None:
    last = f"Последний опрос: {ago(monitor.last_poll, monitor.now())}." if monitor.last_poll else "Опрос ещё не прошёл."
    await message.answer(
        "Слежу за заказами FL.ru и готовлю черновики откликов. На сайте ничего не делаю: только читаю публичный RSS.\n\n"
        f"Опрос раз в {round(settings.poll_seconds / 60)} мин. С 01:00 до 08:00 МСК заказы копятся, утром придёт сводка.\n"
        f"{last}\n\n"
        "/stats — сколько заказов и как быстро вы откликаетесь\n"
        "/top — частые формулировки за неделю\n"
        "/export — все заказы в CSV"
    )


@router.message(Command("stats"))
async def stats(message: Message, db: Database, monitor: Monitor) -> None:
    now = monitor.now()
    await message.answer(stats_text(await db.since(now - timedelta(days=7)), now))


@router.message(Command("top"))
async def top(message: Message, db: Database, monitor: Monitor) -> None:
    await message.answer(top_text(await db.since(monitor.now() - timedelta(days=7))))


@router.message(Command("export"))
async def export(message: Message, db: Database, monitor: Monitor) -> None:
    orders = await db.all_orders()
    name = f"fl-orders-{monitor.now():%Y-%m-%d}.csv"
    await message.answer_document(BufferedInputFile(export_csv(orders), name), caption=f"Заказов в файле: {len(orders)}")


async def _load(callback: CallbackQuery, db: Database):
    order = await db.get(int(callback.data.split(":", 1)[1]))
    if order is None:
        await callback.answer("Заказ не найден", show_alert=True)
    return order


async def _redraw(callback: CallbackQuery, order, monitor: Monitor) -> None:
    try:
        await callback.message.edit_text(card_text(order, monitor.now()), reply_markup=card_keyboard(order))
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise


@router.callback_query(F.data.startswith("open:"))
async def open_card(callback: CallbackQuery, db: Database, monitor: Monitor) -> None:
    order = await _load(callback, db)
    if order is None:
        return
    await callback.answer()
    await monitor.send_card(order)


@router.callback_query(F.data.startswith("alt:"))
async def another_variant(callback: CallbackQuery, db: Database, monitor: Monitor) -> None:
    order = await _load(callback, db)
    if order is None:
        return
    await callback.answer("Готовлю другой вариант")
    order.variant += 1
    order.draft = await monitor.make_draft(order, order.variant)
    await db.save_draft(order.id, order.draft, order.variant)
    await _redraw(callback, order, monitor)


@router.callback_query(F.data.startswith("hide:"))
async def hide(callback: CallbackQuery, db: Database, monitor: Monitor) -> None:
    order = await _load(callback, db)
    if order is None:
        return
    await callback.answer("Скрыто")
    await db.mark_hidden(order.id, monitor.now())
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        # Telegram не даёт боту удалить сообщение старше 48 часов
        await callback.message.edit_text(f"🙈 Скрыто: заказ {order.id}")


@router.callback_query(F.data.startswith("done:"))
async def responded(callback: CallbackQuery, db: Database, monitor: Monitor) -> None:
    order = await _load(callback, db)
    if order is None:
        return
    if order.responded_at:
        await callback.answer("Уже отмечено")
        return
    await callback.answer("Отметил")
    order.responded_at = monitor.now()
    await db.mark_responded(order.id, order.responded_at)
    await _redraw(callback, order, monitor)
