from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Подкатегория «Разработка Чат-ботов» входит в category=5, отдельной ленты ей не нужно:
# category=5 хранит заказы примерно за 9 дней, опрос раз в 3 минуты ничего не пропустит.
DEFAULT_FEEDS = [
    "https://www.fl.ru/rss/all.xml",  # вся биржа
    "https://www.fl.ru/rss/all.xml?category=5",  # Программирование
    "https://www.fl.ru/rss/all.xml?subcategory=37&category=5",  # Программирование / Веб-программирование
    "https://www.fl.ru/rss/all.xml?category=34",  # Мессенджеры: боты для мессенджеров
    "https://www.fl.ru/rss/all.xml?category=41",  # Автоматизация бизнеса: боты, amoCRM, API
    "https://www.fl.ru/rss/all.xml?category=31",  # AI: боты с AI, AI-агенты
]

USER_AGENT = "fl-monitor/1.0 (+https://github.com/a-kamalov781/fl-monitor; contact: t.me/kamalovTG)"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_id: int

    db_path: str = "data/monitor.db"
    tz: str = "Europe/Moscow"

    # NoDecode: в .env адреса через запятую, а не JSON
    feed_urls: Annotated[list[str], NoDecode] = DEFAULT_FEEDS
    poll_seconds: int = 180
    feed_pause_seconds: float = 2
    user_agent: str = USER_AGENT

    min_budget: int = 3000
    quiet_start_hour: int = 1
    quiet_end_hour: int = 8
    # Заказ старше этого срока сохраняется в статистику, но не присылается: например, при добавлении новой ленты
    max_age_hours: int = 24
    first_run_send: int = 3

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    @field_validator("feed_urls", mode="before")
    @classmethod
    def _split_urls(cls, v):
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()] or DEFAULT_FEEDS
        return v

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url and self.llm_api_key and self.llm_model)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
