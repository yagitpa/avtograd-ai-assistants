"""Тесты холодного контура: свежесть стока, касания, сводка, граница ПДн.

Ни сети, ни n8n, ни вызовов модели. Проверяется главное обещание этапа 5:
наружу уходят номера и числа, а решение «что сказать и куда» остаётся в ядре.

Запуск:  python tests/test_cold.py
Код возврата 1 — есть падения.
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_boundary  # noqa: E402
import cold  # noqa: E402
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


class FakeBot:
    def __init__(self, channel="telegram-client"):
        self.channel = channel
        self.sent: list[tuple[int, str]] = []

    def send(self, chat_id, text, keyboard=None):
        self.sent.append((int(chat_id), text))


HEADER = "vin;dealer_id;condition;make;model;trim;year;mileage_km;color;price_rub;status"
ROW = "TESTAG00000000777;dc-north;new;Aurora;X5;Base;2026;10;белый;3200000;in_stock"


def sandbox_feed(tmp: Path, generated: datetime) -> None:
    """Подменяет пути модуля на временные: тест не трогает фикстуры проекта."""
    cold.STOCK = tmp / "stock.csv"
    cold.STOCK_META = tmp / "stock_meta.json"
    cold.STOCK.write_text(HEADER + "\n" + ROW + "\n", encoding="utf-8-sig")
    cold.STOCK_META.write_text(json.dumps(
        {"feed": {"generated_at": generated.isoformat(timespec="seconds"),
                  "stale_after_hours": 4}}, ensure_ascii=False), encoding="utf-8")


def test_feed_freshness() -> None:
    tmp = Path(tempfile.mkdtemp())
    now = datetime(2026, 9, 21, 12, 0)

    sandbox_feed(tmp, now - timedelta(minutes=30))
    state = cold.feed_state(now)
    check(not state["stale"], "получасовой фид считается свежим")
    check(state["age_minutes"] == 30.0, "возраст фида посчитан")

    sandbox_feed(tmp, now - timedelta(hours=5))
    check(cold.feed_state(now)["stale"], "пятичасовой фид считается протухшим")
    check("старше" in cold.feed_state(now)["reason"], "названа причина")

    # Регресс по существу этапа: до этапа 5 признак stale никто не вычислял,
    # его выставляли руками в chat.py. Правило существовало только в тестах.
    cold.STOCK.unlink()
    check(cold.feed_state(now)["stale"], "пропавший файл стока — это тоже «протух»")
    check("недоступен" in cold.feed_state(now)["reason"], "отсутствие файла названо прямо")


def test_sync_rejects_bad_feed() -> None:
    tmp = Path(tempfile.mkdtemp())
    now = datetime(2026, 9, 21, 12, 0)
    sandbox_feed(tmp, now - timedelta(hours=9))
    before = cold.STOCK.read_text(encoding="utf-8-sig")

    for body, title in (
        ("", "пустой фид отвергнут"),
        ("vin;model\nTESTAG1;X3", "фид без обязательных столбцов отвергнут"),
        (HEADER + "\n;dc-north;new;Aurora;X5;Base;2026;10;белый;3200000;in_stock",
         "строка без VIN отвергнута"),
    ):
        try:
            cold.sync_stock(body)
            check(False, title)
        except ValueError:
            check(True, title)

    check(cold.STOCK.read_text(encoding="utf-8-sig") == before,
          "негодный фид не затёр годный")
    check(cold.feed_state(now)["stale"], "после отказа сток остался протухшим")

    good = HEADER + "\n" + ROW + "\n" + ROW.replace("777", "778") + "\n"
    result = cold.sync_stock(good, datetime(2026, 9, 21, 11, 55))
    check(result["принято_строк"] == 2, "годный фид принят целиком")
    check(result["в_наличии"] == 2, "посчитаны позиции в наличии")
    check(not cold.feed_state(now)["stale"], "после приёма сток снова свежий")


def scene():
    store = Store(Path(tempfile.mkdtemp()) / "cold.db")
    contact = store.identify("telegram-client", 4242, "lead")
    dialog = store.current_dialog(contact, "telegram-client")
    store.set_route(dialog, "sales")
    store.add_message(dialog, "in", "Интересует Aurora X5", route="sales")
    store.add_message(dialog, "out", "Есть две в наличии. Показать?", route="sales")
    return store, dialog


def test_silent_dialogs_returns_ids_only() -> None:
    store, dialog = scene()
    check(store.silent_dialogs(hours=0) == [dialog], "молчащий диалог найден")

    payload = json.dumps(store.silent_dialogs(hours=0), ensure_ascii=False)
    check("4242" not in payload and "lead" not in payload,
          "наружу уходят только номера диалогов, без адреса и имени")

    # Реплика клиента снимает диалог с очереди: молчит не он, а ассистент.
    store.add_message(dialog, "in", "Да, покажите", route="sales")
    check(store.silent_dialogs(hours=0) == [], "ответивший клиент из очереди убран")


def test_followup_sent_once() -> None:
    store, dialog = scene()
    bot = FakeBot()
    bots = {"telegram-client": bot}

    first = cold.send_followup(bots, store, dialog)
    check(first["sent"], "напоминание отправлено")
    check(bot.sent[-1][0] == 4242, "ушло в чат клиента")
    check("АвтоГрад" in bot.sent[-1][1], "текст взят из шаблона")

    second = cold.send_followup(bots, store, dialog)
    check(not second["sent"], "второе напоминание не отправлено")
    check(second["reason"] == "касание уже было", "названа причина отказа")
    check(len(bot.sent) == 1, "в чат клиента ушло ровно одно сообщение")
    check(store.silent_dialogs(hours=0) == [], "после касания диалог выбыл из очереди")


def test_followup_respects_human() -> None:
    store, dialog = scene()
    bot = FakeBot()
    store.take_over(dialog, "501")
    result = cold.send_followup({"telegram-client": bot}, store, dialog)
    check(not result["sent"], "в диалог, который ведёт человек, ассистент не пишет")
    check(not bot.sent, "клиент не получил ничего")
    check(store.silent_dialogs(hours=0) == [], "такой диалог и в очередь не попадает")


def test_digest_has_no_personal_data() -> None:
    store, dialog = scene()
    store.add_escalation(dialog, "sales", "ESC-001", "client")
    data = store.digest(days=1)

    check(data["диалогов"] >= 1, "диалоги посчитаны")
    check(data["эскалаций"] == 1, "эскалации посчитаны")
    check(data["по_маршрутам"].get("sales") == 1, "разбивка по маршрутам есть")

    payload = json.dumps(data, ensure_ascii=False)
    check("4242" not in payload, "в сводке нет адреса в канале")
    check("Aurora" not in payload and "Интересует" not in payload,
          "в сводке нет ни одной реплики")

    text = cold.digest_text(data, {"stale": False})
    check("Диалогов:" in text, "сводка читается словами")
    check("ВНИМАНИЕ" in cold.digest_text(data, {"stale": True, "reason": "фид старше 4 ч"}),
          "протухший сток попадает в сводку тревогой")


def test_boundary_scanner_catches_leaks() -> None:
    """Сканер границы обязан ловить то, ради чего он написан."""
    names = {"Тимофеева"}
    check(check_boundary.scan("клиент +7 916 123-45-67 ждёт", names),
          "телефон найден")
    check(check_boundary.scan("VIN TESTAG00000000910", names), "VIN найден")
    check(check_boundary.scan("ivan@example.com", names), "e-mail найден")
    check(check_boundary.scan("машина А123ВС777", names), "госномер найден")
    check(check_boundary.scan("ответственная Тимофеева", names), "имя из базы найдено")

    clean = json.dumps({"dialogs": [4, 7, 12], "sent": True, "диалогов": 3},
                       ensure_ascii=False)
    check(not check_boundary.scan(clean, names),
          "на честном ответе ядра сканер молчит")

    # Оба ложных класса найдены на первом живом журнале n8n. Сканер, кричащий
    # на каждую метку времени и каждый хеш, хуже отсутствующего: к нему
    # привыкают и перестают читать, а настоящая утечка тонет в шуме.
    check(not check_boundary.scan('{"startTime": 1789994813652}', names),
          "unix-время в миллисекундах не читается как телефон")
    check(not check_boundary.scan(
              '{"resumeToken": "4d1b696c4fc81606763771dabe6ba1dc591cacb1"}', names),
          "шестнадцатеричный токен не читается как телефон")
    check(check_boundary.scan('звоните 8 (999) 481-36-61', names),
          "настоящий телефон в скобках всё ещё ловится")
    check(check_boundary.scan('тел. 89161234567 для связи', names),
          "телефон слитно всё ещё ловится")


def main() -> int:
    for test in (test_feed_freshness, test_sync_rejects_bad_feed,
                 test_silent_dialogs_returns_ids_only, test_followup_sent_once,
                 test_followup_respects_human, test_digest_has_no_personal_data,
                 test_boundary_scanner_catches_leaks):
        test()
    print(f"Проверок выполнено: {passed + len(failed)}")
    if failed:
        print(f"\nПадений: {len(failed)}")
        for title in failed:
            print(f"  · {title}")
        return 1
    print("Падений нет. Холодный контур отдаёт номера и числа, тексты пишет ядро.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
