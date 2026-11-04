"""Чтение публичных RSS-лент FL.ru: последовательно, с паузами. Логина и действий на сайте нет."""
from __future__ import annotations

import asyncio
import calendar
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

log = logging.getLogger(__name__)

_ID = re.compile(r"/projects/(\d+)")
# Хвост заголовка в ленте: «Сайт (Бюджет: 1 500  ₽, для всех)»
_TAIL = re.compile(r"\s*\(([^()]*(?:Бюджет|для всех)[^()]*)\)\s*$")
_TAIL_BUDGET = re.compile(r"Бюджет:\s*(\d[\d\s]*?)\s*(?:₽|руб)")
_TEXT_BUDGET = re.compile(
    r"бюджет\D{0,15}?(\d[\d\s]*?)\s*(к|тыс\w*\.?|т\.?\s?р\.?|₽|руб\w*\.?)(?![а-яa-z])", re.I)
_TAG = re.compile(r"<[^>]+>")

DEFAULT_BACKOFF = 900  # секунд, если сервер попросил подождать и не сказал сколько


@dataclass(frozen=True)
class FeedItem:
    id: int
    title: str
    description: str
    url: str
    label: str
    budget: int | None
    for_all: bool
    pub_date: datetime


def _digits(text: str) -> int:
    return int(re.sub(r"\D", "", text))


def parse_title(raw: str) -> tuple[str, int | None, bool]:
    """Заголовок без служебного хвоста, бюджет в рублях, можно ли откликнуться без PRO."""
    raw = re.sub(r"\s+", " ", html.unescape(raw)).strip()
    tail = _TAIL.search(raw)
    if not tail:
        return raw, None, False
    budget = _TAIL_BUDGET.search(tail.group(1))
    return raw[:tail.start()].strip(), _digits(budget.group(1)) if budget else None, "для всех" in tail.group(1)


def budget_from_text(text: str) -> int | None:
    """Запасной путь: в заголовке бюджета нет, а в описании написано «бюджет 30к»."""
    m = _TEXT_BUDGET.search(text)
    if not m:
        return None
    value = _digits(m.group(1))
    return value * 1000 if m.group(2).lower().startswith(("к", "т")) else value


def clean_text(raw: str) -> str:
    text = _TAG.sub(" ", html.unescape(raw or ""))
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def parse_feed(content: bytes) -> list[FeedItem]:
    items = []
    for e in feedparser.parse(content).entries:
        match = _ID.search(e.get("link", ""))
        published = e.get("published_parsed")
        if not match or not published:
            continue
        title, budget, for_all = parse_title(e.get("title", ""))
        description = clean_text(e.get("summary", ""))
        tags = e.get("tags") or [{}]
        items.append(FeedItem(
            id=int(match.group(1)),
            title=title,
            description=description,
            url=e.link,
            label=html.unescape(tags[0].get("term") or ""),
            budget=budget if budget is not None else budget_from_text(description),
            for_all=for_all,
            pub_date=datetime.fromtimestamp(calendar.timegm(published), timezone.utc),
        ))
    return items


@dataclass
class FetchResult:
    items: list[FeedItem] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def nothing_works(self) -> bool:
        return not self.items and bool(self.failed)


class FeedClient:
    def __init__(self, urls: list[str], user_agent: str, pause: float, *, transport=None, sleep=asyncio.sleep,
                 now=lambda: datetime.now(timezone.utc)):
        self.urls, self.pause, self.sleep, self.now = urls, pause, sleep, now
        self.blocked_until: datetime | None = None
        self.http = httpx.AsyncClient(headers={"User-Agent": user_agent}, timeout=25, follow_redirects=True,
                                      transport=transport)

    async def close(self) -> None:
        await self.http.aclose()

    def _back_off(self, response: httpx.Response) -> None:
        header = response.headers.get("Retry-After", "")
        seconds = int(header) if header.isdigit() else DEFAULT_BACKOFF
        self.blocked_until = self.now() + timedelta(seconds=seconds)
        log.warning("feed answered %s, next request in %s s", response.status_code, seconds)

    async def fetch_all(self) -> FetchResult:
        """Строго по одной ленте за раз. На 429 и 503 останавливаемся и ждём."""
        result = FetchResult()
        if self.blocked_until and self.now() < self.blocked_until:
            result.failed = list(self.urls)
            return result
        for index, url in enumerate(self.urls):
            if index:
                await self.sleep(self.pause)
            try:
                response = await self.http.get(url)
            except httpx.HTTPError as e:
                log.warning("feed %s failed: %s", url, type(e).__name__)
                result.failed.append(url)
                continue
            if response.status_code in (429, 503):
                self._back_off(response)
                result.failed.extend(self.urls[index:])
                break
            items = parse_feed(response.content) if response.status_code == 200 else []
            if not items:
                log.warning("feed %s: status %s, no items", url, response.status_code)
                result.failed.append(url)
                continue
            result.items.extend(items)
        return result
