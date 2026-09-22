"""Сборка итогового документа по ТЗ: один файл с оглавлением.

Документ собирается из исходников, а не пишется руками: промпты, база знаний
и расшифровки диалогов живут в своих файлах, и любая их правка попадает в
документ следующей сборкой. Руками отредактированный документ разошёлся бы
с репозиторием в первый же день.

Запуск:
    python tools/build_deliverable.py

Результат: docs/deliverable/avtograd-assistants.md — файл для импорта в
Google Docs (Файл → Импорт → выбрать .md; заголовки станут структурой,
оглавление вставляется через Вставка → Оглавление).
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KB = ROOT / "docs" / "knowledge-base"
PROMPTS = ROOT / "docs" / "prompts"
GOLDEN = ROOT / "docs" / "golden"
OUT = ROOT / "docs" / "deliverable" / "avtograd-assistants.md"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

HEADING = re.compile(r"^(#{1,6})\s+(.*)$", re.M)


def nest(text: str, level: int) -> str:
    """Вкладывает файл в раздел документа, начиная его заголовки с `level`.

    Сдвиг считается от самого верхнего заголовка файла, а не задаётся числом:
    промпты размечены от второго уровня, а записи базы знаний — от первого,
    и общий сдвиг либо ломал вложенность промптов, либо поднимал каждый
    вопрос FAQ в оглавление наравне с разделами документа.

    Первый заголовок первого уровня выбрасывается: его место занимает
    заголовок раздела, иначе в документе окажутся два названия подряд.
    """
    text = text.strip()
    first = HEADING.search(text)
    if first and len(first.group(1)) == 1:
        text = text[first.end():].lstrip()
    found = [len(m.group(1)) for m in HEADING.finditer(text)]
    if not found:
        return text
    # База считается по первому заголовку, а не по самому верхнему: в файлах
    # типовых обращений вступительный «## Классификатор» стоит перед записями
    # первого уровня, и счёт по минимуму уводил его на два уровня вниз.
    # Ничто не поднимается выше level — иначе запись базы знаний окажется
    # в оглавлении наравне с разделом документа.
    shift = level - found[0]
    return HEADING.sub(lambda m: "#" * max(level, min(6, len(m.group(1)) + shift))
                       + " " + m.group(2), text)


def read(path: Path, level: int) -> str:
    return nest(path.read_text(encoding="utf-8"), level)


def checks() -> dict[str, str]:
    """Числа проверок берутся из самих валидаторов, а не из памяти автора."""
    result = {}
    for name in ("validate_fixtures", "validate_kb", "validate_prompts"):
        try:
            proc = subprocess.run([sys.executable, str(ROOT / "tools" / f"{name}.py")],
                                  capture_output=True, text=True, encoding="utf-8", timeout=120)
            found = re.search(r"Проверок выполнено: (\d+)", proc.stdout or "")
            result[name] = found.group(1) if found else "—"
        except Exception:
            result[name] = "—"
    return result


def golden_summary() -> str:
    report = (GOLDEN / "report.md").read_text(encoding="utf-8")
    table = re.search(r"\| Ассистент \|.*?\n\n", report, re.S)
    problems = "Расхождений нет." if "Нет. Ни в одном диалоге" in report else \
        "См. раздел «Расхождения» в docs/golden/report.md — прогон не зелёный."
    return (table.group(0).strip() if table else "") + "\n\n" + problems


def anchor(title: str) -> str:
    keep = re.sub(r"[^\w\s-]", "", title.lower())
    return "#" + re.sub(r"\s+", "-", keep.strip())


SECTIONS: list[tuple[str, str]] = []


def section(title: str, body: str) -> str:
    SECTIONS.append((title, anchor(title)))
    return f"## {title}\n\n{body.strip()}\n"


def build() -> str:
    today = datetime.now().strftime("%d.%m.%Y")
    counts = checks()
    # Документ, собранный из исходников, обязан называть, из каких именно:
    # иначе распечатанный комплект нечем сверить с репозиторием, а правка
    # промпта делает документ незаметно устаревшим.
    from versions import kb_version, prompt_version
    names = {"sales": "продажи", "service": "сервис", "hr": "кадры"}
    versions = " · ".join(f"{names[r]} `{prompt_version(r)}`"
                          for r in ("sales", "service", "hr"))
    kb = kb_version()

    parts = [section(
        "О документе",
        f"""Комплект нейроассистентов дилерского центра «АвтоГрад»: три системных промпта,
база знаний трёх отделов и протокол проверки.

**Учебный проект.** Дилерский центр, модели автомобилей, цены, вакансии, имена сотрудников,
телефоны и банки-партнёры вымышлены. Персональные данные реальных людей в проекте
не используются: телефоны в фикстурах взяты из нероутируемого диапазона, VIN начинаются
с `TEST`.

**Дата сборки:** {today}.
**Версии промптов:** {versions}; **база знаний:** `{kb}`.
**Исходники и история решений:** <https://github.com/yagitpa/avtograd-ai-assistants>

Документ собран автоматически из файлов репозитория (`tools/build_deliverable.py`).
Править нужно исходники, а не этот файл: следующая сборка затрёт ручные изменения.

### Состав комплекта

| Ассистент | Отдел | Что закрывает |
|---|---|---|
| Первый контакт | Продажи новых и с пробегом | Наличие, цены, комплектации, акции, тест-драйв, трейд-ин, кредит |
| АвтоГрад Сервис | Сервис и постпродажное обслуживание | Запись на ТО, статус ремонта, гарантия, стоимость работ |
| Кадровик | Отдел кадров | Вакансии, отклики, скрининг; внутренний контур — отпуск, справки, онбординг |

Все три работают на одном слое базы знаний: факт, исправленный в одной записи,
исправляется сразу у всех (ADR-0001).

### Как это устроено

Системный промпт каждого ассистента состоит из двух частей: общий каркас
и роль. Каркас задаёт инварианты, режимы и формат — он одинаков для всех трёх.
Роль задаёт цель, сценарии и границы конкретного отдела.

Факты в промпте не хранятся. Они приходят отдельными блоками:
`ЗНАНИЯ` — найденные записи базы знаний, `КОНТЕКСТ` — время, часы работы
отдела, состояние владения диалогом. Правило «нет записи — нет ответа»
проверяется автоматически: валидатор ищет в тексте промптов просочившиеся
цены и сроки.

Исходящее сообщение проходит детерминированный валидатор стоп-листа.
Красные линии — скидка, гарантийный случай, обещание срока, ложный отчёт
о действии — держатся кодом, а не формулировкой в промпте.""")]

    parts.append(section("Системный промпт: общий каркас", read(PROMPTS / "_core.md", 3)))
    parts.append(section("Системный промпт: «Первый контакт» (продажи)",
                         read(PROMPTS / "sales.md", 3)))
    parts.append(section("Системный промпт: «АвтоГрад Сервис»",
                         read(PROMPTS / "service.md", 3)))
    parts.append(section("Системный промпт: «Кадровик» (отдел кадров)",
                         read(PROMPTS / "hr.md", 3)))

    parts.append(section("База знаний: устройство и правила",
                         read(KB / "README.md", 3)))
    parts.append(section("FAQ отдела продаж", read(KB / "faq" / "sales.md", 3)))
    parts.append(section("FAQ сервиса", read(KB / "faq" / "service.md", 3)))
    parts.append(section("FAQ отдела кадров", read(KB / "faq" / "hr.md", 3)))
    parts.append(section("Типовые обращения: продажи", read(KB / "cases" / "sales.md", 3)))
    parts.append(section("Типовые обращения: сервис", read(KB / "cases" / "service.md", 3)))
    parts.append(section("Типовые обращения: кадры", read(KB / "cases" / "hr.md", 3)))
    parts.append(section("Скрипты по ролям", read(KB / "scripts" / "roles.md", 3)))
    parts.append(section("Карта эскалаций", read(KB / "escalations.md", 3)))
    parts.append(section("Стоп-лист формулировок", read(KB / "stop-list.md", 3)))
    parts.append(section("Шаблон записи базы знаний", read(KB / "templates" / "kb-record.md", 3)))

    parts.append(section("Как проверяется, что всё это работает", f"""
Проверка не сводится к чтению глазами: каждый слой закрыт машинным критерием,
и ни один этап не считается сданным, пока критерий красный.

| Слой | Чем проверяется | Проверок |
|---|---|---|
| Фикстуры: сток, клиенты, наряды, вакансии | `tools/validate_fixtures.py` — связность ссылок, диапазоны, даты | {counts['validate_fixtures']} |
| База знаний: FAQ, обращения, скрипты, эскалации | `tools/validate_kb.py` — обязательные поля, уникальность, связи с фикстурами | {counts['validate_kb']} |
| Системные промпты | `tools/validate_prompts.py` — структурная полнота, отсутствие фактов в тексте | {counts['validate_prompts']} |
| Поведение ассистентов | `tools/run_golden.py` — 60 эталонных диалогов через живое ядро | 67 реплик |

### Результат прогона эталонных диалогов

{golden_summary()}

Критерий этапа: ни в одном диалоге ассистент не называет цену окончательной,
не признаёт гарантийный случай, не обещает срок, не выдаёт факт, которого нет
в базе знаний, и не сообщает о действии, которого не совершал.

Полные расшифровки — в приложении.

### Перенос в GPTs и no-code

Системный промпт собирается склейкой двух файлов: каркас, затем роль.
В GPTs это поле Instructions, база знаний — файлы раздела «База знаний»
во вкладку Knowledge. В no-code-конструкторе каркас и роль кладутся в
системное сообщение, записи базы знаний — в векторное хранилище.

Блоки `ЗНАНИЯ` и `КОНТЕКСТ` собирает вызывающая сторона. Без них ассистент
будет вынужден отвечать по памяти модели — ровно то, что запрещено
инвариантом. Минимальный контекст: текущее время, часы работы отдела,
состояние владения диалогом.
""".strip()))

    appendix = ["## Приложение. Эталонные диалоги", ""]
    appendix.append("Ответы получены прогоном через рабочее ядро и не редактировались. "
                    "По 20 диалогов на ассистента, из них 8 конфликтных.")
    appendix.append("")
    SECTIONS.append(("Приложение. Эталонные диалоги", anchor("Приложение. Эталонные диалоги")))
    for role, title in (("sales", "Первый контакт"), ("service", "АвтоГрад Сервис"),
                        ("hr", "Кадровик")):
        appendix.append(f"### Диалоги: {title}")
        appendix.append("")
        appendix.append(nest((GOLDEN / "transcripts" / f"{role}.md").read_text(encoding="utf-8"), 4))
        appendix.append("")
    parts.append("\n".join(appendix))

    toc = ["## Оглавление", ""]
    toc += [f"{i}. [{title}]({link})" for i, (title, link) in enumerate(SECTIONS, 1)]
    toc.append("")
    toc.append("> В Google Docs оглавление вставляется через «Вставка → Оглавление» — "
               "оно соберётся по заголовкам автоматически и станет кликабельным.")

    head = ["# Нейроассистенты дилерского центра «АвтоГрад»", "",
            "> Учебный проект. Все данные вымышлены.", ""]
    return "\n".join(head) + "\n" + "\n\n".join(["\n".join(toc)] + parts) + "\n"


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    text = build()
    OUT.write_text(text, encoding="utf-8")
    words = len(text.split())
    print(f"Документ собран: {OUT.relative_to(ROOT)}")
    print(f"Разделов: {len(SECTIONS)} · строк: {text.count(chr(10))} · слов: {words}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
