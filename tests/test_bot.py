"""Сообщение в Telegram, кнопки, команды."""
from __future__ import annotations

import csv
import io
from datetime import timedelta

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, DeleteMessage, EditMessageText, SendDocument, SendMessage

from bot.db import Order
from bot.services.cards import ago, card_keyboard, card_text, duration, snippet
from bot.services.stats import export_csv, top_bigrams
from tests.harness import NOON, STRANGER, make, rss_item

BOT = "Телеграм-бот для записи (Бюджет: 20 000  &#8381;)"


def order(**kw) -> Order:
    values = dict(id=5, title="Бот для записи", description="Нужен бот\nдля записи\nна стрижку\nчетвёртая строка",
                  url="https://www.fl.ru/projects/5/x.html", label="", budget=20000, for_all=False,
                  pub_date=NOON - timedelta(minutes=12), found_at=NOON, matched=True, reject_reason=None,
                  category="booking", score=5, delivery="sent", draft="Здравствуйте!")
    values.update(kw)
    return Order(**values)


def buttons(markup) -> list:
    return [b for row in markup.inline_keyboard for b in row]


def test_card_first_line_has_all_parts_in_order():
    first = card_text(order(), NOON).splitlines()[0]
    assert first == "🆕 5★ <b>Бот для записи</b> · 20 000 ₽ · 12 мин назад · booking"


def test_card_marks_negotiable_budget():
    assert "· 💬 по договорённости ·" in card_text(order(budget=None), NOON)


def test_card_marks_orders_open_without_pro():
    assert "· 20 000 ₽ · для всех · 12 мин" in card_text(order(for_all=True), NOON)


def test_card_shows_two_or_three_lines_of_description():
    text = card_text(order(), NOON)
    assert "Нужен бот\nдля записи\nна стрижку" in text and "четвёртая" not in text


def test_long_description_is_cut():
    assert snippet("слово " * 200).endswith("…") and len(snippet("слово " * 200)) <= 321


def test_empty_description_leaves_no_gap():
    assert "\n\n\n" not in card_text(order(description=""), NOON)


def test_draft_is_monospace_for_copying_by_tap():
    assert card_text(order(), NOON).endswith("<code>Здравствуйте!</code>")


def test_card_escapes_text_from_the_feed():
    text = card_text(order(title="<b>x</b> & y", description="a <script> b", draft="c < d"), NOON)
    assert "<b>&lt;b&gt;x&lt;/b&gt; &amp; y</b>" in text
    assert "a &lt;script&gt; b" in text and "<code>c &lt; d</code>" in text


def test_card_buttons():
    keyboard = buttons(card_keyboard(order()))
    assert [b.text for b in keyboard] == ["🔗 Открыть заказ", "✏️ Другой вариант", "🙈 Скрыть", "✅ Откликнулся"]
    assert keyboard[0].url == "https://www.fl.ru/projects/5/x.html"
    assert [b.callback_data for b in keyboard[1:]] == ["alt:5", "hide:5", "done:5"]


def test_answered_card_keeps_only_the_link():
    keyboard = buttons(card_keyboard(order(responded_at=NOON)))
    assert [b.text for b in keyboard] == ["🔗 Открыть заказ"]


def test_ago():
    assert ago(NOON, NOON) == "только что"
    assert ago(NOON + timedelta(minutes=5), NOON) == "только что"  # часы лент могут спешить
    assert ago(NOON - timedelta(minutes=59), NOON) == "59 мин назад"
    assert ago(NOON - timedelta(hours=3), NOON) == "3 ч назад"
    assert ago(NOON - timedelta(days=2), NOON) == "2 дн назад"


def test_duration():
    assert duration(12.4) == "12 мин"
    assert duration(60) == "1 ч"
    assert duration(135) == "2 ч 15 мин"


async def sent_order(tmp_path):
    h = await make(tmp_path)
    await h.start()
    await h.tick(rss_item(10, BOT, "Нужно сделать бота для записи"))
    return h


async def test_other_variant_replaces_draft_in_the_same_message(tmp_path):
    h = await sent_order(tmp_path)
    before = (await h.db.get(10)).draft
    await h.press("alt:10")
    after = await h.db.get(10)
    assert after.variant == 1 and after.draft != before
    edit = h.session.of(EditMessageText)[-1]
    assert f"<code>{after.draft}</code>" in edit.text and len(h.session.messages) == 1


async def test_other_variant_cycles_through_all_templates(tmp_path):
    h = await sent_order(tmp_path)
    drafts = [(await h.db.get(10)).draft]
    for _ in range(4):
        await h.press("alt:10")
        drafts.append((await h.db.get(10)).draft)
    assert len(set(drafts[:4])) == 4 and "Делал почти такого же бота" in drafts[4]


async def test_callback_is_answered_right_away(tmp_path):
    h = await sent_order(tmp_path)
    await h.press("alt:10")
    names = [type(c).__name__ for c in h.session.calls]
    assert names.index("AnswerCallbackQuery") < names.index("EditMessageText")


async def test_hide_deletes_message_and_marks_order(tmp_path):
    h = await sent_order(tmp_path)
    h.clock.shift(minutes=1)
    await h.press("hide:10")
    assert len(h.session.of(DeleteMessage)) == 1
    assert (await h.db.get(10)).hidden_at == h.clock()


async def test_hide_when_message_is_too_old_to_delete(tmp_path):
    h = await sent_order(tmp_path)
    h.session.fail_with = TelegramBadRequest(method=None, message="message can't be deleted")
    h.session.fail_on = lambda m: isinstance(m, DeleteMessage)
    await h.press("hide:10")
    assert "Скрыто" in h.session.of(EditMessageText)[-1].text
    assert (await h.db.get(10)).hidden_at is not None


async def test_responded_saves_reaction_time(tmp_path):
    h = await sent_order(tmp_path)
    h.clock.shift(minutes=7)
    await h.press("done:10")
    saved = await h.db.get(10)
    assert saved.responded_at == h.clock() and saved.notified_at == NOON
    edit = h.session.of(EditMessageText)[-1]
    assert "✅ Откликнулись через 7 мин" in edit.text
    assert [b.text for b in buttons(edit.reply_markup)] == ["🔗 Открыть заказ"]


async def test_second_press_keeps_first_reaction_time(tmp_path):
    h = await sent_order(tmp_path)
    h.clock.shift(minutes=7)
    await h.press("done:10")
    first = (await h.db.get(10)).responded_at
    h.clock.shift(minutes=30)
    await h.press("done:10")
    assert (await h.db.get(10)).responded_at == first
    assert h.session.of(AnswerCallbackQuery)[-1].text == "Уже отмечено"


async def test_unchanged_message_is_not_an_error(tmp_path):
    h = await sent_order(tmp_path)
    h.session.fail_with = TelegramBadRequest(method=None, message="message is not modified")
    h.session.fail_on = lambda m: isinstance(m, EditMessageText)
    await h.press("alt:10")
    assert (await h.db.get(10)).variant == 1


async def test_digest_number_opens_card_with_draft(tmp_path):
    h = await sent_order(tmp_path)
    h.session.calls.clear()
    await h.db._update(10, draft=None)
    await h.press("open:10")
    card = h.session.messages[0]
    assert "<code>" in card.text and (await h.db.get(10)).draft
    assert any(b.callback_data == "done:10" for b in buttons(card.reply_markup))


async def test_unknown_order_gets_an_answer(tmp_path):
    h = await sent_order(tmp_path)
    for action in ("alt", "hide", "done", "open"):
        await h.press(f"{action}:999")
        assert h.session.of(AnswerCallbackQuery)[-1].text == "Заказ не найден"


async def test_stranger_gets_nothing(tmp_path):
    h = await sent_order(tmp_path)
    h.session.calls.clear()
    for command in ("/start", "/stats", "/top", "/export"):
        await h.say(command, STRANGER)
    assert h.session.messages == [] and h.session.of(SendDocument) == []


async def test_stranger_cannot_press_buttons(tmp_path):
    h = await sent_order(tmp_path)
    await h.press("done:10", STRANGER)
    await h.press("hide:10", STRANGER)
    saved = await h.db.get(10)
    assert saved.responded_at is None and saved.hidden_at is None


async def test_start_explains_itself(tmp_path):
    h = await sent_order(tmp_path)
    h.session.calls.clear()
    await h.say("/start")
    text = h.session.messages[0].text
    assert "/stats" in text and "/top" in text and "/export" in text and "только читаю публичный RSS" in text
    assert "Последний опрос: только что" in text


async def test_stats_counts_day_and_week_by_category(tmp_path):
    h = await make(tmp_path)
    await h.start(
        rss_item(10, "Бот для записи (Бюджет: 20 000  &#8381;)", pub=NOON - timedelta(hours=1)),
        rss_item(11, "Интеграция amoCRM (Бюджет: 10 000  &#8381;)", pub=NOON - timedelta(hours=2)),
        rss_item(12, "Интеграция Битрикс", pub=NOON - timedelta(days=3)),
        rss_item(13, "Парсер цен (Бюджет: 30 000  &#8381;)", pub=NOON - timedelta(days=6)),
        rss_item(14, "Логотип", pub=NOON - timedelta(hours=1)),
        rss_item(15, "Бот старый", pub=NOON - timedelta(days=8)),
    )
    await h.say("/stats")
    text = h.session.messages[-1].text
    assert "Найдено: <b>2</b> / <b>4</b>" in text
    assert "booking: 1 / 1" in text and "integration: 1 / 2" in text and "parser: 0 / 1" in text
    assert "Средний бюджет: 15 000 ₽ / 20 000 ₽" in text
    assert "Откликнулся: 0 / 0" in text and "Среднее время реакции: — / —" in text
    assert "Всего в лентах за неделю: 5, отсеяно: 1" in text


async def test_stats_counts_responses_and_reaction(tmp_path):
    h = await sent_order(tmp_path)
    await h.tick(rss_item(11, "Интеграция amoCRM"))
    h.clock.shift(minutes=10)
    await h.press("done:10")
    h.clock.shift(minutes=10)
    await h.press("done:11")
    await h.say("/stats")
    text = h.session.messages[-1].text
    assert "Откликнулся: 2 / 2" in text and "Среднее время реакции: 15 мин / 15 мин" in text


def test_top_bigrams_groups_word_forms_and_ranks_by_count():
    titles = ["Телеграм бота для записи", "Телеграм бот для кафе", "Сделать телеграм бота", "Настройка amoCRM",
              "Настройка amoCRM", "Логотип кафе"]
    top = top_bigrams(titles)
    assert top[0] == ("телеграм бота", 3)
    assert ("настройка amocrm", 2) in top
    assert all(count > 1 for _, count in top)


def test_top_bigrams_counts_a_phrase_once_per_title():
    assert top_bigrams(["бот бот бот бот", "другое"]) == []


def test_top_bigrams_skips_prepositions_and_keeps_limit():
    titles = [f"слово{i} ещё{i}" for i in range(30)] * 2
    assert len(top_bigrams(titles)) == 20
    assert top_bigrams(["бот для записи", "бот для записи"]) == [("бот записи", 2)]


async def test_top_command(tmp_path):
    h = await make(tmp_path)
    await h.start(rss_item(10, "Разработка телеграм бота", pub=NOON - timedelta(hours=1)),
                  rss_item(11, "Разработка телеграм бота", pub=NOON - timedelta(hours=2)),
                  rss_item(12, "Логотип кафе", pub=NOON - timedelta(hours=2)),
                  rss_item(13, "Логотип кафе", pub=NOON - timedelta(hours=3)))
    await h.say("/top")
    text = h.session.messages[-1].text
    assert "разработка телеграм — 2" in text and "телеграм бота — 2" in text
    assert "логотип" not in text  # отсеянные заказы не входят


async def test_top_with_no_data(tmp_path):
    h = await make(tmp_path)
    await h.say("/top")
    assert "мало данных" in h.session.messages[-1].text


async def test_export_sends_all_orders_as_csv(tmp_path):
    h = await sent_order(tmp_path)
    await h.tick(rss_item(11, "Логотип"))
    await h.say("/export")
    document = h.session.of(SendDocument)[-1]
    assert document.document.filename == "fl-orders-2026-11-04.csv"
    assert "Заказов в файле: 3" in document.caption
    rows = list(csv.DictReader(io.StringIO(document.document.data.decode("utf-8-sig"))))
    assert {r["id"] for r in rows} == {"1", "10", "11"}
    by_id = {r["id"]: r for r in rows}
    assert by_id["10"]["budget"] == "20000" and by_id["10"]["matched"] == "1" and by_id["10"]["category"] == "booking"
    assert by_id["11"]["matched"] == "0" and by_id["11"]["reject_reason"] == "нет ключевых слов"


def test_export_starts_with_bom_for_excel():
    assert export_csv([order()]).startswith(b"\xef\xbb\xbf")


def test_export_neutralises_formulas_from_the_feed():
    data = export_csv([order(title='=HYPERLINK("http://evil","x")', description="+cmd", label="@a")]).decode("utf-8-sig")
    row = next(csv.DictReader(io.StringIO(data)))
    assert row["title"].startswith("'=") and row["description"] == "'+cmd" and row["label"] == "'@a"


async def test_messages_use_html_and_no_previews(tmp_path):
    h = await sent_order(tmp_path)
    assert h.bot.default.parse_mode == "HTML" and h.bot.default.link_preview_is_disabled is True
    assert isinstance(h.session.messages[0], SendMessage)
