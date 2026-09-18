"""Минимальный горячий контур: сборка промпта, поиск по базе знаний, вызов модели, валидатор.

Учебный срез ядра из ADR-0002: системный промпт = каркас + роль, факты приходят
отдельным блоком ЗНАНИЯ, обстановка — блоком КОНТЕКСТ, исходящее сообщение
проходит детерминированный валидатор стоп-листа. PII-шлюз и база диалогов —
этап 4, здесь их нет.

Используется из tools/chat.py и tools/run_golden.py.
"""

from __future__ import annotations

import os
import csv
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "docs" / "knowledge-base"
FIX = KB / "fixtures"
PROMPTS = ROOT / "docs" / "prompts"


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

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
STOPWORDS = {"и", "в", "на", "с", "у", "по", "за", "не", "что", "как", "а", "но", "мне",
             "я", "вы", "мы", "это", "для", "от", "до", "ли", "же", "бы", "есть", "быть"}

# --- Стоп-лист в исполняемом виде (docs/knowledge-base/stop-list.md) ---
STOP_PATTERNS = {
    "решение по гарантии": re.compile(r"(это|у вас|ваш)\s+гарантийн\w+|не\s+гарантийн\w+|"
                                      r"гарантии\s+(заменим|отремонтируем|сделаем|бесплатно)", re.I),
    "обещание срока": re.compile(r"(будет\s+гото\w+|сделаем|успеем|заберёте)\s+(к|в|до)\s+"
                                 r"(понедельник\w*|вторник\w*|сред\w+|четверг\w*|пятниц\w+|"
                                 r"суббот\w+|воскресень\w+|завтра|послезавтра)", re.I),
    "сумма скидки": re.compile(r"скидк\w+\s+(в\s+)?\d{4,}|\d{4,}\s*(₽|руб\w*)\s+скидк|"
                               r"(согласую|выбью|договорюсь)\s+.{0,20}скидк", re.I),
    "итоговая цена": re.compile(r"(итогов\w+|окончательн\w+|финальн\w+)\s+цена", re.I),
    "сумма выкупа": re.compile(r"(дад\w+|оцен\w+|выкуп\w+)\s+.{0,25}\d{6,}|"
                               r"(ваша\s+машина|ваш\s+автомобиль)\s+стоит\s+\d", re.I),
    "обещание банка": re.compile(r"(одобр\w+)\s+(вам|вас|точно)|банк\s+одобрит", re.I),
    "компенсация": re.compile(r"компенсир\w+|верн[ёе]м\s+(деньги|стоимость|средства)", re.I),
    "диагноз": re.compile(r"\bэто\s+(амортизатор\w*|насос\w*|подшипник\w*|ступиц\w+|"
                          r"сцеплени\w+|генератор\w*|стартер\w*)", re.I),
    "запрос документов": re.compile(r"(пришлите|отправьте|скиньте)\s+.{0,20}"
                                    r"(паспорт\w*|снилс|инн|карт\w+|скан\w*)", re.I),
    "абсолютное обещание": re.compile(r"\b(гарантирую|обещаю|100\s*%|точно\s+будет|"
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
                  hours_noted: bool = False, phone: str | None = None) -> str:
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
        lines.append("ОГОВОРКА О ВРЕМЕНИ: "
                     + ("уже сказана в этом диалоге — не повторять"
                        if hours_noted else "ещё не звучала — сказать один раз"))
    if phone:
        lines.append(phone)
    return "\n".join(lines)


# Статусы стока на языке клиента: in_stock в ответе клиенту — утечка служебного кода.
STATUS_RU = {
    "in_stock": "в наличии на площадке",
    "reserved": "в резерве",
    "prep": "на предпродажной подготовке",
    "in_transit": "в пути",
    "sold": "продан",
}


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
                        + (f", примечание: {o['note']}" if o.get("note") else ""))
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
    return "\n\n".join(parts)


def system_prompt(role: str) -> str:
    core = (PROMPTS / "_core.md").read_text(encoding="utf-8")
    part = (PROMPTS / ROLES[role]["file"]).read_text(encoding="utf-8")
    return core + "\n\n" + part


def validate_outgoing(text: str) -> list[str]:
    return [kind for kind, pat in STOP_PATTERNS.items() if pat.search(text)]


def answer(role: str, history: list[dict], now: datetime | None = None,
           owner: str = "bot_owned", stale: bool = False,
           model: str | None = None, extra_context: str = "") -> dict:
    """История — список {'role': 'user'|'assistant', 'content': str}."""
    from openai import OpenAI

    model = model or DEFAULT_MODEL
    now = now or datetime.now()
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")

    if owner != "bot_owned":
        return {"text": "", "mode": "МОЛЧАНИЕ", "records": [], "violations": [],
                "note": "диалогом владеет сотрудник — ассистент не отвечает"}

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
                            hours_noted=hours_noted, phone=phone_note(last_user))
    if extra_context:
        context += chr(10) + extra_context
    blocks = (f"КОНТЕКСТ:\n{context}\n\n"
              f"ЗНАНИЯ:\n{build_knowledge(records, vehicles, owner_card, no_such_id, promos)}")

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
        return {"text": "Не получается ответить прямо сейчас — передаю обращение "
                        "менеджеру, с вами свяжутся в рабочее время.",
                "mode": "ЭСКАЛАЦИЯ (пустой ответ модели)",
                "records": [r.get("id") for r in records], "violations": []}

    violations = validate_outgoing(text)
    if violations:
        return {"text": "Передаю обращение профильному специалисту — он свяжется с вами.",
                "mode": "ЭСКАЛАЦИЯ (валидатор)", "records": [r.get("id") for r in records],
                "violations": violations, "blocked": text}

    return {"text": text, "mode": "ОТВЕТ", "records": [r.get("id") for r in records],
            "violations": []}
