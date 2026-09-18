"""Валидатор системных промптов (этап 2).

Проверяет критерий приёмки этапа: у каждой роли есть все шесть разделов
из ТЗ, протокол передачи человеку и поведение вне рабочих часов упомянуты
в каждой роли (сам протокол живёт в общем каркасе и подставляется в
итоговый промпт первым), в тексте роли нет утёкших числовых фактов —
цен, вилок дохода, номеров телефонов, дат, — которые обязаны приходить
из базы знаний, а не из конфигурации.

Запуск:  python tools/validate_prompts.py
Код возврата 1 — есть нарушения.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "docs" / "prompts"

CORE_FILE = "_core.md"
ROLE_FILES = ["sales.md", "service.md", "hr.md"]

REQUIRED_SECTIONS = [
    "Название, цель и задачи",
    "Приветствие и начало диалога",
    "Роль, стиль, тон",
    "Пошаговые сценарии",
    "Ограничения",
    "Типовые вопросы и ответы",
]

RULE_LABELS = ["ИНВАРИАНТ:", "ТРЕБОВАНИЕ:", "СПРОСИТЬ:", "ПО УМОЛЧАНИЮ:",
               "РЕКОМЕНДАЦИЯ:", "ФАКТ СРЕДЫ:", "ИСКЛЮЧЕНИЕ:"]

HANDOFF_MARKERS = ["ЭСКАЛАЦИЯ", "эскалаци"]
HOURS_MARKERS = ["рабочих час", "рабочие час", "рабочих интервал"]

# Числа, допустимые в тексте роли: ссылки на ADR и нумерация пунктов/сценариев.
ALLOWED_NUMBER_CONTEXT = re.compile(
    r"ADR-\d{4}"          # ссылка на журнал решений
    r"|^\d+\.\s"          # нумерованный пункт списка
    r"|Сценарий [А-Я]\."  # заголовок сценария
)
# Явные утечки фактов: деньги, длинные числа, номера телефонов, календарные даты.
LEAK_PATTERNS = {
    "сумма в рублях": re.compile(r"\d[\d\s]*\s*(?:₽|руб)"),
    "телефон": re.compile(r"\+7[\s\d\-()]{9,}"),
    "дата ISO": re.compile(r"\b20\d\d-\d\d-\d\d\b"),
    "число ≥ 4 цифр": re.compile(r"(?<!ADR-)\b\d{4,}\b"),
}

problems: list[str] = []
checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        problems.append(message)


def strip_code_and_examples(text: str) -> list[str]:
    """Возвращает строки текста роли вне заголовков и таблицы паспорта,
    где числа не являются фактами о дилере."""
    lines = text.splitlines()
    out = []
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and ("Поле" in stripped or "---" in stripped):
            in_table = True
        if in_table and not stripped.startswith("|"):
            in_table = False
        if in_table:
            continue
        out.append(line)
    return out


def main() -> int:
    # Версия промпта должна быть описана в журнале правок (ADR-0016).
    # Проверка живёт здесь, а не отдельной командой: правило, которое можно
    # забыть запустить, не действует.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from versions import changelog_versions, prompt_versions  # noqa: E402

    recorded = changelog_versions()
    for role, version in prompt_versions().items():
        check(version in recorded,
              f"промпт «{role}» версии {version} не описан в docs/prompts/CHANGELOG.md — "
              "что изменилось, почему и какой прогон это подтвердил")

    core_path = PROMPTS / CORE_FILE
    check(core_path.exists(), f"нет общего каркаса {CORE_FILE}")
    core_text = core_path.read_text(encoding="utf-8") if core_path.exists() else ""

    for label in ["ИНВАРИАНТ:", "ТРЕБОВАНИЕ:", "ПО УМОЛЧАНИЮ:"]:  # СПРОСИТЬ живёт в ролях — там известен адресат
        check(label in core_text, f"{CORE_FILE}: нет ни одного правила с меткой {label}")

    check(any(m in core_text for m in HANDOFF_MARKERS),
          f"{CORE_FILE}: нет протокола передачи человеку")
    check(any(m in core_text for m in HOURS_MARKERS),
          f"{CORE_FILE}: нет правил о рабочих часах")

    for role_file in ROLE_FILES:
        path = PROMPTS / role_file
        check(path.exists(), f"нет файла роли {role_file}")
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")

        for section in REQUIRED_SECTIONS:
            check(f"## " in text and section in text,
                  f"{role_file}: нет раздела «{section}»")

        check(any(m in text for m in HANDOFF_MARKERS),
              f"{role_file}: протокол передачи человеку не упомянут в роли")
        check(any(m in text for m in HOURS_MARKERS),
              f"{role_file}: поведение вне рабочих часов не упомянуто в роли")

        check(any(lbl in text for lbl in ("ИНВАРИАНТ:", "ТРЕБОВАНИЕ:")),
              f"{role_file}: нет ни одного обязательного правила (ИНВАРИАНТ/ТРЕБОВАНИЕ)")

        for lineno, line in enumerate(strip_code_and_examples(text), start=1):
            probe = ALLOWED_NUMBER_CONTEXT.sub("", line)
            for kind, pattern in LEAK_PATTERNS.items():
                m = pattern.search(probe)
                check(m is None,
                      f"{role_file}:{lineno}: похоже на утёкший факт ({kind}): «{line.strip()[:80]}»")

    print(f"Файлов проверено: {1 + len(ROLE_FILES)}")
    print(f"Проверок выполнено: {checks}")
    if problems:
        print(f"\nНарушений: {len(problems)}")
        for p in problems:
            print(f"  · {p}")
        return 1
    print("Нарушений нет. Промпты структурно полны, критерий этапа 2 выполнен.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
