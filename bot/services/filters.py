"""Отбор заказов, категория и оценка. Всё на простых правилах, чтобы результат можно было объяснить."""
from __future__ import annotations

import re
from dataclasses import dataclass

_L = r"(?<![а-яa-z0-9_])"
_R = r"(?![а-яa-z0-9_])"


def _word(pattern: str) -> str:
    """Целое слово: «бот» не должно находиться в «работа», «ии» — в «линии»."""
    return f"{_L}(?:{pattern}){_R}"


def _stem(pattern: str) -> str:
    """Начало слова с любым окончанием: морфология по корню."""
    return f"{_L}(?:{pattern})[а-яa-z0-9_]*"


def _compile(pattern: str) -> re.Pattern:
    return re.compile(pattern)


def normalize(text: str) -> str:
    return text.lower().replace("ё", "е")


# YCLIENTS здесь нет намеренно: это система записи, заказ с ней относится к booking
_CRM_NAMES = (r"amo\s?-?crm|амо\s?-?(?:crm|срм)|битрикс\w*|bitrix\w*|retail\s?crm|ритейл\s?срм|"
              r"мегаплан|планфикс|planfix|hubspot|salesforce")
_GOOGLE_SHEETS = r"(?:google|гугл)[\s-]*(?:таблиц\w*|sheets?)|googlesheets"
# «make up» и «make-up» — это косметика, не сервис автоматизации
_MAKE = rf"{_L}make(?![-\s]?up){_R}"
# Живой помощник — не наш заказ: «личный ассистент», «ассистент руководителя»
_ASSISTANT = (rf"{_L}(?<!личный )(?<!личного )(?<!ищу )(?<!ищем )(?<!нужен )ассистент[а-я]*"
              r"(?!\s+(?:руководител|директор|менеджер|врача|стоматолог|фотограф)[а-я]*)")

KEYWORDS: dict[str, re.Pattern] = {name: _compile(p) for name, p in {
    "telegram": _stem("телеграмм?|telegramm?|тг-?бот"),
    "бот": _word(r"(?:чат-?)?бот(?:а|у|ом|е|ы|ов|ам|ами|ах|ик|ика)?|(?:chat-?)?bots?"),
    "aiogram": _stem("aiogram"),
    "интеграция": _stem("интеграц|интегрир"),
    "webhook": _stem("вебхук|веб-хук|webhook"),
    "google таблицы": _L + f"(?:{_GOOGLE_SHEETS})",
    "amocrm": _L + r"(?:amo\s?-?crm|амо\s?-?(?:crm|срм))",
    "битрикс": _stem("битрикс|bitrix"),
    "yclients": _L + r"(?:yclients|y-clients)",
    "n8n": _word("n8n"),
    "make": _MAKE,
    "zapier": _stem("zapier"),
    "парсер": _stem("парсер|парсинг|парсит|спарсит"),
    "автоматизация": _stem("автоматизац|автоматизир"),
    "уведомления": _stem("уведомлен"),
    "crm": _word("crm|срм|црм"),
    "mini app": _L + r"(?:mini\s?-?apps?|мини\s?-?(?:апп\w*|приложен\w*))",
    "ai": _word("ai|ии|искусственн\\w+ интеллект\\w*"),
    "gpt": _word("(?:chat\\s?)?gpt[\\w.-]*|(?:чат\\s?)?гпт"),
    "ассистент": _ASSISTANT,
}.items()}

# Описание в RSS обрезано, поэтому раздел биржи тоже считается: заказ из «Разработка Чат-ботов» точно про бота
LABEL_HINTS = ("разработка чат-ботов", "боты для мессенджеров", "боты для бизнеса", "боты с ai", "интеграция по api",
               "парсинг данных", "ai-агенты", "amocrm", "битрикс24", "bitrix24", "n8n", "zapier", "make")

MINUS_WORDS: dict[str, re.Pattern] = {name: _compile(p) for name, p in {
    "за долю": _L + r"за\s+долю" + _R,
    "бесплатно": _word("бесплатно"),
    "продвижение": _stem("продвижени"),
    "накрутка": _stem("накрутк"),
    "рассылка спама": _L + r"рассылк[а-я]*\s+спам",
    "реферал": _stem("реферал"),
    "вакансия в штат": _L + r"ваканси[а-я]*\s+в\s+штат",
}.items()}


@dataclass(frozen=True)
class Verdict:
    matched: bool
    reason: str | None = None
    keywords: tuple[str, ...] = ()


def find_keywords(text: str) -> list[str]:
    text = normalize(text)
    return [name for name, pattern in KEYWORDS.items() if pattern.search(text)]


def check(title: str, description: str, label: str, budget: int | None, min_budget: int = 3000) -> Verdict:
    text = normalize(f"{title}\n{description}")
    for name, pattern in MINUS_WORDS.items():
        if pattern.search(text):
            return Verdict(False, f"минус-слово: {name}")
    keywords = find_keywords(text)
    if not keywords:
        section = normalize(label).split(" / ")[-1].strip()
        if section not in LABEL_HINTS:
            return Verdict(False, "нет ключевых слов")
        keywords = [section]
    if budget is not None and budget < min_budget:
        return Verdict(False, f"бюджет меньше {min_budget}")
    return Verdict(True, None, tuple(keywords))


# Вес совпадения. В заголовке вес удваивается: заголовок точнее передаёт суть заказа
_RULES: dict[str, list[tuple[int, re.Pattern]]] = {cat: [(w, _compile(p)) for w, p in rules] for cat, rules in {
    "ai": [
        (2, _word(r"ai|ии|llm|rag|искусственн\w+ интеллект\w*")),
        (2, _word(r"(?:chat\s?)?gpt[\w.-]*|(?:чат\s?)?гпт|deepseek|claude")),
        (2, _ASSISTANT),
        (2, _stem("нейросет|нейрон|ai-агент")),
    ],
    "booking": [
        (2, _L + r"онлайн[\s-]?запис"),
        (2, _L + r"запис[а-я]*\s+(?:на|к|клиент)"),
        (2, _stem("бронир")),
        (2, _L + r"(?:yclients|y-clients)"),
        (1, _stem("заяв[ко]")),
        (1, _stem("статус")),
        (1, _stem("расписани")),
        (1, _word("запись|записи|записей")),
    ],
    "integration": [
        (2, _L + f"(?:{_CRM_NAMES})"),
        (2, _word("crm|срм|црм|n8n|zapier")),
        (2, _MAKE),
        (2, _stem("вебхук|веб-хук|webhook|интеграц|интегрир")),
        (2, _L + f"(?:{_GOOGLE_SHEETS})"),
        (1, _word("api|апи|1с|1c")),
        (1, _stem("таблиц|выгрузк|синхрониз")),
    ],
    "parser": [
        (2, _stem("парс|спарс|скрап|scrap")),
        (1, _L + r"сбор[а-я]*\s+(?:данных|информац)"),
    ],
}.items()}

# При равном счёте порядок ключей _RULES решает, кто выигрывает: максимум берёт первую из равных
CATEGORIES = ("booking", "integration", "ai", "parser", "other")


def classify(title: str, description: str, label: str = "") -> str:
    title, description, label = normalize(title), normalize(description), normalize(label)
    best, best_score = "other", 0
    for category, rules in _RULES.items():
        score = 0
        for weight, pattern in rules:
            if pattern.search(title):
                score += weight * 2
            elif pattern.search(description) or pattern.search(label):
                score += weight
        if score > best_score:
            best, best_score = category, score
    return best


# «ИИ-видео» проходит по ключевому слову «ии», но это работа для монтажёра, а не для разработчика
_AI_MEDIA = _compile(
    _L + r"(?:генерация\s+(?:видео|изображ|голоса)|ai-аватар|видео|ролик|рилс|клип|монтаж|озвучк|короткометражк|фотосесс|аватар)")


def rate(category: str, budget: int | None, title: str = "", description: str = "", label: str = "") -> int:
    """Насколько заказ подходит нам, от 1 до 5. Booking и integration с бюджетом от 10 тысяч — это 5."""
    if category == "ai" and _AI_MEDIA.search(normalize(f"{title}\n{label}")):
        return 1
    big = budget is not None and budget >= 10_000
    unknown = budget is None
    if category in ("booking", "integration"):
        return 5 if big else 4 if unknown else 3
    if category == "ai":
        return 4 if big else 3 if unknown else 2
    if category == "parser":
        return 3 if big or unknown else 2
    score = 3 if big else 2 if unknown else 1
    # Telegram-бот без понятной категории всё равно ближе к нашему демо, чем прочие заказы
    text = normalize(f"{title}\n{description}")
    if KEYWORDS["бот"].search(text) or KEYWORDS["telegram"].search(text):
        score += 1
    return min(score, 5)
