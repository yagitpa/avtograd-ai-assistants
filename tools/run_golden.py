"""Прогон эталонных диалогов — критерий приёмки этапа 3.

Каждый диалог проигрывается через то же ядро, что отвечает в Telegram:
`answer()` с той же базой знаний, тем же контекстом и тем же валидатором.
Проверки идут на каждую реплику ассистента:

* стоп-лист  — детерминированные шаблоны из `docs/knowledge-base/stop-list.md`;
* `forbid`   — запреты конкретного диалога (например, «Base» там, где этой
               комплектации у модели нет);
* `expect`   — что в этой реплике обязано быть (оговорка про оферту при цене);
* `expect_dialog` — что обязано прозвучать хоть где-то в диалоге: часть
               требований не привязана к ходу, и «сказал сразу» не хуже,
               чем «сказал после уточнения»;

* `mode`     — ожидаемый режим: ОТВЕТ, ЭСКАЛАЦИЯ, МОЛЧАНИЕ;
* числа      — все суммы от четырёх знаков сверяются с фикстурами и базой
               знаний. Число, которого там нет, ассистент придумал.

`expect` и `expect_dialog` пишутся **основами, а не словоформами**. Шаблон
«будни» не совпадает с «по будням», «не числится» — с «не числятся»,
«только владельц» — с «только владелец». Такой шаблон проходит у того
провайдера, под чьи формулировки он написан, и падает у следующего: при
переезде на YandexGPT три диалога из шестидесяти упали именно так, и ни
один из них не был дефектом ассистента. Правило то же, что для стоп-листа
(`docs/knowledge-base/stop-list.md`), и нарушается оно так же незаметно.

Нарушение стоп-листа, `forbid`, `expect` и режима — отказ прогона.
Незнакомое число — предупреждение: сверять его глазами дешевле, чем
поддерживать список исключений, а ложное срабатывание валидатора по
стоп-листу такой же дефект, как пропуск (stop-list.md, раздел «Как это
проверяется»).

Запуск:
    python tools/run_golden.py                  все три ассистента
    python tools/run_golden.py --role sales     один
    python tools/run_golden.py --only GD-SLS-04 один диалог
    python tools/run_golden.py --workers 1      последовательно, для отладки

Результат: docs/golden/report.md и docs/golden/transcripts/<роль>.md —
приложение к сдаче по ТЗ.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from assistant import (DEFAULT_MODEL, KB_VERSION, PROMPT_VERSIONS, ROLES,  # noqa: E402
                       answer, load_env, validate_outgoing)

load_env()

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "tests" / "golden"
OUT = ROOT / "docs" / "golden"
KB = ROOT / "docs" / "knowledge-base"

ROLE_FILES = {"sales": "sales.json", "service": "service.json", "hr": "hr.json"}

# Пробел разделяет разряды только между группами ровно по три цифры: иначе
# «Aurora X5 2024» превращается в число 52024 и попадает в отчёт как выдумка.
NUMBER_RE = re.compile(r"(?<!\d)\d{1,3}(?:[   ]\d{3})+(?!\d)|\d+")


def known_numbers() -> set[int]:
    """Все числа, которые ассистенту разрешено называть.

    Источник — фикстуры и тексты базы знаний. Всё, чего здесь нет, ассистент
    либо посчитал сам, либо вспомнил из обучения: и то и другое запрещено
    инвариантом «факты только из блока ЗНАНИЯ».
    """
    corpus = []
    for path in list(KB.rglob("*.json")) + list(KB.rglob("*.md")) + list(KB.rglob("*.csv")):
        corpus.append(path.read_text(encoding="utf-8"))
    numbers: set[int] = set()
    for chunk in corpus:
        for raw in NUMBER_RE.findall(chunk):
            digits = re.sub(r"\D", "", raw)
            if digits:
                numbers.add(int(digits))
    return numbers


KNOWN = known_numbers()
# Ниже этого порога числа не проверяем: ТО-1, три года гарантии, 20 минут,
# 09:00 и проценты дают шум, а риск выдумки лежит в суммах.
NUMBER_FLOOR = 1000


def unknown_numbers(text: str) -> list[int]:
    found = []
    for raw in NUMBER_RE.findall(text):
        digits = re.sub(r"\D", "", raw)
        if not digits:
            continue
        value = int(digits)
        if value < NUMBER_FLOOR or value in KNOWN:
            continue
        if 1990 <= value <= 2100:  # год
            continue
        found.append(value)
    return found


def parse_when(value: str | None) -> datetime:
    if not value:
        return datetime(2026, 9, 18, 14, 0)
    return datetime.fromisoformat(value)


def run_dialog(role: str, case: dict, model: str) -> dict:
    ctx = case.get("context", {})
    when = parse_when(ctx.get("time"))
    kwargs = dict(now=when, owner=ctx.get("owner", "bot_owned"),
                  stale=bool(ctx.get("stale")), model=model,
                  extra_context=ctx.get("extra", ""))

    history: list[dict] = []
    turns_out = []
    problems: list[str] = []   # дошло бы до клиента
    caught: list[str] = []     # поймано валидатором до отправки
    warnings: list[str] = []

    for index, turn in enumerate(case["turns"], 1):
        history.append({"role": "user", "content": turn["user"]})
        result = answer(role, history, **kwargs)
        text = result["text"]
        if text:
            history.append({"role": "assistant", "content": text})

        where = f"{case['id']} реплика {index}"

        expected_mode = turn.get("mode")
        if expected_mode and not result["mode"].startswith(expected_mode):
            problems.append(f"{where}: ожидался режим {expected_mode}, получен {result['mode']}")

        # Срабатывание валидатора — не то же самое, что перейденная красная
        # линия. Модель попыталась сказать запрещённое, сеть поймала, клиент
        # получил честную передачу человеку. Это дефект качества ответа, но
        # не пробой безопасности, и считается отдельно: смешивать их значит
        # либо пугаться исправной защиты, либо не заметить настоящий пробой.
        if result.get("violations"):
            caught.append(f"{where}: стоп-лист поймал — {', '.join(result['violations'])}")
            # Клиент получил заглушку, а не ответ модели. Проверять заглушку
            # на обязательные и запрещённые фрагменты бессмысленно: одно
            # событие превратилось бы в два расхождения и завысило счёт.
            turns_out.append({"user": turn["user"], "assistant": text,
                              "mode": result["mode"], "records": result.get("records", []),
                              "guessed": bool(result.get("mode_guessed")),
                              "blocked": result.get("blocked", "")})
            continue

        for pattern in turn.get("forbid", []):
            if re.search(pattern, text, re.IGNORECASE):
                problems.append(f"{where}: запрещённое «{pattern}» найдено в ответе")

        for pattern in turn.get("expect", []):
            if not re.search(pattern, text, re.IGNORECASE):
                problems.append(f"{where}: обязательное «{pattern}» в ответе отсутствует")

        if text:
            extra = validate_outgoing(text)
            if extra and not result.get("violations"):
                problems.append(f"{where}: запрещённая формулировка дошла бы до клиента — "
                                f"{', '.join(extra)}")
            for value in unknown_numbers(text):
                warnings.append(f"{where}: число {value} не найдено в базе знаний")

        turns_out.append({"user": turn["user"], "assistant": text,
                          "mode": result["mode"], "records": result.get("records", []),
                          "guessed": bool(result.get("mode_guessed")),
                          "blocked": result.get("blocked", "")})

    # Требование уровня диалога, а не реплики. Часть обязательного не привязана
    # к ходу: вилку дохода ассистент вправе назвать сразу или после уточнения
    # вакансии, и оба поведения правильные. Проверка «в этой реплике» наказывала
    # за законное уточнение и ломалась при смене провайдера: OpenAI уточнял,
    # YandexGPT называл сразу, и один и тот же диалог падал то там, то тут.
    whole = " ".join(t["assistant"] for t in turns_out)
    for pattern in case.get("expect_dialog", []):
        if not re.search(pattern, whole, re.IGNORECASE):
            problems.append(f"{case['id']}: обязательное «{pattern}» "
                            "не прозвучало ни в одной реплике")

    return {"case": case, "role": role, "turns": turns_out,
            "problems": problems, "caught": caught, "warnings": warnings}


def load_cases(role: str) -> list[dict]:
    data = json.loads((GOLDEN / ROLE_FILES[role]).read_text(encoding="utf-8"))
    return data["dialogs"]


def write_transcripts(role: str, runs: list[dict]) -> None:
    lines = [f"# Эталонные диалоги — {ROLES[role]['name']}", "",
             "> Учебный проект «АвтоГрад». Прогон этапа 3: реальные ответы ядра, "
             "не отредактированные вручную. Данные вымышлены.", ""]
    for run in runs:
        case = run["case"]
        ctx = case.get("context", {})
        lines.append(f"## {case['id']} — {case['title']}")
        lines.append("")
        meta = [f"**Тип:** {case.get('kind', 'типовой')}"]
        if case.get("covers"):
            meta.append(f"**Проверяет:** {case['covers']}")
        meta.append(f"**Время:** {ctx.get('time', '2026-09-18 14:00')}")
        if ctx.get("owner", "bot_owned") != "bot_owned":
            meta.append(f"**Владение диалогом:** {ctx['owner']}")
        if ctx.get("stale"):
            meta.append("**Данные стока:** устарели")
        lines.append(" · ".join(meta))
        lines.append("")
        for turn in run["turns"]:
            lines.append(f"**Клиент:** {turn['user']}")
            lines.append("")
            body = turn["assistant"] or "_(ответа нет — режим молчания)_"
            lines.append(f"**Ассистент:** {body}")
            lines.append("")
            if turn.get("blocked"):
                lines.append(f"> Валидатор заблокировал исходное сообщение: {turn['blocked']}")
                lines.append("")
        verdict = "расхождений нет" if not run["problems"] else "; ".join(run["problems"])
        lines.append(f"_Итог: {verdict}._")
        lines.append("")
    path = OUT / "transcripts" / f"{role}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_report(all_runs: list[dict], model: str, seconds: float, passes: int = 1) -> None:
    problems = [p for run in all_runs for p in run["problems"]]
    caught = [c for run in all_runs for c in run["caught"]]
    warnings = [w for run in all_runs for w in run["warnings"]]
    by_role: dict[str, list[dict]] = {}
    for run in all_runs:
        by_role.setdefault(run["role"], []).append(run)

    lines = ["# Отчёт прогона эталонных диалогов", "",
             "> Учебный проект «АвтоГрад». Файл создаётся `tools/run_golden.py`, "
             "руками не правится.", "",
             f"**Дата прогона:** {datetime.now():%Y-%m-%d %H:%M} · "
             f"**модель:** {model} · **время:** {seconds:.0f} с", "",
             "**Версии промптов:** "
             + " · ".join(f"{role} `{ver}`" for role, ver in PROMPT_VERSIONS.items())
             + f" · **база знаний** `{KB_VERSION}`", "",
             "Прогон идёт мимо кэша ответов: приёмка обязана каждый раз "
             "спрашивать модель заново (ADR-0017).", "",
             (f"Прогонов подряд: {passes}. " if passes > 1 else "")
             + "Модель недетерминирована — `temperature` у неё не настраивается, "
               "и один и тот же вопрос даёт разные формулировки. Поэтому "
               "расхождения считаются по всем прогонам сразу: дефект, "
               "повторившийся хотя бы раз, считается найденным.", "",
             "| Ассистент | Диалогов | Конфликтных | Реплик | Дошло бы до клиента | Поймал валидатор |",
             "|---|---|---|---|---|---|"]
    for role, runs in by_role.items():
        conflict = sum(1 for r in runs if r["case"].get("kind") == "конфликтный") // passes
        turns = sum(len(r["turns"]) for r in runs)
        bad = sum(len(r["problems"]) for r in runs)
        net = sum(len(r["caught"]) for r in runs)
        lines.append(f"| {ROLES[role]['name']} | {len(runs) // passes} | {conflict} | {turns}"
                     f" | {bad} | {net} |")
    lines += ["", f"**Итого:** {len(all_runs) // passes} диалогов, "
              f"{sum(len(r['turns']) for r in all_runs)} реплик ассистента"
              + (f" за {passes} прогона." if passes > 1 else "."), ""]

    lines.append("## Критерий приёмки: что дошло бы до клиента")
    lines.append("")
    if problems:
        lines += [f"- {p}" for p in problems]
    else:
        lines.append("Ничего. Ни в одной реплике ассистент не назвал цену окончательной, "
                     "не признал гарантийный случай, не пообещал срок, не сообщил о "
                     "действии, которого не совершал, и не вышел за пределы базы знаний.")
    lines.append("")

    lines.append("## Срабатывания валидатора: что он поймал до отправки")
    lines.append("")
    if caught:
        lines.append("Модель попыталась сказать запрещённое, детерминированный валидатор "
                     "заблокировал ответ, клиент получил честную передачу человеку. "
                     "Это дефект качества ответа, а не пробой красной линии — но каждый "
                     "случай разбирается: либо правится промпт, либо шаблон, если "
                     "срабатывание ложное.")
        lines.append("")
        lines += [f"- {c}" for c in caught]
    else:
        lines.append("Ни одного: валидатору не пришлось вмешиваться.")
    lines.append("")

    lines.append("## Числа под вопросом")
    lines.append("")
    if warnings:
        lines.append("Числа, которых нет в фикстурах и текстах базы знаний. "
                     "Каждое проверяется глазами: часть — арифметика по разрешённым "
                     "данным, часть — выдумка.")
        lines.append("")
        lines += [f"- {w}" for w in warnings]
    else:
        lines.append("Нет: все суммы в ответах найдены в базе знаний.")
    lines.append("")
    lines.append("## Диалоги")
    lines.append("")
    for role, runs in by_role.items():
        lines.append(f"- [{ROLES[role]['name']}](transcripts/{role}.md) — {len(runs)} диалогов")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Прогон эталонных диалогов «АвтоГрада»")
    ap.add_argument("--role", choices=sorted(ROLE_FILES), action="append")
    ap.add_argument("--only", action="append", help="идентификатор диалога, можно повторять")
    ap.add_argument("--dump", metavar="ФАЙЛ",
                    help="записать реплики частичного прогона в отдельный файл: "
                         "сравнение провайдеров надо читать, а не только считать")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--passes", type=int, default=1,
                    help="сколько раз прогнать весь набор: модель недетерминирована, "
                         "и редкий дефект виден только на повторе")
    args = ap.parse_args()

    roles = args.role or sorted(ROLE_FILES)
    jobs = []
    for role in roles:
        for case in load_cases(role):
            if args.only and case["id"] not in args.only:
                continue
            jobs.append((role, case))

    if not jobs:
        print("Нечего прогонять.")
        return 1

    passes = max(1, args.passes)
    print(f"Диалогов: {len(jobs)} · модель {args.model} · потоков {args.workers}"
          + (f" · прогонов: {passes}" if passes > 1 else ""))
    started = time.time()
    runs: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for attempt in range(passes):
            batch = list(pool.map(lambda job: run_dialog(job[0], job[1], args.model), jobs))
            runs.extend(batch)
            if passes > 1:
                bad = sum(len(r["problems"]) for r in batch)
                net = sum(len(r["caught"]) for r in batch)
                print(f"  прогон {attempt + 1}: дошло бы до клиента {bad}, "
                      f"поймал валидатор {net}")
    seconds = time.time() - started

    order = {case["id"]: i for i, (_r, case) in enumerate(jobs)}
    runs.sort(key=lambda r: order[r["case"]["id"]])

    # Приложение к сдаче пишется только полным прогоном: частичный затёр бы
    # расшифровки остальных диалогов, и приложение перестало бы быть полным.
    if args.only:
        print("Частичный прогон: расшифровки и отчёт не перезаписываются.")
        if args.dump:
            # Приложение к сдаче собирается полным прогоном и частичным не
            # трогается. Но приёмка, которую нельзя прочитать, — это счётчик,
            # а не приёмка: сравнивая провайдеров, смотрят на формулировки.
            lines = ["# Реплики частичного прогона", "",
                     f"**Модель:** `{args.model}` · **диалогов:** {len(jobs)} · "
                     f"**время:** {seconds} с", ""]
            for r in runs:
                lines += [f"## {r['case']['id']} — {r['case']['title']}", ""]
                for turn in r["turns"]:
                    lines += [f"**Клиент:** {turn['user']}", "",
                              f"**Ассистент:** {turn['assistant']}", "",
                              f"*режим: {turn.get('mode', '—')}*", ""]
            Path(args.dump).write_text("\n".join(lines) + "\n", encoding="utf-8")
            print(f"Реплики: {args.dump}")
    else:
        for role in roles:
            # В расшифровки идёт последний прогон каждого диалога: приложение
            # к сдаче показывает один связный разговор, а не три варианта.
            # Из нескольких прогонов одного диалога в приложение идёт тот,
            # где что-то пошло не так: приложение к сдаче должно показывать
            # найденный дефект, а не удачный дубль, которым его можно
            # случайно прикрыть.
            seen: dict[str, dict] = {}
            for r in runs:
                if r["role"] != role:
                    continue
                kept = seen.get(r["case"]["id"])
                if kept is None or (not kept["problems"] and not kept["caught"]):
                    seen[r["case"]["id"]] = r
            if seen:
                write_transcripts(role, [seen[cid] for cid in
                                         sorted(seen, key=lambda c: order[c])])
        write_report(runs, args.model, seconds, passes)

    problems = [p for r in runs for p in r["problems"]]
    caught = [c for r in runs for c in r["caught"]]
    warnings = [w for r in runs for w in r["warnings"]]
    for p in problems:
        print(f"  ДОШЛО БЫ ДО КЛИЕНТА · {p}")
    for c in caught:
        print(f"  поймал валидатор · {c}")
    print(f"\nДиалогов: {len(runs) // passes} · реплик: "
          f"{sum(len(r['turns']) for r in runs)} · время: {seconds:.0f} с")
    print(f"Дошло бы до клиента: {len(problems)} · поймал валидатор: {len(caught)} · "
          f"чисел под вопросом: {len(warnings)}")

    # Соблюдение контракта о режиме — свойство модели, и при смене провайдера
    # оно меняется первым. Считается отдельно от расхождений: клиент этого не
    # видит, но эскалация держится на догадке, а не на объявлении.
    turns = sum(len(r["turns"]) for r in runs)
    guessed = sum(1 for r in runs for t in r["turns"] if t.get("guessed"))
    if turns:
        print(f"Режим не объявлен ассистентом: {guessed} из {turns} реплик "
              f"({100 * guessed / turns:.0f}%) — определён по признакам")
    print(f"Отчёт: docs/golden/report.md")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
