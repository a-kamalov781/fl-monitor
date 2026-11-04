"""Как заказ выглядит в Telegram."""
from __future__ import annotations

from datetime import datetime
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.db import Order
from bot.services.drafts import format_price

SNIPPET_LINES = 3
SNIPPET_LENGTH = 320
DIGEST_PAGE = 20  # столько строк сводки точно влезает в лимит Telegram 4096 знаков


def _e(text: str) -> str:
    return escape(text, quote=False)


def ago(moment: datetime, now: datetime) -> str:
    minutes = max(int((now - moment).total_seconds() // 60), 0)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин назад"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч назад"
    return f"{hours // 24} дн назад"


def duration(minutes: float) -> str:
    minutes = round(minutes)
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    return f"{hours} ч {rest} мин" if rest else f"{hours} ч"


def budget_text(order: Order) -> str:
    return f"{format_price(order.budget)} ₽" if order.budget is not None else "💬 по договорённости"


def snippet(description: str) -> str:
    lines = [line for line in description.splitlines() if line.strip()][:SNIPPET_LINES]
    text = "\n".join(lines)
    if len(text) > SNIPPET_LENGTH:
        text = text[:SNIPPET_LENGTH].rsplit(" ", 1)[0] + "…"
    return text


def card_text(order: Order, now: datetime) -> str:
    parts = [f"🆕 {order.score}★ <b>{_e(order.title)}</b>", budget_text(order), ago(order.pub_date, now), order.category]
    if order.for_all:
        parts.insert(2, "для всех")
    text = " · ".join(parts)
    body = snippet(order.description)
    if body:
        text += f"\n\n{_e(body)}"
    if order.draft:
        text += f"\n\n<code>{_e(order.draft)}</code>"
    if order.responded_at and order.notified_at:
        minutes = (order.responded_at - order.notified_at).total_seconds() / 60
        text += f"\n\n✅ Откликнулись через {duration(minutes)}"
    return text


def card_keyboard(order: Order) -> InlineKeyboardMarkup:
    open_button = InlineKeyboardButton(text="🔗 Открыть заказ", url=order.url)
    if order.responded_at:
        return InlineKeyboardMarkup(inline_keyboard=[[open_button]])
    return InlineKeyboardMarkup(inline_keyboard=[
        [open_button, InlineKeyboardButton(text="✏️ Другой вариант", callback_data=f"alt:{order.id}")],
        [InlineKeyboardButton(text="🙈 Скрыть", callback_data=f"hide:{order.id}"),
         InlineKeyboardButton(text="✅ Откликнулся", callback_data=f"done:{order.id}")],
    ])


def digest_pages(orders: list[Order], now: datetime) -> list[tuple[str, InlineKeyboardMarkup]]:
    """Сводка за тихие часы: лучшие заказы сверху, номер на кнопке открывает карточку с черновиком."""
    ranked = sorted(orders, key=lambda o: (-o.score, o.pub_date))
    pages = []
    for start in range(0, len(ranked), DIGEST_PAGE):
        chunk = ranked[start:start + DIGEST_PAGE]
        lines = [f"🌅 <b>Заказы за ночь: {len(ranked)}</b>"] if start == 0 else ["🌅 <b>Заказы за ночь, продолжение</b>"]
        rows: list[list[InlineKeyboardButton]] = []
        for number, order in enumerate(chunk, start=start + 1):
            title = order.title if len(order.title) <= 70 else order.title[:70].rsplit(" ", 1)[0] + "…"
            lines.append(f'{number}. {order.score}★ <a href="{escape(order.url)}">{_e(title)}</a> · '
                         f"{budget_text(order)} · {ago(order.pub_date, now)} · {order.category}")
            button = InlineKeyboardButton(text=str(number), callback_data=f"open:{order.id}")
            if rows and len(rows[-1]) < 5:
                rows[-1].append(button)
            else:
                rows.append([button])
        lines.append("\nНажмите номер, чтобы получить карточку с черновиком.")
        pages.append(("\n".join(lines), InlineKeyboardMarkup(inline_keyboard=rows)))
    return pages
