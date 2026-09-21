"""Тесты лестницы деградации и метрик качества.

Стенд отказов (`tools/failures.py`) сам по себе — инструмент разбора: он
печатает, что получил клиент, и его читает человек. Здесь тот же стенд
превращается в ворота: любой уровень, на котором клиент остался без ответа,
роняет прогон.

Нужны они оба. Отчёт отвечает на вопрос «как оно ломается», тест — на вопрос
«не сломали ли мы то, что чинили». Второй вопрос задаётся чаще.

Ни сети, ни вызовов модели.

Запуск:  python tests/test_failures.py
Код возврата 1 — есть падения.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import failures  # noqa: E402
from store import Store  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

passed, failed = 0, []


def check(condition: bool, title: str) -> None:
    global passed
    if condition:
        passed += 1
    else:
        failed.append(title)


def test_ladder_holds() -> None:
    """Критерий приёмки этапа 6: ни при одном отказе клиент не без ответа."""
    rows = failures.run()
    check(len(rows) >= 8, "сценариев отказа не меньше восьми")
    for row in rows:
        check(row["ответ есть"],
              f"{row['уровень']}: клиент получил ответ ({row['что отказало']})")
        check(row.get("исправен", True),
              f"{row['уровень']}: уровень реализован ({row['что отказало']})")


def test_ladder_details() -> None:
    """Не «ответ есть», а «ответ правильного рода»."""
    rows = {f"{r['уровень']}|{r['что отказало']}": r for r in failures.run()}

    l1 = rows["L1|DMS дилера"]
    check(l1["режим"].startswith("ЭСКАЛАЦИЯ"), "L1 при молчащей DMS уходит человеку")
    check("статус" not in l1["клиент получил"].lower()
          or "не берусь" in l1["клиент получил"].lower(),
          "L1 не называет выдуманный статус")

    l2 = rows["L2|основной провайдер модели"]
    check(l2["режим"] == "ОТВЕТ", "L2 переключился на резерв незаметно для клиента")
    check("18 900" in l2["клиент получил"], "клиент получил ответ по существу")
    check(l2["попыток к провайдеру"] == 2, "основного дёрнули дважды, прежде чем сдаться")

    l3 = rows["L3|все провайдеры модели"]
    check(l3["режим"].startswith("ЭСКАЛАЦИЯ"),
          "L3 ставит диалог в очередь оператору, а не просто отвечает шаблоном")

    l4 = rows["L4|ядро целиком"]
    check(l4["режим"] == "ЭСКАЛАЦИЯ (сбой ядра)", "L4 помечен отдельной причиной")
    check("сохранено" in l4["клиент получил"],
          "L4 говорит клиенту, что повторять не нужно")


def test_report_names_the_gaps() -> None:
    """Отчёт обязан называть провал провалом, а не прятать его в таблице."""
    good = [{"уровень": "L0", "что отказало": "ничего", "клиент получил": "ответ",
             "режим": "ОТВЕТ", "ответ есть": True, "разбор": "—", "исправен": True}]
    text = failures.report(good)
    check("Ни при одном отказе клиент не остался без ответа" in text,
          "зелёный отчёт говорит об этом прямо")

    bad = good + [{"уровень": "L4", "что отказало": "ядро", "клиент получил": "",
                   "режим": "—", "ответ есть": False, "разбор": "—", "исправен": False}]
    text = failures.report(bad)
    check("Критерий не выполнен" in text, "провал назван провалом")
    check("**ДА**" in text, "молчание выделено в таблице")


def metrics_scene():
    store = Store(Path(tempfile.mkdtemp()) / "q.db")
    contact = store.identify("telegram-client", 77, "x")
    dialog = store.current_dialog(contact, "telegram-client")
    store.set_route(dialog, "service")
    add = lambda text, mode: store.add_message(  # noqa: E731
        dialog, "out", text, route="service", mode=mode)
    add("Стоимость ТО-2 — 18 900 ₽.", "ОТВЕТ")
    add("По этому VIN записей не нашёл.", "ОТВЕТ")
    add("Передаю обращение специалисту.", "ЭСКАЛАЦИЯ (валидатор)")
    add("Передаю обращение руководителю сервиса.", "ЭСКАЛАЦИЯ")
    add("Уточните, пожалуйста, VIN.", "УТОЧНЕНИЕ")
    add("Здравствуйте, это Ирина.", "ЧЕЛОВЕК (501)")
    add("Вы недавно писали в «АвтоГрад».", "ХОЛОДНЫЙ КОНТУР (followup)")
    return store, dialog


def test_quality_counts_only_the_assistant() -> None:
    store, _ = metrics_scene()
    data = store.quality(days=1)

    check(data["ответов_ассистента"] == 5,
          "реплики человека и касания контура в знаменатель не входят")
    check(data["не_знаю"]["штук"] == 1, "ответ «не нашёл» посчитан")
    check(data["не_знаю"]["доля_%"] == 20.0, "доля считается от ответов ассистента")
    check(data["эскалаций"]["штук"] == 2, "эскалации посчитаны обе")
    check(data["поймал_валидатор"]["штук"] == 1, "сработка валидатора выделена отдельно")
    check(data["уточнений"]["штук"] == 1, "уточнения посчитаны")

    reasons = data["эскалаций"]["по_причинам"]
    check(reasons.get("валидатор") == 1, "причина эскалации видна")
    check(reasons.get("по решению ассистента") == 1,
          "эскалация без скобок названа решением ассистента")


def test_quality_survives_empty_period() -> None:
    """Деление на ноль в метриках — классический способ уронить дашборд."""
    store = Store(Path(tempfile.mkdtemp()) / "empty.db")
    data = store.quality(days=1)
    check(data["ответов_ассистента"] == 0, "пустой период посчитан как ноль")
    check(data["не_знаю"]["доля_%"] == 0.0, "доля на пустом периоде равна нулю")
    check(data["кэш"]["сэкономлено_вызовов_%"] == 0.0, "экономия кэша не падает на нуле")


def main() -> int:
    for test in (test_ladder_holds, test_ladder_details, test_report_names_the_gaps,
                 test_quality_counts_only_the_assistant, test_quality_survives_empty_period):
        test()
    print(f"Проверок выполнено: {passed + len(failed)}")
    if failed:
        print(f"\nПадений: {len(failed)}")
        for title in failed:
            print(f"  · {title}")
        return 1
    print("Падений нет. Лестница держит все пять уровней, метрики считаются честно.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
