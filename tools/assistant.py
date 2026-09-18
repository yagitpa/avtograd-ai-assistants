"""Минимальный горячий контур: сборка промпта, поиск по базе знаний, вызов модели, валидатор.

Учебный срез ядра из ADR-0002: системный промпт = каркас + роль, факты приходят
отдельным блоком ЗНАНИЯ, обстановка — блоком КОНТЕКСТ, исходящее сообщение
проходит детерминированный валидатор стоп-листа.

Персональные данные не уходят в модель: перед вызовом текст проходит шлюз
(`pii.py`), ответ разворачивается обратно. Поиск по фикстурам работает до
шлюза — ему нужны настоящие VIN и номера, и он локальный.

Ответ помечается версией промпта и версией знаний (ADR-0016) и может быть
взят из кэша, если вопрос обезличенный (ADR-0017).

Используется из tools/chat.py, tools/run_golden.py и tools/telegram_bots.py.
"""

from __future__ import annotations

import os
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "docs" / "knowledge-base"
FIX = KB / "fixtures"
PROMPTS = ROOT / "docs" / "prompts"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pii import Vault  # noqa: E402
from versions import kb_version, prompt_version  # noqa: E402

# Версии считаются один раз при импорте: процесс живёт с тем текстом промпта,
# с которым запущен, и ответ помечается именно им (ADR-0016).
KB_VERSION = kb_version()


def stamp(result: dict, role: str) -> dict:
    """Помечает ответ версиями промпта и знаний.

    Без этой пометки разбор жалобы упирается в вопрос «а что тогда было
    написано в промпте» — и ответа на него нет.
    """
    result["prompt_version"] = PROMPT_VERSIONS.get(role, "—")
    result["kb_version"] = KB_VERSION
    return result


def load_env(path: Path | None = None) -> None:
    """Читает .env из корня проекта.

    Уже заданная переменная окружения приоритетнее файла: так временный
    экспорт в консоли перекрывает .env, а не наоборот.
    """
    path = path or ROOT / '.env'
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


load_env()
DEFAULT_MODEL = os.environ.get('AVTOGRAD_MODEL', 'gpt-5-mini')

ROLES = {
    "sales": {"file": "sales.md", "department": "sales_new", "name": "Первый контакт"},
    "service": {"file": "service.md", "department": "service", "name": "АвтоГрад Сервис"},
    "hr": {"file": "hr.md", "department": "hr", "name": "Кадровик"},
}

PROMPT_VERSIONS = {role: prompt_version(role) for role in ROLES}

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
STOPWORDS = {"и", "в", "на", "с", "у", "по", "за", "не", "что", "как", "а", "но", "мне",
             "я", "вы", "мы", "это", "для", "от", "до", "ли", "же", "бы", "есть", "быть"}

# --- Стоп-лист в исполняемом виде (docs/knowledge-base/stop-list.md) ---
# Число с разделителем разрядов: «200 000» и «200000» — одна и та же сумма.
# Без этого куска шаблон со \d{4,} пропускал любую сумму, записанную по-русски.
#
# Пробел допускается только между группами ровно по три цифры. Более
# вольный шаблон склеивал «Aurora X5 2024» в число 52024 — и соседство
# модели с годом выпуска читалось как названная сумма.
# Границы обязательны: без них шаблон откусывает «5 202» от «X5 2024».
GROUPED = r"(?<!\d)\d{1,3}(?:[   ]\d{3})+(?!\d)"
# Единица измерения после числа означает, что это не деньги. «Передаю запрос
# о сумме выкупа Vector Sedan (60 000 км)» — это пробег, и красная линия про
# названную сумму выкупа здесь ни при чём.
NOT_UNIT = r"(?!\s*(?:км\b|km\b|кг\b|л\b|литр|лет\b|год|мес|дн))"
NUM4 = rf"(?:{GROUPED}|\d{{4,}}){NOT_UNIT}"
NUM6 = rf"(?:{GROUPED}|\d{{6,}}){NOT_UNIT}"

STOP_PATTERNS = {
    "решение по гарантии": re.compile(r"(это|у вас|ваш)\s+гарантийн\w+|не\s+гарантийн\w+|"
                                      r"призна[юё]\s+.{0,20}гарантийн\w+|"
                                      r"гарантии\s+(заменим|отремонтируем|сделаем|бесплатно)", re.I),
    # Действие во внешней системе, объявленное совершённым. Прошедшее время —
    # признак лжи: ассистенту не подключено ничего, что меняет состояние.
    "ложное действие": re.compile(r"\b(передал|передала|зарегистрировал\w*|зафиксировал\w*|"
                                  r"оформил\w*|забронировал\w*|зарезервировал\w*|"
                                  r"поставил\w*\s+в\s+очередь|записал\w*\s+(вас|на)|"
                                  r"создал\w*\s+(заявку|запись|наряд))\b|"
                                  r"(лог|переписк\w+|обращени\w+)\s+сохран[ёе]н|"
                                  r"\b(соедин|переключ)\w*\s+вас", re.I),
    # Внутренние идентификаторы записей в ответе клиенту. Номер заказ-наряда
    # (wo-) не в списке намеренно: это документ, который клиент видит в ДЦ.
    "служебный идентификатор": re.compile(r"\bon_call\.\w+|\b(ACT-\d+|ESC-\d+|"
                                          r"FAQ-[A-ZА-Я]{2,4}-\d+|veh-\d+|cus-\d+|vac-\d+|"
                                          r"emp-\d+|cand-\d+|sales_new|sales_used|"
                                          r"bot_owned|human_owned)\b", re.I),
    "обещание срока": re.compile(r"(будет\s+гото\w+|сделаем|успеем|заберёте)\s+(к|в|до)\s+"
                                 r"(понедельник\w*|вторник\w*|сред\w+|четверг\w*|пятниц\w+|"
                                 r"суббот\w+|воскресень\w+|завтра|послезавтра)", re.I),
    "сумма скидки": re.compile(rf"скидк\w+\s+(в\s+)?{NUM4}|{NUM4}\s*(₽|руб\w*)\s+скидк|"
                               r"(согласую|выбью|договорюсь)\s+.{0,20}скидк", re.I),
    "итоговая цена": re.compile(r"(итогов\w+|окончательн\w+|финальн\w+)\s+цена", re.I),
    # Снова граница слова: «процент» содержит «оцен», и вилка дохода в ответе
    # кадровика читалась как названная сумма выкупа автомобиля.
    "сумма выкупа": re.compile(rf"\b(дад\w+|оцен\w+|выкуп\w+)\s+.{{0,25}}{NUM6}|"
                               r"(ваша\s+машина|ваш\s+автомобиль)\s+стоит\s+\d", re.I),
    # Граница слова обязательна: без неё «подобрать вам автомобиль» читается
    # как «одобрать вам» и корректный ответ блокируется.
    "обещание банка": re.compile(r"\b(одобр\w+)\s+(вам|вас|точно)|банк\s+одобрит", re.I),
    "компенсация": re.compile(r"\bкомпенсир\w+|\bверн[ёе]м\s+(деньги|стоимость|средства)", re.I),
    "диагноз": re.compile(r"\bэто\s+(амортизатор\w*|насос\w*|подшипник\w*|ступиц\w+|"
                          r"сцеплени\w+|генератор\w*|стартер\w*)", re.I),
    "запрос документов": re.compile(r"(пришлите|отправьте|скиньте)\s+.{0,20}"
                                    r"(паспорт\w*|снилс|инн|карт\w+|скан\w*)", re.I),
    # Отрицание снимает запрет: «не гарантирую одобрение» — рекомендованная
    # замена из стоп-листа, а не нарушение.
    "абсолютное обещание": re.compile(r"(?<!не )\b(гарантирую|обещаю|100\s*%|точно\s+будет|"
                                      r"можете\s+быть\s+уверены)\b", re.I),
}


def load_json(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def parse_records(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    recs, i, n = [], 0, len(lines)
    while i < n:
        if lines[i].strip() == "---" and i + 1 < n and lines[i + 1].startswith("id:"):
            i += 1
            meta = {}
            while i < n and lines[i].strip() != "---":
                if ":" in lines[i] and not lines[i].startswith(" "):
                    k, _, v = lines[i].partition(":")
                    meta[k.strip()] = v.strip()
                i += 1
            i += 1
            body = []
            while i < n and not (lines[i].strip() == "---" and i + 1 < n
                                 and lines[i + 1].startswith("id:")):
                body.append(lines[i])
                i += 1
            meta["body"] = "\n".join(body).strip()
            recs.append(meta)
        else:
            i += 1
    return recs


def load_kb(role: str) -> list[dict]:
    files = list(KB.glob("faq/*.md")) + list(KB.glob("cases/*.md")) \
        + list(KB.glob("scripts/*.md")) + [KB / "escalations.md"]
    out = []
    for f in files:
        for rec in parse_records(f):
            if role in rec.get("assistants", ""):
                out.append(rec)
    return out


def norm(text: str) -> list[str]:
    words = re.findall(r"[а-яёa-z0-9]+", text.lower())
    return [w[:5] for w in words if w not in STOPWORDS and len(w) > 2]


def retrieve(query: str, records: list[dict], k: int = 5) -> list[dict]:
    terms = set(norm(query))
    scored = []
    for rec in records:
        hay = norm(rec.get("body", "") + " " + rec.get("tags", ""))
        score = sum(1 for t in terms if t in hay)
        if rec.get("type") == "escalation":
            score += 0.5 * score  # карта эскалаций весит больше при совпадении
        if score:
            scored.append((score, rec))
    scored.sort(key=lambda x: -x[0])
    return [r for _, r in scored[:k]]


def load_stock() -> list[dict]:
    with open(FIX / "stock.csv", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


# Клиент пишет по-русски и как придётся: «вега кросс», «Аврора Х5», «кроссовер до 2.5 млн».
CYRILLIC_NAMES = {
    "вега": "Vega", "веге": "Vega", "вегу": "Vega",
    "аврора": "Aurora", "авроры": "Aurora", "аврору": "Aurora", "авроре": "Aurora",
    "вектор": "Vector", "вектора": "Vector",
    "кросс": "Cross", "кросса": "Cross", "кроссе": "Cross",
    "седан": "Sedan", "седана": "Sedan", "седане": "Sedan",
    "спорт": "Sport", "спорта": "Sport",
    "х3": "X3", "x3": "X3", "х5": "X5", "x5": "X5", "с7": "S7", "s7": "S7",
}
BODY_WORDS = {
    "кроссовер": "кроссовер", "кроссоверы": "кроссовер", "кроссовера": "кроссовер",
    "внедорожник": "кроссовер", "паркетник": "кроссовер",
    "седан": "седан", "седаны": "седан", "седана": "седан",
    "купе": "купе",
}
USED_WORDS = ("пробегом", " бу", "б/у", "подержан", "вторичк")
NEW_WORDS = ("новый", "новая", "новое", "новые", "новых")


def parse_budget(query: str) -> int | None:
    """«до 2.5 млн», «2 миллиона», «500 тысяч», «2 500 000» → рубли."""
    q = query.lower().replace(",", ".")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:млн|миллион\w*)", q)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:тыс\w*)", q)
    if m:
        return int(float(m.group(1)) * 1_000)
    m = re.search(r"\b(\d[\d\s]{5,})\b", q)
    if m:
        return int(m.group(1).replace(" ", ""))
    return None


def model_bodies() -> dict:
    return {(m["make"], m["model"]): m["body"] for m in load_json("models.json")["models"]}


def find_vehicles(query: str, limit: int = 5) -> list[dict]:
    """Поиск по стоку на естественной формулировке: название латиницей или
    кириллицей, тип кузова, состояние, бюджет. Пустой список — запрос не про
    подбор автомобиля."""
    rows = load_stock()
    q = query.lower()
    words = set(re.findall(r"[а-яёa-z0-9/]+", q))

    by_vin = [r for r in rows if r["vin"].lower() in q]
    if by_vin:
        return by_vin[:limit]

    all_makes = {r["make"] for r in rows}
    all_models = {r["model"] for r in rows}
    spotted = {CYRILLIC_NAMES[w] for w in words if w in CYRILLIC_NAMES}
    spotted |= {m for m in all_makes if m.lower() in q}
    spotted |= {m for m in all_models if m.lower() in q}
    makes = spotted & all_makes
    models = spotted & all_models
    named = makes | models

    bodies = {BODY_WORDS[w] for w in words if w in BODY_WORDS}
    budget = parse_budget(q)
    used = any(w in q for w in USED_WORDS)
    new = any(w in q for w in NEW_WORDS)

    if not (named or bodies or budget or used or new):
        return []

    hits = list(rows)
    if makes and models:      # «вега кросс» — обе части, иначе выпадет вся марка
        hits = [r for r in hits if r["make"] in makes and r["model"] in models]
    elif models:
        hits = [r for r in hits if r["model"] in models]
    elif makes:
        hits = [r for r in hits if r["make"] in makes]
    elif bodies:
        lookup = model_bodies()
        hits = [r for r in hits
                if any(b in lookup.get((r["make"], r["model"]), "") for b in bodies)]
    if used:
        hits = [r for r in hits if r["condition"] == "used"]
    elif new:
        hits = [r for r in hits if r["condition"] == "new"]
    if budget:
        hits = [r for r in hits if int(r["price_rub"]) <= budget * 1.05]

    hits.sort(key=lambda r: (r["status"] != "in_stock", int(r["price_rub"])))
    return hits[:limit]


def find_owner_vehicle(query: str) -> dict | None:
    """Карточка машины клиента по VIN или госномеру — с историей заказ-нарядов.

    Без этого сервисный ассистент видел только сток, то есть машины на продажу,
    и на VIN клиента отвечал «принял, поднимаю карточку», не имея карточки.
    """
    q = query.lower().replace(" ", "")
    vehicles = load_json("vehicles.json")["vehicles"]
    found = None
    for v in vehicles:
        plate = v.get("plate", "").lower().replace(" ", "")
        if v["vin"].lower() in q or (plate and plate in q):
            found = v
            break
    if not found:
        return None
    orders = [o for o in load_json("work_orders.json")["work_orders"]
              if o["vehicle_id"] == found["id"]]
    customers = {c["id"]: c for c in load_json("customers.json")["customers"]}
    return {"vehicle": found, "orders": orders,
            "customer": customers.get(found.get("customer_id"), {})}


def looks_like_vehicle_id(query: str) -> bool:
    """Похоже ли на VIN или госномер — чтобы отличить «нет такого» от «не спрашивали»."""
    q = query.upper().replace(" ", "")
    return bool(re.search(r"TESTAG\d{4,}", q) or
                re.search(r"[АВЕКМНОРСТУХABEKMHOPCTYX]\d{3}[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\d{2,3}", q))


# В российском госномере используются двенадцать букв, совпадающих по
# начертанию с латиницей. «П», «Щ», «Ю» и прочие в номерах не встречаются.
PLATE_LETTERS = set("АВЕКМНОРСТУХ") | set("ABEKMHOPCTYX")
PLATE_SHAPE = re.compile(r"(?<![A-Za-zА-Яа-яЁё0-9])([А-Яа-яЁёA-Za-z])\s?(\d{3})\s?"
                         r"([А-Яа-яЁёA-Za-z]{2})\s?(\d{2,3})(?![A-Za-zА-Яа-яЁё0-9])")


def plate_note(text: str) -> str | None:
    """Похоже ли на госномер и настоящие ли в нём буквы.

    «П 001 ЩЮ 222» выглядит как номер и принимался молча: поиск его просто
    не находил, а ассистент продолжал разговор, будто номер принят. Проверка
    формата — это не проверка существования: она отвечает на вопрос «мог ли
    такой номер быть выдан», и отвечает до обращения к базе.
    """
    for match in PLATE_SHAPE.finditer(text.upper()):
        letters = match.group(1) + match.group(3)
        wrong = sorted({c for c in letters if c not in PLATE_LETTERS})
        if wrong:
            return ("ГОСНОМЕР: в сообщении есть похожее на номер сочетание, но буквы "
                    f"{', '.join(wrong)} в госномерах не используются. Переспросить номер; "
                    "карточку по нему не искать и не делать вид, что он принят.")
    return None


def active_promotions(today: str) -> list[dict]:
    """Только действующие: истёкшая акция не предлагается ни при каких условиях."""
    return [a for a in load_json("promotions.json")["promotions"]
            if a.get("status") == "active" and a.get("to", "") >= today]


def expand_days(key: str) -> set[str]:
    """«mon-sun» → все дни, «sat» → {sat}. Без этого диапазоны не совпадают с днём недели."""
    if "-" not in key:
        return {key.strip()}
    start, _, end = key.partition("-")
    i, j = WEEKDAYS.index(start.strip()), WEEKDAYS.index(end.strip())
    return set(WEEKDAYS[i:j + 1]) if i <= j else set(WEEKDAYS[i:] + WEEKDAYS[:j + 1])


def working_hours(department: str, now: datetime) -> tuple[str, bool]:
    wh = load_json("working_hours.json")
    sched = wh["schedule"].get(department, {})
    day = WEEKDAYS[now.weekday()]
    window = None
    for key, val in sched.items():
        if day in expand_days(key):
            window = val
            break
    if not window:
        return "сегодня выходной", False
    start, end = window
    is_open = start <= now.strftime("%H:%M") <= end
    return f"{start}–{end}", is_open


PHONE_RE = re.compile(r"[+]?[0-9][0-9 ()-]{5,}[0-9]")


def phone_note(text: str) -> str | None:
    """Похоже ли на телефон и сколько в нём цифр.

    Российский номер — десять цифр без кода страны или одиннадцать с ним.
    «+38 222 222 22» выглядит как номер, но набрать его нельзя: девять цифр.
    Принять такой контакт молча значит пообещать звонок, которого не будет,
    поэтому ядро помечает неполноту, а ассистент переспрашивает.
    """
    low = text.lower()
    cue = any(w in low for w in ("телефон", "номер", "позвон", "звоните",
                                 "свяж", "связат", "whatsapp", "вотсап"))
    for raw in PHONE_RE.findall(text):
        digits = [c for c in raw if c.isdigit()]
        if len(digits) < 6:
            continue
        # С «плюсом» номер записан международно: код страны плюс национальный,
        # для России это одиннадцать цифр. «+38 222 222 22» даёт десять —
        # по виду номер, по факту набрать нельзя.
        ok = (11,) if raw.startswith("+") else (10, 11)
        if len(digits) in ok:
            return None
        # Бюджет «2 500 000» и дата «2026-09-18» тоже похожи на номер.
        # Без явного признака контакта в сообщении ядро молчит: ложная
        # просьба «уточните номер» в разговоре о цене хуже, чем пропуск.
        if not (raw.startswith("+") or cue):
            continue
        return ("НОМЕР ТЕЛЕФОНА: номер в сообщении выглядит неполным "
                f"({len(digits)} цифр). Переспросить его целиком; "
                "на неполный номер не ссылаться и не обещать по нему связь.")
    return None


def build_context(role: str, now: datetime, owner: str, stale: bool,
                  hours_noted: bool = False, phone: str | None = None,
                  plate: str | None = None) -> str:
    dept = ROLES[role]["department"]
    hours, is_open = working_hours(dept, now)
    lines = [
        f"ТЕКУЩЕЕ ВРЕМЯ: {now:%Y-%m-%d %H:%M} ({['понедельник','вторник','среда','четверг','пятница','суббота','воскресенье'][now.weekday()]})",
        f"ПРОФИЛЬНЫЙ ОТДЕЛ: {dept}, часы работы сегодня: {hours}",
        f"СЕЙЧАС {'РАБОЧЕЕ' if is_open else 'НЕРАБОЧЕЕ'} время отдела",
        f"владение: {owner}",
        "АКТУАЛЬНОСТЬ ДАННЫХ: " + ("данные стока устарели более чем на 4 часа — "
                                    "по наличию и статусу не отвечать"
                                    if stale else "данные свежие, обновлены минуты назад"),
    ]
    # Оговорка про нерабочее время нужна один раз. Повторённая в каждом
    # сообщении, она вытесняет ответ по существу — дефект живого теста.
    if not is_open:
        # Формулировка флага пересказуема: ассистент однажды написал клиенту
        # «поэтому уточню это один раз». Значение читается, а не цитируется.
        lines.append("ОГОВОРКА О НЕРАБОЧЕМ ВРЕМЕНИ: "
                     + ("уже звучала" if hours_noted else "ещё не звучала"))
    if phone:
        lines.append(phone)
    if plate:
        lines.append(plate)
    return "\n".join(lines)


# Статусы стока на языке клиента: in_stock в ответе клиенту — утечка служебного кода.
STATUS_RU = {
    "in_stock": "в наличии на площадке",
    "reserved": "в резерве",
    "prep": "на предпродажной подготовке",
    "in_transit": "в пути",
    "sold": "продан",
}


def id_names() -> dict[str, str]:
    """Служебный код → человеческая подпись.

    Записи базы знаний ссылаются на ответственных как `emp-01`, акции — друг
    на друга как `ACT-109`. Код, попавший в блок ЗНАНИЯ, рано или поздно
    появляется в ответе клиенту: в прогоне этапа 3 ассистент написал
    «передам ответственному (emp-01)». Надёжнее не показывать код модели,
    чем требовать от неё молчания о нём.
    """
    # Ключи дежурств из working_hours.json: в живом тесте ассистент написал
    # клиенту «поставлю в дежурную очередь on_call.quality».
    table: dict[str, str] = {
        "on_call.quality": "дежурный менеджер по качеству",
        "on_call.service": "дежурный мастер сервиса",
        "on_call.safety": "дежурный по вопросам безопасности",
        "sales_new": "отдел продаж новых автомобилей",
        "sales_used": "отдел продаж автомобилей с пробегом",
    }
    try:
        for p in load_json("staff.json")["people"]:
            table[p["id"]] = f"{p['name']}, {p.get('role', '')}".strip(", ")
    except Exception:
        pass
    try:
        for a in load_json("promotions.json")["promotions"]:
            table[a["id"]] = f"«{a['name']}»"
    except Exception:
        pass
    return table


ID_NAMES = id_names()
ID_RE = re.compile(r"\bon_call\.\w+|\b(emp-\d+|ACT-\d+|sales_new|sales_used)\b", re.I)


def humanize_ids(text: str) -> str:
    return ID_RE.sub(lambda m: ID_NAMES.get(m.group(0).lower(),
                                            ID_NAMES.get(m.group(0).upper(), m.group(0))), text)


def build_knowledge(records: list[dict], vehicles: list[dict],
                    owner: dict | None = None, no_such_id: bool = False,
                    promos: list[dict] | None = None) -> str:
    parts = []
    if no_such_id:
        parts.append("ПРОВЕРКА ИДЕНТИФИКАТОРА: такого VIN или госномера в базе нет. "
                     "Сообщить об этом прямо и не делать вид, что карточка поднята.")
    if owner:
        v, c = owner["vehicle"], owner["customer"]
        rows = [f"Карточка владельца: {c.get('name','—')}, {v['make']} {v['model']} {v['year']}, "
                f"VIN {v['vin']}, госномер {v.get('plate','—')}, пробег {v['mileage_km']} км, "
                f"куплен {v.get('purchased_at','—')}"]
        for o in owner["orders"]:
            rows.append(f"  наряд {o['id']}: {o['works']}, статус {o['status']}, "
                        f"открыт {o['opened_at']}, план/закрыт {o['closed_or_planned']}"
                        # Примечание наряда — служебная пометка мастера, а не
                        # сообщение клиенту. В ней встречается «страховой
                        # случай, не гарантия»: пересказанная дословно, такая
                        # запись превращается в вердикт по гарантии от лица
                        # ассистента, которого он выносить не вправе.
                        + (f", служебное примечание (клиенту не пересказывать, "
                           f"квалификацию случая не озвучивать): {o['note']}"
                           if o.get("note") else ""))
        parts.append(chr(10).join(rows))
    if promos:
        rows = ["Действующие акции (истёкшие не предлагать):"]
        for a in promos:
            rows.append(f"  {a['name']} до {a['to']}: {a.get('conditions','')}"
                        + (f", выгода {a['benefit_rub']} ₽" if a.get("benefit_rub") else "")
                        + (f", не сочетается с {a['not_combinable_with']}" if a.get("not_combinable_with") else ""))
        parts.append(chr(10).join(rows))
    if not records and not vehicles and not parts:
        return "(записей по теме не найдено)"
    for rec in records:
        parts.append(f"[{rec.get('id')}] {rec.get('body','')}")
    if vehicles:
        rows = ["Позиции из стока:"]
        for v in vehicles:
            rows.append(f"  VIN {v['vin']} · {v['make']} {v['model']} {v['trim']} {v['year']} · "
                        f"{v['price_rub']} ₽ · {STATUS_RU.get(v['status'], v['status'])}"
                        + (f" до {v['reserved_until']}" if v['reserved_until'] else "")
                        + (f" · продан {v['sold_at']}" if v['sold_at'] else "")
                        + (f" · пробег {v['mileage_km']} км" if v['mileage_km'] != "0" else ""))
        parts.append("\n".join(rows))
    return humanize_ids("\n\n".join(parts))


def system_prompt(role: str) -> str:
    core = (PROMPTS / "_core.md").read_text(encoding="utf-8")
    part = (PROMPTS / ROLES[role]["file"]).read_text(encoding="utf-8")
    return core + "\n\n" + part


def known_sums() -> set[int]:
    """Суммы, которые ассистенту разрешено называть.

    Всё, что встречается в фикстурах и текстах базы знаний. Любое другое
    число рядом со словом о деньгах — либо цифра собеседника, подхваченная
    из его же реплики, либо выдумка. Первое опаснее: повторённая сумма
    скидки или оклада читается как принятая к рассмотрению.
    """
    numbers: set[int] = set()
    for path in list(KB.rglob("*.json")) + list(KB.rglob("*.md")) + list(KB.rglob("*.csv")):
        for raw in MONEY_RE.findall(path.read_text(encoding="utf-8")):
            numbers.add(int(re.sub(r"\D", "", raw)))
    return numbers


MONEY_RE = re.compile(NUM4)
MONEY_WORD = re.compile(r"скидк\w+|оклад\w*|зарплат\w+|доход\w*|компенсац\w+|выплат\w+|"
                        r"выгод\w+|сумм\w+|цен[ауые]\w*", re.I)
# Слова, которыми обращение отправляют дальше. Сумма рядом с ними перестаёт
# быть цитатой собеседника и превращается в предмет рассмотрения.
RELAY_WORD = re.compile(r"переда\w+|согласов\w+|согласу\w+|рассмотр\w+|одобр\w+|"
                        r"утверд\w+|запрос на|заявк\w+", re.I)


def foreign_sums(text: str) -> list[int]:
    """Суммы, которых нет в базе, отправленные «на рассмотрение».

    Назвать сумму собеседника, отказывая, — «запрошенная сумма выше вилки» —
    допустимо и честнее умолчания. Недопустимо другое: пересказать её как
    предмет согласования. «Передам запрос на скидку в такую-то сумму»
    читается как заявка, принятая в работу, хотя никакой заявки нет.
    """
    found = []
    for sentence in re.split(r"[.!?\n]", text):
        if not (MONEY_WORD.search(sentence) and RELAY_WORD.search(sentence)):
            continue
        for raw in MONEY_RE.findall(sentence):
            value = int(re.sub(r"\D", "", raw))
            if value >= 1000 and value not in KNOWN_SUMS:
                found.append(value)
    return found


KNOWN_SUMS = known_sums()


# «Хотите, чтобы я передал запрос рекрутеру?» — форма прошедшего времени,
# но смысл будущий: это предложение, а не отчёт о сделанном. Придаточное
# с «чтобы» снимает запрет на совершённое действие.
SUBJUNCTIVE = re.compile(r"чтобы[^.?!]{0,120}$", re.I)


PRICE_SAID = re.compile(rf"{NUM4}\s*(?:₽|руб)", re.I)
OFFER_NOTE = ("Информация не является публичной офертой; "
              "окончательные условия фиксируются договором.")


def ensure_offer_note(text: str) -> str:
    """Дописывает оговорку об оферте, если в ответе названа цена, а оговорки нет.

    Требование каркаса модель выполняет почти всегда — «почти» здесь и есть
    проблема: в одном прогоне из трёх цена уходила клиенту без оговорки.
    Формальность, которую нельзя пропускать, держится кодом, а не старанием
    модели. Дописанное предложение ничего не скрывает и не меняет смысла
    ответа — оно добавляет верное утверждение, которое обязано там быть.
    """
    if not PRICE_SAID.search(text) or "оферт" in text.lower():
        return text
    separator = " " if text.endswith((".", "!", "?", "»")) else ". "
    return text + separator + OFFER_NOTE


def validate_outgoing(text: str) -> list[str]:
    found = []
    for kind, pat in STOP_PATTERNS.items():
        for match in pat.finditer(text):
            # Проверяется каждое совпадение: одно предложение может быть
            # предложением услуги, а следующее за ним — отчётом о сделанном.
            if kind == "ложное действие" and SUBJUNCTIVE.search(text[:match.start()]):
                continue
            found.append(kind)
            break
    if foreign_sums(text):
        found.append("сумма не из базы знаний")
    return found


def answer(role: str, history: list[dict], now: datetime | None = None,
           owner: str = "bot_owned", stale: bool = False,
           model: str | None = None, extra_context: str = "", cache=None,
           pii: bool = True, pii_map: dict | None = None) -> dict:
    """История — список {'role': 'user'|'assistant', 'content': str}.

    `cache` — хранилище с методами `cache_get` / `cache_put` (ADR-0017) либо
    None. По умолчанию кэша нет: прогон эталонных диалогов обязан каждый раз
    спрашивать модель, иначе приёмка начнёт подтверждать сама себя.

    `pii` — шлюз персональных данных (ADR-0002). Включён по умолчанию:
    отключение должно быть осознанным действием отладки, а не забытой
    настройкой. `pii_map` — уже известные плейсхолдеры диалога, чтобы один
    и тот же телефон в третьей реплике назывался так же, как в первой.
    """
    from openai import OpenAI

    model = model or DEFAULT_MODEL
    now = now or datetime.now()
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")

    if owner != "bot_owned":
        return stamp({"text": "", "mode": "МОЛЧАНИЕ", "records": [], "violations": [],
                      "note": "диалогом владеет сотрудник — ассистент не отвечает"}, role)

    records = retrieve(last_user, load_kb(role))
    vehicles = find_vehicles(last_user) if role == "sales" else []

    # Машина клиента и её наряды — для сервиса; сток тут не поможет.
    # Имя owner_card, а не owner: owner — это владение диалогом. Его затирание
    # отправляло в КОНТЕКСТ «владение: None», и ассистент по инварианту молчал.
    owner_card = find_owner_vehicle(last_user) if role == "service" else None
    no_such_id = bool(role == "service" and not owner_card and looks_like_vehicle_id(last_user))
    promos = active_promotions(now.strftime("%Y-%m-%d")) if role == "sales" else None
    # Оговорка засчитывается сказанной, как только ассистент её произнёс.
    HOURS_MARKS = ("нерабоч", "не работает", "в очередь", "рабочий интервал",
                   "рабочие часы", "рабочее время")
    hours_noted = any(m["role"] == "assistant"
                      and any(w in m["content"].lower() for w in HOURS_MARKS)
                      for m in history)
    context = build_context(role, now, owner, stale,
                            hours_noted=hours_noted, phone=phone_note(last_user),
                            plate=plate_note(last_user))
    if extra_context:
        context += chr(10) + extra_context

    # Кэш ответов на обезличенные вопросы (ADR-0017). Не кэшируется ничего,
    # что зависит от человека: карточка владельца, любой вопрос с телефоном,
    # VIN, госномером или почтой. Признаки контекста, меняющие ответ, входят
    # в ключ — иначе вечерний ответ про рабочие часы выдавался бы утром.
    key = None
    if cache is not None and owner == "bot_owned" and not stale and not owner_card:
        from store import cache_key, pseudonymize
        _, found_pii = pseudonymize(last_user)
        if not found_pii and len(history) <= 2:
            dept_open = "открыт" if working_hours(ROLES[role]["department"], now)[1] else "закрыт"
            marks = f"{now:%Y-%m-%d}|{dept_open}|{'напоминали' if hours_noted else 'впервые'}"
            key = cache_key(role, last_user, PROMPT_VERSIONS[role], KB_VERSION, marks)
            hit = cache.cache_get(key)
            if hit:
                return stamp({"text": hit["text"], "mode": hit["mode"],
                              "records": hit["records"], "violations": [],
                              "cached": True}, role)
    blocks = (f"КОНТЕКСТ:\n{context}\n\n"
              f"ЗНАНИЯ:\n{build_knowledge(records, vehicles, owner_card, no_such_id, promos)}")

    # Шлюз персональных данных (ADR-0002). Закрывает всё, что видит модель:
    # реплики собеседника, карточку владельца в блоке ЗНАНИЯ, контекст.
    # Поиск по фикстурам уже отработал выше — он локальный, и ему нужны
    # настоящие VIN и номера; за границу процесса уходят уже плейсхолдеры.
    vault = Vault(pii_map or {})
    if owner_card:
        # Имя человека регулярным выражением не поймать, но карточку ядро
        # собрало само и знает, где имя, а где марка автомобиля.
        vault.add("NAME", owner_card["customer"].get("name", ""))
        vault.add("PHONE", owner_card["customer"].get("phone", ""))
    if pii:
        blocks = vault.hide(blocks)
        history = [{"role": m["role"], "content": vault.hide(m["content"])} for m in history]

    messages = [{"role": "system", "content": system_prompt(role)},
                {"role": "system", "content": blocks}] + history

    client = OpenAI()

    # Модель изредка тратит весь бюджет на размышления и возвращает пустой текст.
    # Молчание в ответ клиенту неотличимо от поломки, поэтому одна повторная
    # попытка, а затем честная деградация до передачи человеку (уровень L3).
    text = ""
    for attempt in range(2):
        try:
            resp = client.chat.completions.create(model=model, messages=messages)
            text = (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            print(f"[ядро] ошибка вызова модели: {exc}")
            text = ""
        if text:
            break

    if not text:
        return stamp({"text": "Не получается ответить прямо сейчас — передаю обращение "
                              "менеджеру, с вами свяжутся в рабочее время.",
                      "mode": "ЭСКАЛАЦИЯ (пустой ответ модели)",
                      "records": [r.get("id") for r in records], "violations": []}, role)

    # Обратный путь шлюза: клиент видит свой VIN, а не «{{VIN_1}}».
    # Валидатор работает уже по развёрнутому тексту — по тому самому, который
    # уйдёт человеку.
    if pii:
        text = vault.show(text)
        broken = vault.leftovers(text)
        if broken:
            # Модель испортила плейсхолдер: вернула «{{VIN_2}}», которого нет
            # в словаре. Отправить скобки клиенту хуже, чем передать человеку.
            print(f"[шлюз ПДн] неизвестные плейсхолдеры в ответе: {', '.join(broken)}")
            return stamp({"text": "Передаю обращение профильному специалисту — "
                                  "он свяжется с вами.",
                          "mode": "ЭСКАЛАЦИЯ (испорченный плейсхолдер)",
                          "records": [r.get("id") for r in records],
                          "violations": [], "blocked": text}, role)

    text = ensure_offer_note(text)
    violations = validate_outgoing(text)
    if violations:
        return stamp({"text": "Передаю обращение профильному специалисту — он свяжется с вами.",
                      "mode": "ЭСКАЛАЦИЯ (валидатор)",
                      "records": [r.get("id") for r in records],
                      "violations": violations, "blocked": text}, role)

    result = stamp({"text": text, "mode": "ОТВЕТ", "records": [r.get("id") for r in records],
                    "violations": []}, role)

    # Срок жизни привязан к источнику: ответ, опирающийся на сток, живёт не
    # дольше интервала обновления фида. Справка по регламенту живёт до смены
    # версии знаний — она и так в ключе, так что сутки здесь только верхняя
    # граница на случай правки фикстур без пересборки процесса.
    if key:
        cache.cache_put(key, role, last_user, result, 600 if vehicles else 86400)
    return result
