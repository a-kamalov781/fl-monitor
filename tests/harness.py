"""Прогон без сети: RSS отдаёт httpx.MockTransport, запросы к Telegram складываются в список."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from itertools import count

import httpx
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendDocument, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from bot.config import Settings
from bot.db import Database
from bot.main import bot_defaults, build_dispatcher
from bot.services.feed import FeedClient
from bot.services.monitor import Monitor

OWNER = User(id=777, is_bot=False, first_name="Владелец")
STRANGER = User(id=100, is_bot=False, first_name="Чужой")
# 12:00 МСК, вне тихих часов
NOON = datetime(2026, 11, 4, 9, 0, tzinfo=timezone.utc)
FEED_URL = "https://www.fl.ru/rss/all.xml"

_ids = count(1)


def rss_item(order_id: int, title: str, description: str = "", label: str = "Программирование / Python",
             pub: datetime = NOON) -> str:
    return f"""<item>
      <title><![CDATA[{title}]]></title>
      <link>https://www.fl.ru/projects/{order_id}/zakaz.html</link>
      <description><![CDATA[{description}]]></description>
      <guid>https://www.fl.ru/projects/{order_id}/zakaz.html</guid>
      <category><![CDATA[{label}]]></category>
      <pubDate>{format_datetime(pub, usegmt=True)}</pubDate>
    </item>"""


def rss(*items: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Заказы на FL.ru</title><link>https://www.fl.ru</link>
{''.join(items)}
</channel></rss>""".encode("utf-8")


class FakeSession(BaseSession):
    """Настоящий Bot и настоящие методы aiogram, но до сети дело не доходит."""

    def __init__(self):
        super().__init__()
        self.calls: list = []
        self.fail_with: Exception | None = None
        self.fail_on = None

    async def close(self) -> None:
        pass

    async def stream_content(self, *a, **kw):  # pragma: no cover
        raise NotImplementedError

    async def make_request(self, bot, method, timeout=None):
        if self.fail_with and (self.fail_on is None or self.fail_on(method)):
            raise self.fail_with
        self.calls.append(method)
        if isinstance(method, (SendMessage, SendDocument)):
            return Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=method.chat_id, type="private"),
                           text=getattr(method, "text", None))
        return True

    def of(self, kind) -> list:
        return [c for c in self.calls if isinstance(c, kind)]

    @property
    def messages(self) -> list[SendMessage]:
        return self.of(SendMessage)


class Clock:
    def __init__(self, moment: datetime = NOON):
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def shift(self, **kw) -> None:
        self.moment += timedelta(**kw)


class Harness:
    def __init__(self, tmp_path, **overrides):
        values = dict(bot_token="42:TEST", admin_id=OWNER.id, feed_urls=[FEED_URL], db_path=str(tmp_path / "t.db"),
                      llm_base_url="", llm_api_key="", llm_model="")
        values.update(overrides)
        self.settings = Settings(_env_file=None, **values)
        self.db = Database(self.settings.db_path)
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session, default=bot_defaults())
        self.clock = Clock()
        self.feed_body = rss()
        self.feed_status = 200
        self.feed_headers: dict[str, str] = {}
        self.requests: list[httpx.Request] = []
        self.slept: list[float] = []

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return httpx.Response(self.feed_status, content=self.feed_body, headers=self.feed_headers)

        async def sleep(seconds: float) -> None:
            self.slept.append(seconds)

        self.feeds = FeedClient(self.settings.feed_urls, self.settings.user_agent, self.settings.feed_pause_seconds,
                                transport=httpx.MockTransport(handle), sleep=sleep, now=self.clock)
        self.monitor = Monitor(self.bot, self.db, self.settings, self.feeds, sleep=sleep, now=self.clock)
        self.dp = build_dispatcher(self.settings, self.db, self.monitor)

    async def tick(self, *items: str) -> None:
        self.feed_body = rss(*items)
        await self.monitor.tick()

    async def start(self, *items: str) -> None:
        """Первый запуск с заказом-затравкой: дальше бот работает в обычном режиме."""
        await self.db.init()
        await self.tick(rss_item(1, "Старый заказ", pub=NOON - timedelta(days=30)), *items)
        self.session.calls.clear()

    async def say(self, text: str, user: User = OWNER) -> None:
        m = Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=user.id, type="private"),
                    from_user=user, text=text)
        await self.dp.feed_update(self.bot, Update(update_id=next(_ids), message=m))

    async def press(self, data: str, user: User = OWNER) -> None:
        m = Message(message_id=next(_ids), date=datetime.now(), chat=Chat(id=user.id, type="private"),
                    from_user=user, text="x")
        q = CallbackQuery(id=str(next(_ids)), from_user=user, chat_instance="ci", message=m, data=data)
        await self.dp.feed_update(self.bot, Update(update_id=next(_ids), callback_query=q))


async def make(tmp_path, **overrides) -> Harness:
    h = Harness(tmp_path, **overrides)
    await h.db.init()
    return h
