from __future__ import annotations

import pytest

from bot.services.filters import check, classify, find_keywords, rate


@pytest.mark.parametrize("text, keyword", [
    ("Нужен телеграм-бот", "telegram"),
    ("Бот в Телеграмме", "telegram"),
    ("TELEGRAM канал", "telegram"),
    ("Интеграция Bitrix и Telegramm", "telegram"),
    ("Сделать ботов для магазина", "бот"),
    ("Настройка чат-бота", "бот"),
    ("Нужен чатбот", "бот"),
    ("telegram bot на python", "бот"),
    ("Бот на aiogram 3", "aiogram"),
    ("Интеграции с сервисами", "интеграция"),
    ("Нужно интегрировать сайт", "интеграция"),
    ("Настроить вебхуки", "webhook"),
    ("Настроить webhook", "webhook"),
    ("Выгрузка в Google Таблицы", "google таблицы"),
    ("Выгрузка в гугл-таблицу", "google таблицы"),
    ("Google Sheets отчёт", "google таблицы"),
    ("Доработка amoCRM", "amocrm"),
    ("Доработка АМО СРМ", "amocrm"),
    ("Настроить Битрикс24", "битрикс"),
    ("Настройка Bitrix24", "битрикс"),
    ("Запись через YCLIENTS", "yclients"),
    ("Сценарий в n8n", "n8n"),
    ("Сценарий в Make", "make"),
    ("Настроить make.com", "make"),
    ("Связка через Zapier", "zapier"),
    ("Нужен парсер сайта", "парсер"),
    ("Парсинг товаров", "парсер"),
    ("Автоматизация отчётов", "автоматизация"),
    ("Автоматизировать рассылку счетов", "автоматизация"),
    ("Уведомления о заказах", "уведомления"),
    ("Настроить CRM", "crm"),
    ("Настроить срм для отдела", "crm"),
    ("Telegram Mini App для кафе", "mini app"),
    ("Мини-приложение в ВК", "mini app"),
    ("AI-агент для поддержки", "ai"),
    ("Добавить ИИ в сервис", "ai"),
    ("Интеграция ChatGPT", "gpt"),
    ("Подключить GPT-4", "gpt"),
    ("Голосовой ассистент на сайте", "ассистент"),
])
def test_keyword_is_found(text, keyword):
    assert keyword in find_keywords(text)


@pytest.mark.parametrize("text", [
    "Ищу работу в офисе",  # «бот» внутри слова
    "Ботинки на заказ",
    "Ботаника для школьников",
    "Линии электропередач",  # «ии» внутри слова
    "Сделать макияж",
    "Make up artist",
    "Make-up на съёмку",
    "Makeup для фото",
    "Заполнить таблицу в Excel",
    "Личный ассистент",
    "Ассистент руководителя",
    "Ищу ассистента с опытом",
    "Daily отчёт",  # «ai» внутри слова
])
def test_keyword_is_not_found_in_lookalikes(text):
    assert find_keywords(text) == []


@pytest.mark.parametrize("text", [
    "Телеграм-бот за долю в проекте",
    "Сделать бота бесплатно",
    "Бот и ПРОДВИЖЕНИЕ канала",
    "План продвижения для бота",
    "Бот для накрутки подписчиков",
    "Бот, накрутка просмотров",
    "Бот: рассылка спама по чатам",
    "Бот с реферальной системой",
    "Рефералы для бота",
    "Вакансия в штат: разработчик ботов",
])
def test_minus_word_rejects(text):
    verdict = check(text, "", "", 50_000)
    assert not verdict.matched and verdict.reason.startswith("минус-слово")


def test_minus_word_is_found_in_description():
    assert not check("Телеграм-бот", "Оплата за долю в проекте", "", None).matched


def test_minus_word_does_not_look_at_section_name():
    """Раздел «Продвижение сайтов» — название рубрики, а не текст заказа."""
    assert check("Телеграм-бот", "", "Продвижение сайтов (SEO, GEO) / Прочее", None).matched


def test_no_keywords_rejects():
    verdict = check("Логотип для кафе", "Нарисовать логотип", "Дизайн / Логотипы", 5000)
    assert not verdict.matched and verdict.reason == "нет ключевых слов"


def test_keyword_is_found_in_description():
    assert check("Доработка сервиса", "Нужно добавить уведомления менеджеру", "", None).matched


def test_section_name_counts_when_text_is_cut():
    assert check("Переделать сценарий", "Детали в ЛС", "Программирование / Разработка Чат-ботов", None).matched


@pytest.mark.parametrize("title, description", [
    ("Создать ии видео", "Сделать ии видео на мою строительную сферу"),
    ("Подготовить презентацию", "Тема «От хаоса в данных до AI-powered команды»"),
    ("Нужен специалист по генерации ИИ фото", ""),
    ("Обучение ИИ для команды", ""),
])
def test_ai_alone_is_not_enough(title, description):
    verdict = check(title, description, "AI — искусственный интеллект / Генерация видео", 50_000)
    assert not verdict.matched and verdict.reason == "ai без контекста"


@pytest.mark.parametrize("title, description", [
    ("ИИ для Telegram", ""),  # другое ключевое слово
    ("Бот с ИИ", ""),
    ("ИИ ассистент", ""),
    ("Интеграция ИИ в сервис", ""),
    ("Разработка ИИ-агента", ""),
    ("Сервис", "Нужны AI-агенты для отдела продаж"),
    ("ИИ для CRM", ""),
    ("Подключить ИИ", "через автоматизацию отчётов"),
])
def test_ai_with_context_passes(title, description):
    assert check(title, description, "", None).matched


@pytest.mark.parametrize("section", ["AI-агенты", "Боты с AI"])
def test_ai_in_our_section_passes(section):
    assert check("Сделать ИИ", "", f"AI — искусственный интеллект / {section}", None).matched


def test_travel_agency_is_not_ai_context():
    assert not check("Реклама ИИ для агентства", "", "", None).matched


def test_gpt_keyword_is_not_narrowed():
    assert check("Настроить ChatGPT", "", "", None).matched


def test_general_section_name_is_not_enough():
    assert not check("Отчёт в 1С", "", "Автоматизация бизнеса / 1С бухгалтерия", None).matched


@pytest.mark.parametrize("budget, matched", [(2999, False), (1500, False), (3000, True), (150_000, True)])
def test_budget_threshold(budget, matched):
    verdict = check("Телеграм-бот", "", "", budget)
    assert verdict.matched is matched
    if not matched:
        assert "бюджет" in verdict.reason


def test_negotiable_budget_is_not_rejected():
    assert check("Телеграм-бот", "", "", None).matched


@pytest.mark.parametrize("title, description, expected", [
    ("Бот для онлайн-записи клиентов", "", "booking"),
    ("Бот для записи на стрижку", "", "booking"),
    ("Бронирование столиков", "", "booking"),
    ("Бот с интеграцией YCLIENTS", "", "booking"),
    ("Телеграм-бот", "Клиенты оставляют заявки и смотрят статус", "booking"),
    ("Запись на приём с выгрузкой в Google Таблицы", "", "booking"),
    ("AI-ассистент для поддержки", "", "ai"),
    ("Бот с ChatGPT", "отвечает на вопросы", "ai"),
    ("Подключить нейросеть к сайту", "", "ai"),
    ("Интеграция amoCRM и сайта", "", "integration"),
    ("Заявки с сайта в amoCRM", "", "integration"),
    ("Настроить вебхуки", "", "integration"),
    ("Сценарий в n8n", "", "integration"),
    ("Выгрузка в гугл таблицы", "", "integration"),
    ("Парсер цен конкурентов", "", "parser"),
    ("Спарсить каталог", "", "parser"),
    ("Переделать бота", "", "other"),
    ("Настройка чат-бота в MAX", "", "other"),
])
def test_category(title, description, expected):
    assert classify(title, description) == expected


def test_section_name_helps_category():
    assert classify("Сбор информации", "по двум спискам", "Программирование / Парсинг данных") == "parser"


@pytest.mark.parametrize("category, budget, expected", [
    ("booking", 10_000, 5),
    ("integration", 10_000, 5),
    ("booking", 150_000, 5),
    ("integration", None, 4),
    ("booking", 9_999, 3),
    ("ai", 20_000, 4),
    ("ai", None, 3),
    ("ai", 5_000, 2),
    ("parser", 15_000, 3),
    ("parser", None, 3),
    ("parser", 5_000, 2),
    ("other", 20_000, 3),
    ("other", None, 2),
    ("other", 5_000, 1),
])
def test_score(category, budget, expected):
    assert rate(category, budget) == expected


def test_telegram_bot_in_other_is_rated_higher():
    assert rate("other", None, "Переделать бота") == 3


@pytest.mark.parametrize("title, label", [
    ("Создать ИИ видео", ""),
    ("Монтаж серии Ai видеороликов", ""),
    ("Сделать картинки", "AI — искусственный интеллект / Генерация изображений"),
])
def test_ai_media_orders_get_lowest_score(title, label):
    assert rate("ai", 100_000, title, "", label) == 1


def test_score_is_always_within_range():
    for category in ("booking", "integration", "ai", "parser", "other"):
        for budget in (None, 3000, 10_000, 1_000_000):
            assert 1 <= rate(category, budget, "Телеграм-бот", "бот") <= 5
