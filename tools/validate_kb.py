"""Валидатор базы знаний (этап 1).

Проверяет то, что объявлено критерием приёмки этапа: у каждой записи есть
владелец и дата, источники существуют, все девять триггеров эскалации описаны
с адресатом и сроком, объём FAQ по отделам соответствует плану.

Запуск:  python tools/validate_kb.py
Код возврата 1 — есть нарушения.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "docs" / "knowledge-base"
TODAY = date(2026, 9, 11)

REQUIRED_FIELDS = ["id", "type", "department", "owner", "updated_at",
                   "valid_until", "sources", "tags", "assistants", "escalate_to"]
TYPE_PREFIX = {"faq": "FAQ", "case": "CASE", "script": "SCRIPT", "escalation": "ESC"}
DEPARTMENTS = {"sales_new", "sales_used", "service", "parts", "credit", "hr", "quality"}
ASSISTANTS = {"sales", "hr", "service"}
MIN_FAQ_PER_ASSISTANT = 20

problems: list[str] = []
checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        problems.append(message)


def parse_scalar(raw: str):
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [x.strip() for x in inner.split(",") if x.strip()] if inner else []
    return raw


def parse_records(path: Path) -> list[dict]:
    """Достаёт блоки метаданных: строка '---', затем 'id:', затем до '---'."""
    lines = path.read_text(encoding="utf-8").splitlines()
    records, i = [], 0
    while i < len(lines):
        if lines[i].strip() == "---" and i + 1 < len(lines) and lines[i + 1].startswith("id:"):
            block, i = {}, i + 1
            while i < len(lines) and lines[i].strip() != "---":
                if ":" in lines[i] and not lines[i].startswith(" "):
                    key, _, value = lines[i].partition(":")
                    block[key.strip()] = parse_scalar(value)
                elif lines[i].lstrip().startswith("- "):
                    block.setdefault("sources", [])
                    if isinstance(block["sources"], list):
                        block["sources"].append(lines[i].lstrip()[2:].strip())
                i += 1
            block["_file"] = str(path.relative_to(KB))
            records.append(block)
        i += 1
    return records


def main() -> int:
    staff = json.loads((KB / "fixtures" / "staff.json").read_text(encoding="utf-8"))
    staff_ids = {p["id"] for p in staff["people"]} | {"on_call"}

    files = sorted(KB.glob("faq/*.md")) + sorted(KB.glob("cases/*.md")) \
        + sorted(KB.glob("scripts/*.md")) + [KB / "escalations.md"]

    records: list[dict] = []
    for path in files:
        check(path.exists(), f"нет файла {path.name}")
        if path.exists():
            records.extend(parse_records(path))

    check(bool(records), "не найдено ни одной записи")
    seen_ids: set[str] = set()
    faq_per_assistant: dict[str, int] = {a: 0 for a in ASSISTANTS}

    for rec in records:
        rid = rec.get("id", "?")
        where = f"{rec.get('_file')} · {rid}"

        for field in REQUIRED_FIELDS:
            check(field in rec and rec[field] not in ("", [], None),
                  f"{where}: нет поля {field}")

        check(rid not in seen_ids, f"{where}: идентификатор повторяется")
        seen_ids.add(rid)

        rtype = rec.get("type", "")
        check(rtype in TYPE_PREFIX, f"{where}: неизвестный type «{rtype}»")
        if rtype in TYPE_PREFIX:
            check(rid.startswith(TYPE_PREFIX[rtype]),
                  f"{where}: префикс идентификатора не совпадает с типом {rtype}")

        check(rec.get("department") in DEPARTMENTS,
              f"{where}: неизвестный department «{rec.get('department')}»")
        check(rec.get("owner") in staff_ids, f"{where}: владелец не найден в staff.json")
        check(rec.get("escalate_to") in staff_ids,
              f"{where}: адресат эскалации не найден в staff.json")

        for assistant in rec.get("assistants", []):
            check(assistant in ASSISTANTS, f"{where}: неизвестный ассистент «{assistant}»")
            if rtype == "faq":
                faq_per_assistant[assistant] = faq_per_assistant.get(assistant, 0) + 1

        for src in rec.get("sources", []):
            check((KB / src).exists(), f"{where}: источник {src} не существует")

        for field in ("updated_at", "valid_until"):
            value = str(rec.get(field, ""))
            ok = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
            check(ok, f"{where}: поле {field} не дата ISO («{value}»)")
            if ok and field == "valid_until":
                check(date.fromisoformat(value) > TODAY,
                      f"{where}: срок жизни записи уже истёк ({value})")

    for assistant, count in faq_per_assistant.items():
        check(count >= MIN_FAQ_PER_ASSISTANT,
              f"ассистент «{assistant}»: записей FAQ {count}, нужно не меньше {MIN_FAQ_PER_ASSISTANT}")

    esc = [r for r in records if r.get("type") == "escalation"]
    check(len(esc) == 9, f"триггеров эскалации {len(esc)}, в карте объявлено 9")
    for n in range(1, 10):
        check(any(r.get("id") == f"ESC-{n:03d}" for r in esc), f"нет записи ESC-{n:03d}")

    esc_text = (KB / "escalations.md").read_text(encoding="utf-8")
    for n in range(1, 10):
        check(f"| ESC-{n:03d} |" in esc_text,
              f"ESC-{n:03d} отсутствует в сводной таблице с адресатом и сроком")

    stop = KB / "stop-list.md"
    check(stop.exists(), "нет файла stop-list.md")
    if stop.exists():
        text = stop.read_text(encoding="utf-8")
        for section in ["Гарантия", "Сроки", "Цена и скидка", "Оценка трейд-ин",
                        "Кредит и банк", "Компенсации", "Диагноз", "Персональные данные"]:
            check(section in text, f"stop-list.md: нет раздела «{section}»")

    print(f"Записей разобрано: {len(records)}")
    print(f"  FAQ по ассистентам: " + ", ".join(f"{k} — {v}" for k, v in sorted(faq_per_assistant.items())))
    print(f"Проверок выполнено: {checks}")
    if problems:
        print(f"\nНарушений: {len(problems)}")
        for p in problems:
            print(f"  · {p}")
        return 1
    print("Нарушений нет. База знаний связна, критерий этапа 1 выполнен.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
