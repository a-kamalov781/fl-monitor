from __future__ import annotations

import httpx
import pytest

from bot.services.drafts import (DEMO_LINK, build_draft, build_questions, extract_points, find_systems, format_price,
                                 template_for)
from bot.services.llm import llm_draft
from tests.harness import Harness


def test_points_are_sentences_with_task_verbs():
    text = ("Есть магазин на Tilda. Нужно сделать бота для приёма заказов. Бот должен отправлять уведомления менеджеру. "
            "Работаем давно. Чтобы клиент видел статус заказа в чате.")
    assert extract_points("Бот", text) == [
        "сделать бота для приёма заказов",
        "бот должен отправлять уведомления менеджеру",
        "чтобы клиент видел статус заказа в чате",
    ]


def test_at_most_three_points():
    text = "\n".join(f"Нужно сделать модуль номер {word}" for word in ("альфа", "браво", "чарли", "дельта", "эхо"))
    assert len(extract_points("Заказ", text)) == 3


def test_filler_at_the_start_is_removed():
    assert extract_points("", "Нам также нужно добавить оплату через ЮKassa") == ["добавить оплату через ЮKassa"]
    assert extract_points("", "Необходимо, что бы заявки падали с сайтов") == ["что бы заявки падали с сайтов"]


def test_list_markers_and_emoji_are_removed():
    assert extract_points("", "🚀 Требуется: разработать сервис заявок") == ["разработать сервис заявок"]
    assert extract_points("", "1. Нужно собрать каталог товаров") == ["собрать каталог товаров"]
    assert extract_points("", "— сделать выгрузку отчётов") == ["сделать выгрузку отчётов"]


def test_abbreviation_keeps_its_case():
    assert extract_points("", "Нужно CRM подключить к сайту") == ["CRM подключить к сайту"]


def test_cut_rss_text_does_not_end_with_half_a_word():
    points = extract_points("", "Нужно настроить обмен заказами и остатками между сервисами на платфор...")
    assert points == ["настроить обмен заказами и остатками между сервисами на"]


def test_long_sentence_is_shortened_on_a_word_boundary():
    point = extract_points("", "Нужно " + "очень длинное описание задачи " * 10)[0]
    assert len(point) <= 101 and point.endswith("…") and "  " not in point


def test_service_phrases_are_not_points():
    text = ("Нужен программист на Python. В отклике нужно написать про кейсы. Ставить ничего не нужно. "
            "Нужно добавить оплату в бота")
    assert extract_points("", text) == ["добавить оплату в бота"]


def test_without_task_verbs_first_sentence_and_title_are_used():
    points = extract_points("Сбор информации", "Спарсить данные по двум спискам с сайта, объём 500 тысяч записей")
    assert points == ["спарсить данные по двум спискам с сайта, объём 500 тысяч записей", "сбор информации"]


def test_title_is_not_added_when_it_repeats_the_description():
    points = extract_points("Переделать бота", "Нужно немного доработать существующего бота")
    assert points == ["немного доработать существующего бота"]


def test_title_only_when_description_is_empty():
    assert extract_points("Настройка Битрикс.24", "") == ["настройка Битрикс.24"]


def test_no_term_asks_about_term():
    assert "срок" in build_questions("Бот для записи", "сотни заявок", "booking")[0]


@pytest.mark.parametrize("text", ["Срок 3 дня", "Нужно до 15 ноября", "Сделать за 5 дней", "Срочно!", "К 20.11", "на неделю"])
def test_term_is_recognised(text):
    questions = build_questions("Бот", text, "booking")
    assert not any("срок" in q for q in questions)


def test_integration_without_crm_asks_about_crm_first():
    assert build_questions("Связать сайт и таблицу", "", "integration")[0] == "какая у вас CRM и есть ли доступ к API?"


def test_integration_with_named_crm_does_not_ask_about_crm():
    questions = build_questions("Интеграция amoCRM и сайта", "", "integration")
    assert not any("CRM" in q for q in questions)
    assert "срок" in questions[0]


def test_crm_mentioned_without_name_asks_which_one():
    assert "какая у вас CRM" in build_questions("Бот для записи", "Заявки в CRM. Срок неделя.", "booking")[0]


def test_no_volume_asks_about_volume():
    questions = build_questions("Бот", "Срок 3 дня", "booking")
    assert questions == ["сколько примерно пользователей и заявок в день?"]


def test_volume_is_recognised():
    questions = build_questions("Бот", "Срок 3 дня, около 500 пользователей", "booking")
    assert not any("пользователей" in q for q in questions)


def test_parser_volume_question_is_about_pages():
    assert "страниц" in build_questions("Парсер", "Срок 3 дня", "parser")[0]


def test_always_at_least_one_question():
    assert len(build_questions("Бот", "Срок 3 дня, 500 пользователей", "booking")) == 1
    assert build_questions("Ассистент", "Срок 3 дня, 500 пользователей", "ai")[0].startswith("на каких материалах")


def test_systems_are_found_in_text():
    assert find_systems("Связка АМО СРМ, гугл таблиц и телеграм") == ["amoCRM", "Google Таблицы", "Telegram"]
    assert find_systems("Покрасить забор") == []


def test_booking_template():
    draft = build_draft("Бот для записи", "Нужно сделать бота для записи на стрижку", "booking", None)
    assert draft.startswith("Здравствуйте! Делал почти такого же бота: заявка → сохраняется в базе → вам уведомление")
    assert DEMO_LINK in draft
    assert "Под вашу задачу: сделать бота для записи на стрижку." in draft
    assert "Срок — 3 дня, цена — 14 900 ₽ фикс." in draft
    assert "Отдаю исходный код, инструкцию по запуску и тесты." in draft
    assert draft.endswith("Вопрос: какой срок для вас комфортный, есть ли дедлайн?")


def test_integration_template_names_systems_from_text():
    draft = build_draft("Интеграция Битрикс и Telegram", "Нужно передавать заявки в чат", "integration", None)
    assert draft.startswith("Здравствуйте! Сделаю связку Битрикс24 + Telegram: передавать заявки в чат")
    assert "через вебхуки/API, с логами и уведомлением об ошибках" in draft
    assert "Срок — 2 дня, 7 900 ₽ фикс." in draft
    assert "Чтобы оценить точно: " in draft


def test_integration_template_without_named_systems():
    assert "связку нужных вам систем:" in build_draft("Связать сервисы", "", "integration", None)


def test_ai_template():
    draft = build_draft("ИИ ассистент", "Нужно отвечать клиентам по базе знаний", "ai", None)
    assert "Сделаю AI-ассистента" in draft and "передаёт сложные вопросы человеку" in draft
    assert f"через бота: {DEMO_LINK}" in draft
    assert "Срок 5 дней, 19 900 ₽." in draft


@pytest.mark.parametrize("category, price, term", [("parser", "9 900", 2), ("other", "9 900", 3)])
def test_universal_template(category, price, term):
    draft = build_draft("Заказ на обработку каталога", "", category, None)
    assert f"Срок — {term} дня, {price} ₽ фикс." in draft
    assert DEMO_LINK not in draft


@pytest.mark.parametrize("category", ["booking", "integration", "ai", "parser", "other"])
def test_budget_replaces_price(category):
    assert "45 000 ₽" in build_draft("Заказ", "", category, 45_000)


def test_format_price():
    assert format_price(14900) == "14 900"
    assert format_price(1500000) == "1 500 000"


def test_other_variant_changes_template_and_comes_back():
    assert [template_for("integration", i) for i in range(5)] == [
        "integration", "booking", "ai", "universal", "integration"]
    assert template_for("parser", 0) == "universal"
    texts = {build_draft("Бот", "Нужно собрать заявки", "booking", None, variant) for variant in range(4)}
    assert len(texts) == 4


def test_other_variant_changes_question():
    first = build_draft("Бот для записи", "", "booking", None, 0)
    fifth = build_draft("Бот для записи", "", "booking", None, 1)
    assert "какой срок" in first and "сколько примерно" in fifth


def test_empty_order_still_gives_a_draft():
    assert "Здравствуйте" in build_draft("Бот", "", "other", None)


def _llm_settings(tmp_path):
    return Harness(tmp_path, llm_base_url="https://llm.test/v1/", llm_api_key="k", llm_model="m").settings


async def test_llm_gets_system_prompt_and_order(tmp_path):
    seen = {}

    def handle(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["Authorization"], request.read().decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": "  Понял задачу.\nДемо\nВопрос?  "}}]})

    text = await llm_draft(_llm_settings(tmp_path), "Бот", "Описание", "booking", None, transport=httpx.MockTransport(handle))
    assert text == "Понял задачу.\nДемо\nВопрос?"
    assert seen["url"] == "https://llm.test/v1/chat/completions" and seen["auth"] == "Bearer k"
    assert DEMO_LINK in seen["body"] and "14 900" in seen["body"]


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, json={"choices": []}),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"choices": [{"message": {"content": "x" * 601}}]}),
    httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]}),
])
async def test_bad_llm_answer_gives_none(tmp_path, response):
    transport = httpx.MockTransport(lambda request: response)
    assert await llm_draft(_llm_settings(tmp_path), "Бот", "", "booking", None, transport=transport) is None


async def test_llm_network_error_gives_none(tmp_path):
    def handle(request):
        raise httpx.ConnectError("no route")

    assert await llm_draft(_llm_settings(tmp_path), "Бот", "", "booking", None, transport=httpx.MockTransport(handle)) is None
