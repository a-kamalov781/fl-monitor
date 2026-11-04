from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from bot.config import DEFAULT_FEEDS, USER_AGENT, Settings
from bot.services.feed import FeedClient, budget_from_text, clean_text, parse_feed, parse_title
from tests.harness import NOON, rss, rss_item

# Формат скопирован из настоящей ленты: пробелы в бюджете, знак рубля кодом внутри CDATA
REAL_TITLE = "Записать гимплей мк11 (Бюджет: 1 500  &#8381;)"


@pytest.mark.parametrize("raw, title, budget, for_all", [
    (REAL_TITLE, "Записать гимплей мк11", 1500, False),
    ("Верстка (Бюджет: 40 000  &#8381;)", "Верстка", 40000, False),
    ("Проект (Бюджет: 150 000  &#8381;, для всех)", "Проект", 150000, True),
    ("монтаж (для всех)", "монтаж", None, True),
    ("Разработка приложения", "Разработка приложения", None, False),
    ("Настройка (срочно) бота", "Настройка (срочно) бота", None, False),
    ("  Два   пробела  ", "Два пробела", None, False),
])
def test_title_parts(raw, title, budget, for_all):
    assert parse_title(raw) == (title, budget, for_all)


@pytest.mark.parametrize("text, expected", [
    ("Бюджет: 30000 руб", 30000),
    ("бюджет 30к", 30000),
    ("Бюджет до 15 тыс.", 15000),
    ("Бюджет — 50 000 ₽", 50000),
    ("Бюджет: по договоренности", None),
    ("Бюджет обсудим", None),
    ("Сделать 5 страниц", None),
])
def test_budget_in_description(text, expected):
    assert budget_from_text(text) == expected


def test_clean_text_removes_tags_and_extra_space():
    assert clean_text("Строка  <b>один</b>\n\n  две&nbsp;&amp;\n") == "Строка один\nдве &"


def test_parse_feed_reads_all_fields():
    pub = datetime(2026, 11, 4, 16, 4, 52, tzinfo=timezone.utc)
    item = parse_feed(rss(rss_item(5528023, "Бот (Бюджет: 20 000  &#8381;, для всех)", "Нужен бот\nна Python",
                                   "Сайты / Сайт-каталог", pub)))[0]
    assert item.id == 5528023
    assert item.title == "Бот" and item.budget == 20000 and item.for_all is True
    assert item.description == "Нужен бот\nна Python"
    assert item.url == "https://www.fl.ru/projects/5528023/zakaz.html"
    assert item.label == "Сайты / Сайт-каталог"
    assert item.pub_date == pub


def test_budget_from_description_is_used_when_title_has_none():
    assert parse_feed(rss(rss_item(1, "Бот", "Бюджет 30к")))[0].budget == 30000


def test_item_without_order_link_is_skipped():
    broken = "<item><title>Новость</title><link>https://www.fl.ru/blog/1</link>" \
             "<pubDate>Wed, 04 Nov 2026 16:04:52 GMT</pubDate></item>"
    assert parse_feed(rss(broken, rss_item(2, "Бот"))) != [] and len(parse_feed(rss(broken))) == 0


@pytest.mark.parametrize("body", [b"", b"<html>captcha</html>", b"not xml at all"])
def test_garbage_gives_no_items(body):
    assert parse_feed(body) == []


def client(handler, urls=("https://a.test/1", "https://a.test/2"), now=lambda: NOON, slept=None):
    async def sleep(seconds):
        if slept is not None:
            slept.append(seconds)

    return FeedClient(list(urls), USER_AGENT, 2, transport=httpx.MockTransport(handler), sleep=sleep, now=now)


async def test_feeds_are_read_one_by_one_with_pause_and_contact():
    seen, slept = [], []

    def handle(request):
        seen.append((str(request.url), request.headers["User-Agent"]))
        return httpx.Response(200, content=rss(rss_item(len(seen), "Бот")))

    result = await client(handle, slept=slept).fetch_all()
    assert [s[0] for s in seen] == ["https://a.test/1", "https://a.test/2"]
    assert all("t.me/kamalovTG" in s[1] for s in seen)
    assert slept == [2] and len(result.items) == 2 and result.failed == []


async def test_only_get_requests_to_given_urls():
    """Бот ничего не отправляет на сайт: ни логина, ни откликов."""
    methods = []

    def handle(request):
        methods.append(request.method)
        return httpx.Response(200, content=rss(rss_item(1, "Бот")))

    await client(handle).fetch_all()
    assert set(methods) == {"GET"}


async def test_failed_feed_does_not_stop_the_others():
    def handle(request):
        if request.url.path == "/1":
            raise httpx.ConnectError("no route")
        return httpx.Response(200, content=rss(rss_item(2, "Бот")))

    result = await client(handle).fetch_all()
    assert [i.id for i in result.items] == [2] and result.failed == ["https://a.test/1"]
    assert not result.nothing_works


async def test_error_page_counts_as_failure():
    result = await client(lambda r: httpx.Response(200, content=b"<html>captcha</html>")).fetch_all()
    assert result.items == [] and result.nothing_works


@pytest.mark.parametrize("status", [429, 503])
async def test_rate_limit_stops_reading_and_waits(status):
    calls = []
    clock = {"now": NOON}

    def handle(request):
        calls.append(request.url.path)
        return httpx.Response(status, headers={"Retry-After": "60"})

    feeds = client(handle, now=lambda: clock["now"])
    result = await feeds.fetch_all()
    assert calls == ["/1"] and result.nothing_works

    clock["now"] = NOON + timedelta(seconds=59)
    await feeds.fetch_all()
    assert calls == ["/1"]  # всё ещё ждём

    clock["now"] = NOON + timedelta(seconds=61)
    await feeds.fetch_all()
    assert calls == ["/1", "/1"]


async def test_rate_limit_without_header_waits_long():
    feeds = client(lambda r: httpx.Response(429))
    await feeds.fetch_all()
    assert feeds.blocked_until == NOON + timedelta(seconds=900)


def test_default_settings():
    s = Settings(_env_file=None, bot_token="1:x", admin_id=1, llm_base_url="", llm_api_key="", llm_model="")
    assert s.feed_urls == DEFAULT_FEEDS and s.poll_seconds == 180 and s.llm_enabled is False
    assert len(s.feed_urls) == len(set(s.feed_urls)) and "https://www.fl.ru/rss/all.xml" in s.feed_urls


def test_feed_list_from_env_string():
    s = Settings(_env_file=None, bot_token="1:x", admin_id=1, feed_urls="https://a.test/1, https://a.test/2")
    assert s.feed_urls == ["https://a.test/1", "https://a.test/2"]


def test_llm_needs_all_three_settings():
    base = dict(_env_file=None, bot_token="1:x", admin_id=1)
    assert Settings(**base, llm_base_url="u", llm_api_key="k", llm_model="m").llm_enabled
    assert not Settings(**base, llm_base_url="u", llm_api_key="", llm_model="m").llm_enabled
