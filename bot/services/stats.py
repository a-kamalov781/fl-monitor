"""/stats, /top и /export: что заказывают на самом деле и как быстро мы на это отвечаем."""
from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from html import escape

from bot.db import Order
from bot.services.cards import duration
from bot.services.drafts import format_price
from bot.services.filters import CATEGORIES, normalize

TOP_SIZE = 20

_WORD = re.compile(r"[a-zа-я0-9][a-zа-я0-9+#.-]*[a-zа-я0-9+#]|[a-zа-я0-9]")
_STOP = frozenset("""
а без более бы в во вам вас все всех для до его ее если есть же за и из или им их к как ко кто ли либо мне мой мы на над
наш не нет ни но о об от по под при про с со так то тот у уже что чтобы это я the a an and for of on to in with
""".split())
_ENDINGS = ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ах", "ях", "ов", "ев", "ом", "ем", "ой", "ей",
            "ую", "юю", "ая", "яя", "ое", "ее", "ые", "ие", "ый", "ий", "а", "я", "у", "ю", "ы", "и", "е", "о", "ь")


def _stem(word: str) -> str:
    """Грубо, но достаточно, чтобы «телеграм бота» и «телеграм бот» считались одной формулировкой."""
    if not re.fullmatch(r"[а-я]+", word) or len(word) < 4:
        return word
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[:-len(ending)]
    return word


def top_bigrams(titles: list[str], size: int = TOP_SIZE) -> list[tuple[str, int]]:
    counts: Counter[tuple[str, str]] = Counter()
    forms: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for title in titles:
        words = [w for w in _WORD.findall(normalize(title)) if w not in _STOP]
        # В одном заголовке формулировка считается один раз
        pairs = {}
        for first, second in zip(words, words[1:]):
            pairs[(_stem(first), _stem(second))] = f"{first} {second}"
        for key, form in pairs.items():
            counts[key] += 1
            forms[key][form] += 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], forms[kv[0]].most_common(1)[0][0]))
    return [(forms[key].most_common(1)[0][0], count) for key, count in ranked[:size] if count > 1]


def _average_budget(orders: list[Order]) -> str:
    budgets = [o.budget for o in orders if o.budget is not None]
    return f"{format_price(round(sum(budgets) / len(budgets)))} ₽" if budgets else "—"


def _average_reaction(orders: list[Order]) -> str:
    minutes = [(o.responded_at - o.notified_at).total_seconds() / 60
               for o in orders if o.responded_at and o.notified_at]
    return duration(sum(minutes) / len(minutes)) if minutes else "—"


def stats_text(week: list[Order], now: datetime) -> str:
    """Заказы считаются по дате публикации, поэтому заказы из лент первого запуска тоже входят."""
    border = now - timedelta(hours=24)
    found_week = [o for o in week if o.matched]
    found_day = [o for o in found_week if o.pub_date >= border]
    day_by_cat, week_by_cat = Counter(o.category for o in found_day), Counter(o.category for o in found_week)
    responded_day = sum(1 for o in found_day if o.responded_at)
    responded_week = sum(1 for o in found_week if o.responded_at)
    lines = [
        "📊 <b>Заказы, прошедшие фильтр</b>  (за 24 ч / за 7 дней)",
        f"Найдено: <b>{len(found_day)}</b> / <b>{len(found_week)}</b>",
        "",
        *[f"{category}: {day_by_cat[category]} / {week_by_cat[category]}" for category in CATEGORIES],
        "",
        f"Средний бюджет: {_average_budget(found_day)} / {_average_budget(found_week)}",
        f"Откликнулся: {responded_day} / {responded_week}",
        f"Среднее время реакции: {_average_reaction(found_day)} / {_average_reaction(found_week)}",
        "",
        f"Всего в лентах за неделю: {len(week)}, отсеяно: {len(week) - len(found_week)}",
    ]
    return "\n".join(lines)


def top_text(week: list[Order]) -> str:
    bigrams = top_bigrams([o.title for o in week if o.matched])
    if not bigrams:
        return "Пока мало данных: формулировки появятся, когда заголовки начнут повторяться."
    lines = ["🔝 <b>Что заказывают: формулировки за неделю</b>"]
    lines += [f"{i}. {escape(text, quote=False)} — {count}" for i, (text, count) in enumerate(bigrams, start=1)]
    return "\n".join(lines)


CSV_FIELDS = ("id", "pub_date", "title", "category", "score", "budget", "for_all", "matched", "reject_reason", "label",
              "url", "description", "delivery", "notified_at", "hidden_at", "responded_at")


def _safe_cell(value) -> str:
    """Текст заказа пишет посторонний человек: формула в ячейке не должна выполниться в Excel."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def export_csv(orders: list[Order]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_FIELDS)
    for order in orders:
        writer.writerow([_safe_cell(getattr(order, name)) for name in CSV_FIELDS])
    return out.getvalue().encode("utf-8-sig")  # BOM: без него Excel показывает кириллицу кракозябрами
