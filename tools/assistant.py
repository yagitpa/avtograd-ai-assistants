"""Минимальный горячий контур: сборка промпта, поиск по базе знаний, вызов модели, валидатор.

Учебный срез ядра из ADR-0002: системный промпт = каркас + роль, факты приходят
отдельным блоком ЗНАНИЯ, обстановка — блоком КОНТЕКСТ, исходящее сообщение
проходит детерминированный валидатор стоп-листа. PII-шлюз и база диалогов —
этап 4, здесь их нет.

Используется из tools/chat.py и tools/run_golden.py.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "docs" / "knowledge-base"
FIX = KB / "fixtures"
PROMPTS = ROOT / "docs" / "prompts"

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


def find_vehicles(query: str, limit: int = 4) -> list[dict]:
    rows = load_stock()
    q = query.lower()
    hits = []
    for r in rows:
        if r["vin"].lower() in q or r["model"].lower() in q and r["make"].lower() in q \
                or r["model"].lower() in q:
            hits.append(r)
    return hits[:limit]


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


def build_context(role: str, now: datetime, owner: str, stale: bool) -> str:
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
    return "\n".join(lines)


def build_knowledge(records: list[dict], vehicles: list[dict]) -> str:
    if not records and not vehicles:
        return "(записей по теме не найдено)"
    parts = []
    for rec in records:
        parts.append(f"[{rec.get('id')}] {rec.get('body','')}")
    if vehicles:
        rows = ["Позиции из стока:"]
        for v in vehicles:
            rows.append(f"  VIN {v['vin']} · {v['make']} {v['model']} {v['trim']} {v['year']} · "
                        f"{v['price_rub']} ₽ · статус {v['status']}"
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
           model: str = "gpt-5-mini") -> dict:
    """История — список {'role': 'user'|'assistant', 'content': str}."""
    from openai import OpenAI

    now = now or datetime.now()
    last_user = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")

    if owner != "bot_owned":
        return {"text": "", "mode": "МОЛЧАНИЕ", "records": [], "violations": [],
                "note": "диалогом владеет сотрудник — ассистент не отвечает"}

    records = retrieve(last_user, load_kb(role))
    vehicles = find_vehicles(last_user) if role == "sales" else []
    blocks = (f"КОНТЕКСТ:\n{build_context(role, now, owner, stale)}\n\n"
              f"ЗНАНИЯ:\n{build_knowledge(records, vehicles)}")

    messages = [{"role": "system", "content": system_prompt(role)},
                {"role": "system", "content": blocks}] + history

    client = OpenAI()
    resp = client.chat.completions.create(model=model, messages=messages)
    text = (resp.choices[0].message.content or "").strip()

    violations = validate_outgoing(text)
    if violations:
        return {"text": "Передаю обращение профильному специалисту — он свяжется с вами.",
                "mode": "ЭСКАЛАЦИЯ (валидатор)", "records": [r.get("id") for r in records],
                "violations": violations, "blocked": text}

    return {"text": text, "mode": "ОТВЕТ", "records": [r.get("id") for r in records],
            "violations": []}
