"""Шаблонный черновик отклика: пункты из текста заказа и один вопрос по пробелу в ТЗ."""
from __future__ import annotations

import re

from bot.services.filters import _L, _R, normalize

DEMO_LINK = "t.me/RemontPlusDemoBot"

PRICES = {"booking": 14_900, "integration": 7_900, "ai": 19_900, "parser": 9_900, "other": 9_900}
TERMS = {"booking": 3, "integration": 2, "ai": 5, "parser": 2, "other": 3}

TEMPLATES = {
    "booking": (
        "Здравствуйте! Делал почти такого же бота: заявка → сохраняется в базе → вам уведомление → клиенту статус. "
        "Демо можно потрогать: {demo}. Под вашу задачу: {points}. Срок — {term} дня, цена — {price} ₽ фикс. "
        "Отдаю исходный код, инструкцию по запуску и тесты. Вопрос: {question}"
    ),
    "integration": (
        "Здравствуйте! Сделаю связку {systems}: {points}. Делаю такие интеграции через вебхуки/API, с логами "
        "и уведомлением об ошибках. Срок — {term} дня, {price} ₽ фикс. Чтобы оценить точно: {question}"
    ),
    "ai": (
        "Здравствуйте! Сделаю AI-ассистента, который отвечает по вашим материалам и передаёт сложные вопросы "
        "человеку — без выдумок. Под задачу: {points}. Пример переписки клиента с администратором через бота: {demo}. "
        "Срок {term} дней, {price} ₽. Вопрос: {question}"
    ),
    "universal": (
        "Здравствуйте! Возьмусь: {points}. Отдаю исходный код и инструкцию по запуску. "
        "Срок — {term} дня, {price} ₽ фикс. Вопрос: {question}"
    ),
}
# «Другой вариант» перебирает шаблоны по кругу, начиная с шаблона категории заказа
_ROTATION = ("booking", "integration", "ai", "universal")

_VERBS = re.compile(_L + r"(?:нужн[а-я]*|надо|сделать|сделайте|должен|должна|должно|должны|чтобы|необходимо|требуется)" + _R)
_LEAD = re.compile(r"^(?:(?:нам|мне|вам|также|еще|ещё)\s+)*(?:нужн[а-я]*|надо|необходимо|требуется|хочу|хотим)[\s,:]+", re.I)
# Маркеры списков и эмодзи в начале строки: всё, что не буква и не цифра. Номер пункта убирается вместе с точкой
_BULLET = re.compile(r"^(?:\d{1,2}[.)]\s+)?[^\w(«\"]+|^\d{1,2}[.)]\s*")
# Служебные фразы и отрицания: они не описывают задачу
_META = re.compile(
    r"отклик|портфолио|в\s+лс|в\s+личк|пишите|напишите|кейс|здравствуйте|добрый\s+день|что\s+(?:надо|нужно)\s+делать|"
    r"\bне\s+(?:нужн|надо|должн)|"
    r"(?:нужен|нужна|нужны|ищу|ищем|требуется)\s+(?:\w+\s+)?(?:программист|разработчик|специалист|исполнител|фрилансер|человек)")
POINT_LIMIT = 100

_TERM = re.compile(
    r"срок|дедлайн|сегодня|завтра|срочн|недел[июяе]|месяц|"
    + _L + r"(?:до|к)\s+\d{1,2}[.\s]|\d+\s*(?:дн|дня|дней|час)")
_VOLUME = re.compile(
    r"\d+\s*(?:тыс\w*\s+|к\s+)?(?:пользовател|заяв|клиент|человек|сообщен|товар|страниц|запис|строк|сайт|карточ|позиц|менеджер)")
_CRM_WORD = re.compile(_L + r"(?:crm|срм|црм)" + _R)

# Как система называется в тексте заказа и как её написать в отклике
SYSTEMS = (
    (r"amo\s?-?crm|амо\s?-?(?:crm|срм)", "amoCRM"),
    (r"битрикс|bitrix", "Битрикс24"),
    (r"yclients|y-clients", "YCLIENTS"),
    (r"retail\s?crm|ритейл\s?срм", "RetailCRM"),
    (r"(?:google|гугл)[\s-]*(?:таблиц|sheets?)|googlesheets", "Google Таблицы"),
    (r"телеграмм?|telegramm?", "Telegram"),
    (r"whatsapp|ватсап|вотсап", "WhatsApp"),
    (r"1с|1c", "1С"),
    (r"n8n", "n8n"),
    (r"zapier", "Zapier"),
    (r"make\.com|make" + _R, "Make"),
    (r"авито|avito", "Авито"),
    (r"wildberries|вайлдберриз", "Wildberries"),
    (r"ozon|озон", "Ozon"),
    (r"мегаплан", "Мегаплан"),
    (r"планфикс|planfix", "Планфикс"),
    (r"jira", "Jira"),
    (r"excel|эксель", "Excel"),
)
_CRM_SYSTEMS = {"amoCRM", "Битрикс24", "YCLIENTS", "RetailCRM", "Мегаплан", "Планфикс"}


def find_systems(text: str) -> list[str]:
    text = normalize(text)
    return [name for pattern, name in SYSTEMS if re.search(_L + f"(?:{pattern})", text)]


def _lower_first(text: str) -> str:
    # Аббревиатуры и названия вроде «CRM» и «Telegram» оставляем как есть
    if len(text) > 1 and text[0].isupper() and text[1].islower() and text[0] in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЭЮЯ":
        return text[0].lower() + text[1:]
    return text


def _shorten(text: str, limit: int = POINT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"


def _sentences(description: str) -> list[str]:
    text = description.strip()
    if text.endswith(("...", "…")):
        # RSS обрезает описание посреди слова: хвост с неполным словом не нужен
        text = text.rstrip(".…")
        text = text.rsplit(" ", 1)[0] if " " in text else text
    parts = re.split(r"(?<=[.!?…])\s+|\n+", text)
    return [_BULLET.sub("", p).strip() for p in parts if p.strip()]


def _to_point(sentence: str) -> str:
    point = re.sub(r"\s+", " ", sentence).strip().rstrip(".!;:,")
    while True:  # «Нам также нужно …» снимается в один проход, «Нужно: надо …» — в два
        shorter = _LEAD.sub("", point).lstrip(":— ").strip()
        if shorter == point:
            break
        point = shorter
    return _lower_first(_shorten(point))


def _words(text: str) -> set[str]:
    # Первые 4 буквы: «бота» и «боты», «перенести» и «перенос» считаются одним словом
    return {w[:4] for w in re.findall(r"[а-яa-z0-9]{3,}", normalize(text))}


def _repeats(point: str, points: list[str]) -> bool:
    """Заголовок обычно пересказывает описание: дважды одно и то же в отклике не нужно."""
    words = _words(point)
    have = _words(" ".join(points))
    return bool(words) and len(words & have) / len(words) >= 0.5


def extract_points(title: str, description: str, limit: int = 3) -> list[str]:
    """Предложения с глаголами «нужно/сделать/должен/чтобы». Если таких нет — первое предложение и заголовок."""
    points: list[str] = []

    sentences = _sentences(description)
    for sentence in sentences:
        low = normalize(sentence)
        if _VERBS.search(low) and not _META.search(low):
            point = _to_point(sentence)
            if len(point) >= 8 and point not in points:
                points.append(point)
        if len(points) == limit:
            return points
    if not points:
        first = next((s for s in sentences if not _META.search(normalize(s))), "")
        if len(first) >= 8:
            points.append(_to_point(first))
    if len(points) < 2 and title:
        point = _to_point(title)
        if len(point) >= 8 and not _repeats(point, points):
            points.append(point)
    return points


def build_questions(title: str, description: str, category: str) -> list[str]:
    """Вопросы по пробелам в ТЗ, самый важный первым. Всегда хотя бы один."""
    text = normalize(f"{title}\n{description}")
    has_crm = bool(_CRM_SYSTEMS & set(find_systems(text)))
    term = None if _TERM.search(text) else "какой срок для вас комфортный, есть ли дедлайн?"
    crm = None if has_crm else "какая у вас CRM и есть ли доступ к API?"
    if _VOLUME.search(text):
        volume = None
    elif category == "parser":
        volume = "сколько страниц или позиций нужно собирать и как часто?"
    else:
        volume = "сколько примерно пользователей и заявок в день?"

    if category == "integration":
        order = [crm, term, volume]
    elif _CRM_WORD.search(text):
        order = [crm, term, volume]  # CRM упомянута, но не названа
    else:
        order = [term, volume]
    questions = [q for q in order if q]
    if not questions:
        questions = [{"ai": "на каких материалах должен отвечать ассистент и где они лежат?"}.get(
            category, "есть ли пример или готовое ТЗ, на которое ориентироваться?")]
    return questions


def template_for(category: str, variant: int = 0) -> str:
    first = category if category in TEMPLATES else "universal"
    rotation = [first, *(t for t in _ROTATION if t != first)]
    return rotation[variant % len(rotation)]


def format_price(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def build_draft(title: str, description: str, category: str, budget: int | None, variant: int = 0) -> str:
    template = template_for(category, variant)
    # Универсальный шаблон у parser и other общий, а цена и срок у них свои
    kind = template if template != "universal" else (category if category in ("parser", "other") else "other")
    points = extract_points(title, description)
    questions = build_questions(title, description, category)
    systems = find_systems(f"{title}\n{description}")
    return TEMPLATES[template].format(
        demo=DEMO_LINK,
        points="; ".join(points) or _lower_first(title),
        term=TERMS[kind],
        price=format_price(budget if budget is not None else PRICES[kind]),
        question=questions[variant % len(questions)],
        systems=" + ".join(systems[:4]) if systems else "нужных вам систем",
    )
