"""SQLite: все заказы из лент, включая отсеянные. id заказа FL.ru — первичный ключ, он и даёт дедупликацию."""
from __future__ import annotations

from dataclasses import astuple, dataclass, fields
from datetime import datetime
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    url TEXT NOT NULL,
    label TEXT NOT NULL,
    budget INTEGER,
    for_all INTEGER NOT NULL,
    pub_date TEXT NOT NULL,
    found_at TEXT NOT NULL,
    matched INTEGER NOT NULL,
    reject_reason TEXT,
    category TEXT NOT NULL,
    score INTEGER NOT NULL,
    delivery TEXT NOT NULL,
    held INTEGER NOT NULL DEFAULT 0,
    draft TEXT,
    variant INTEGER NOT NULL DEFAULT 0,
    notified_at TEXT,
    hidden_at TEXT,
    responded_at TEXT
);
CREATE INDEX IF NOT EXISTS orders_pub_date ON orders (pub_date);
CREATE INDEX IF NOT EXISTS orders_delivery ON orders (delivery);
"""

# Что случилось с заказом: pending — ждёт отправки, sent — показан, skipped — показывать не нужно, failed — Telegram отказал
PENDING, SENT, SKIPPED, FAILED = "pending", "sent", "skipped", "failed"

_DATES = {"pub_date", "found_at", "notified_at", "hidden_at", "responded_at"}


@dataclass
class Order:
    id: int
    title: str
    description: str
    url: str
    label: str
    budget: int | None
    for_all: bool
    pub_date: datetime
    found_at: datetime
    matched: bool
    reject_reason: str | None
    category: str
    score: int
    delivery: str
    held: bool = False
    draft: str | None = None
    variant: int = 0
    notified_at: datetime | None = None
    hidden_at: datetime | None = None
    responded_at: datetime | None = None


COLUMNS = [f.name for f in fields(Order)]


def _to_row(order: Order) -> tuple:
    return tuple(v.isoformat() if isinstance(v, datetime) else v for v in astuple(order))


def _from_row(row) -> Order:
    values = dict(zip(COLUMNS, row))
    for key in _DATES:
        if values[key]:
            values[key] = datetime.fromisoformat(values[key])
    values["for_all"] = bool(values["for_all"])
    values["matched"] = bool(values["matched"])
    values["held"] = bool(values["held"])
    return Order(**values)


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def _select(self, where: str, args: tuple = ()) -> list[Order]:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(f"SELECT {', '.join(COLUMNS)} FROM orders {where}", args)
            return [_from_row(row) for row in await cursor.fetchall()]

    async def _update(self, order_id: int, **values) -> None:
        sets = ", ".join(f"{key} = ?" for key in values)
        args = [v.isoformat() if isinstance(v, datetime) else v for v in values.values()]
        async with aiosqlite.connect(self.path) as db:
            await db.execute(f"UPDATE orders SET {sets} WHERE id = ?", (*args, order_id))
            await db.commit()

    async def is_empty(self) -> bool:
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute("SELECT 1 FROM orders LIMIT 1")
            return await cursor.fetchone() is None

    async def known_ids(self, ids: list[int]) -> set[int]:
        known: set[int] = set()
        async with aiosqlite.connect(self.path) as db:
            for start in range(0, len(ids), 500):  # у SQLite есть предел числа параметров
                part = ids[start:start + 500]
                marks = ",".join("?" * len(part))
                cursor = await db.execute(f"SELECT id FROM orders WHERE id IN ({marks})", part)
                known.update(row[0] for row in await cursor.fetchall())
        return known

    async def insert_many(self, orders: list[Order]) -> None:
        """Уже известный id не перезаписывается: реакция и черновик остаются на месте."""
        marks = ", ".join("?" * len(COLUMNS))
        async with aiosqlite.connect(self.path) as db:
            await db.executemany(f"INSERT OR IGNORE INTO orders ({', '.join(COLUMNS)}) VALUES ({marks})",
                                 [_to_row(o) for o in orders])
            await db.commit()

    async def get(self, order_id: int) -> Order | None:
        rows = await self._select("WHERE id = ?", (order_id,))
        return rows[0] if rows else None

    async def pending(self) -> list[Order]:
        return await self._select("WHERE delivery = ? ORDER BY pub_date, id", (PENDING,))

    async def since(self, moment: datetime) -> list[Order]:
        return await self._select("WHERE pub_date >= ? ORDER BY pub_date", (moment.isoformat(),))

    async def all_orders(self) -> list[Order]:
        return await self._select("ORDER BY pub_date, id")

    async def mark_sent(self, order_id: int, moment: datetime) -> None:
        await self._update(order_id, delivery=SENT, notified_at=moment)

    async def mark_failed(self, order_id: int) -> None:
        await self._update(order_id, delivery=FAILED)

    async def save_draft(self, order_id: int, draft: str, variant: int) -> None:
        await self._update(order_id, draft=draft, variant=variant)

    async def mark_hidden(self, order_id: int, moment: datetime) -> None:
        await self._update(order_id, hidden_at=moment)

    async def mark_responded(self, order_id: int, moment: datetime) -> None:
        await self._update(order_id, responded_at=moment)
