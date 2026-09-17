"""Маршрутизатор клиентского бота: продажи, найм или сервис.

Один бот — три ассистента. Решение принимается в три ступени, от самой
дешёвой и предсказуемой к самой дорогой:

1. Явный выбор клиента (кнопка или команда) — выигрывает всегда.
2. Правила по сигналам в тексте: VIN, номер наряда, слова-маркеры.
3. Арбитр на модели — только когда правила не дали уверенного отрыва.

Если не уверены и после арбитра — спрашиваем клиента кнопками, а не гадаем.
Это дешевле, чем ответить голосом не того отдела.

Отдельное требование заказчика: клиент, пришедший в сервис, должен иметь
возможность тут же спросить про новые модели и скидки. Поэтому маршрут
липкий в пределах темы, но сильный сигнал другого отдела его перебивает —
см. HYSTERESIS_MARGIN и STRONG.
"""

from __future__ import annotations

import re

ROUTES = ("sales", "hr", "service")

ROUTE_NAMES = {
    "sales": "Первый контакт — продажи",
    "hr": "Кадры — вакансии и отклики",
    "service": "Сервис и постпродажное обслуживание",
}

# Сильные сигналы: почти не встречаются в чужой теме. Перебивают липкость маршрута.
STRONG = {
    "service": [
        r"\btestag\d{6,}\b",                      # VIN из фикстур
        r"\b[авекмнорстух]\d{3}[авекмнорстух]{2}\d{2,3}\b",  # госномер
        r"\bwo-\d+\b",                            # номер заказ-наряда
        r"заказ[- ]наряд", r"гарантийн\w+", r"эвакуатор", r"\bто-[1-4]\b",
        r"\bто\b", r"пройти то", r"на то пора",  # одиночное «ТО» — техобслуживание
        r"не заводится", r"застучал\w*", r"течь масла", r"дефектовк\w+",
    ],
    "hr": [
        r"ваканси\w+", r"резюме", r"трудоустр\w+", r"собеседовани\w+", r"соискател\w+",
        r"хочу (?:у вас )?работать", r"устроит\w+ся( на работу)?", r"приём на работу",
        r"ищу работу", r"\bhh\.?ru\b", r"откликнул\w+ся на",
    ],
    "sales": [
        r"тест[- ]драйв", r"трейд[- ]ин", r"комплектаци\w+", r"нов\w+ модел\w+",
        r"поменять машину", r"хочу купить", r"в кредит", r"рассрочк\w+",
        r"что нового", r"какие акции", r"какие скидки", r"новинк\w+",
    ],
}

# Слабые сигналы: тему подсказывают, но в одиночку не решают.
WEAK = {
    "service": ["то", "сервис", "обслуживани", "ремонт", "гаранти", "масл", "колодк",
                "диагностик", "стучит", "скрипит", "запчаст", "мастер", "приёмщик",
                "пробег", "неисправн", "чинить", "починить"],
    "hr": ["работа", "зарплат", "оклад", "график работы", "смена", "карьер",
           "стажировк", "опыт работы", "hr", "кадр", "испытательн"],
    "sales": ["купить", "цена", "стоит", "скидк", "акци", "наличии", "модел",
              "кроссовер", "седан", "автомобиль", "машину", "кредит", "подобрать",
              "посмотреть", "салон", "новая", "новый"],
}

# Насколько новый маршрут должен опередить текущий, чтобы переключение произошло.
HYSTERESIS_MARGIN = 2
# Отрыв, при котором правила решают сами и арбитр не нужен.
CONFIDENT_MARGIN = 2

ARBITER_PROMPT = """Ты классификатор обращений автосалона «АвтоГрад». Определи, к кому обращается человек.

sales — покупка автомобиля, наличие, цены, комплектации, акции и скидки на машины, тест-драйв, трейд-ин, кредит на покупку.
hr — трудоустройство: вакансии, отклик, резюме, собеседование, условия работы в компании.
service — обслуживание уже купленной машины: ТО, ремонт, гарантийный случай, статус ремонта, запись в сервис.

Ответь одним словом: sales, hr, service или unknown. Ничего не объясняй.
Если сообщение — приветствие без темы или тему определить нельзя, отвечай unknown."""


def _weak_pattern(word: str) -> str:
    """Короткое слово ищем целиком, длинное — по началу слова.

    Без границы слова маркер «то» совпадал внутри «что» и «это», и сервис
    набирал очки на любой фразе. Это ломало переключение маршрута.
    """
    if len(word) <= 3:
        return r"\b" + re.escape(word) + r"\b"
    return r"\b" + re.escape(word)


_WEAK_COMPILED = {route: [(w, re.compile(_weak_pattern(w))) for w in words]
                  for route, words in WEAK.items()}


def catalogue_names() -> set[str]:
    """Названия марок и моделей, включая кириллические написания клиента."""
    try:
        import sys
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
        from assistant import CYRILLIC_NAMES, load_json
        names = set(CYRILLIC_NAMES)
        for m in load_json("models.json")["models"]:
            names.add(m["make"].lower())
            names.add(m["model"].lower())
        return names
    except Exception:
        return set()


_NAMES = catalogue_names()


def score(text: str) -> dict[str, float]:
    """Счёт по каждому маршруту: сильный сигнал — 10, слабый — 1."""
    low = text.lower()
    result = {r: 0.0 for r in ROUTES}
    for route, patterns in STRONG.items():
        for pat in patterns:
            if re.search(pat, low):
                result[route] += 10
    for route, compiled in _WEAK_COMPILED.items():
        for _word, pat in compiled:
            if pat.search(low):
                result[route] += 1
    # Названная марка или модель — заметный, но не решающий сигнал продаж:
    # «Aurora X5 когда на ТО» должна остаться в сервисе.
    words = set(re.findall(r"[а-яёa-z0-9]+", low))
    if words & _NAMES:
        result["sales"] += 4
    return result


def has_strong(text: str, route: str) -> bool:
    low = text.lower()
    return any(re.search(p, low) for p in STRONG[route])


def rule_decision(text: str) -> tuple[str | None, dict[str, float]]:
    """Маршрут по правилам или None, если уверенного отрыва нет."""
    scores = score(text)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    top, second = ranked[0], ranked[1]
    if top[1] == 0:
        return None, scores
    if top[1] >= 10 and top[1] - second[1] >= 10:
        return top[0], scores
    if top[1] - second[1] >= CONFIDENT_MARGIN:
        return top[0], scores
    return None, scores


def arbiter_decision(text: str, model: str = "gpt-5-mini") -> str | None:
    """Арбитр на модели. Вызывается только на спорных сообщениях."""
    from openai import OpenAI
    try:
        resp = OpenAI().chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": ARBITER_PROMPT},
                      {"role": "user", "content": text[:1000]}],
        )
        answer = (resp.choices[0].message.content or "").strip().lower()
    except Exception:
        return None
    for route in ROUTES:
        if route in answer:
            return route
    return None


def route(text: str, current: str | None = None, use_arbiter: bool = True,
          model: str = "gpt-5-mini") -> dict:
    """Возвращает {route, source, switched, scores}. route=None — надо спросить клиента."""
    decided, scores = rule_decision(text)
    source = "правила"

    if decided is None and use_arbiter:
        decided = arbiter_decision(text, model=model)
        source = "арбитр" if decided else source

    if decided is None:
        # Тему определить не удалось. Если маршрут уже был — остаёмся в нём.
        return {"route": current, "source": "не определено", "switched": False,
                "scores": scores, "ask": current is None}

    if current is None or decided == current:
        return {"route": decided, "source": source, "switched": False,
                "scores": scores, "ask": False}

    # Маршрут есть и он другой: переключаемся только при уверенном сигнале.
    # Без этого уточняющий вопрос внутри темы («а когда готово?») уводил бы
    # клиента в другой отдел на каждой реплике.
    strong = has_strong(text, decided)
    margin = scores[decided] - scores[current]
    if strong or margin >= HYSTERESIS_MARGIN:
        return {"route": decided, "source": source, "switched": True,
                "scores": scores, "ask": False}

    return {"route": current, "source": "удержание маршрута", "switched": False,
            "scores": scores, "ask": False}
