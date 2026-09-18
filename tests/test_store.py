"""Тесты хранилища диалогов: без сети и без вызовов модели.

Проверяют то, что ломается молча: псевдонимизацию, разделение контактов по
каналам, обрыв контекста при переключении маршрута, срок хранения, право на
забвение и ключ кэша.

Запуск:  python tests/test_store.py
Код возврата 1 — есть падения.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from store import Store, cache_key, pseudonymize, restore  # noqa: E402

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


def fresh() -> Store:
    return Store(Path(tempfile.mkdtemp()) / "test.db")


def test_pseudonymize() -> None:
    text = ("VIN TESTAG00000000901, телефон +7 900 000-21-01, "
            "номер А001АА777, почта ivan@example.com")
    clean, mapping = pseudonymize(text)
    check("TESTAG00000000901" not in clean, "VIN убран из текста")
    check("+7 900 000-21-01" not in clean, "телефон убран из текста")
    check("А001АА777" not in clean, "госномер убран из текста")
    check("example.com" not in clean, "почта убрана из текста")
    # Регресс: телефонный шаблон находит одиннадцать цифр внутри VIN и рвал его
    # пополам, пока не встал после VIN в списке шаблонов.
    check(mapping.get("{{VIN_1}}") == "TESTAG00000000901", "VIN распознан целиком")
    check(restore(clean, mapping) == text, "исходный текст восстанавливается")

    twice, mapping2 = pseudonymize("Звоните на +7 900 000-21-01 или +7 900 000-21-01")
    check(len([v for v in mapping2 if v.startswith("{{PHONE")]) == 1,
          "одно значение — один плейсхолдер")

    money, mapping3 = pseudonymize("Цена 2 490 000 ₽, бюджет до 2 500 000")
    check(not mapping3, "цены и бюджеты за персональные данные не принимаются")


def test_contacts() -> None:
    store = fresh()
    first = store.identify("telegram-client", 1001, "ivan")
    again = store.identify("telegram-client", 1001)
    check(first == again, "тот же адрес в канале — тот же контакт")

    other_channel = store.identify("telegram-staff", 1001)
    check(other_channel != first,
          "один и тот же telegram id в разных контурах — разные контакты")

    stranger = store.identify("telegram-client", 1002)
    check(stranger != first, "другой адрес — другой контакт")


def test_dialog_and_history() -> None:
    store = fresh()
    contact = store.identify("telegram-client", 2001)
    dialog = store.current_dialog(contact, "telegram-client")
    check(store.current_dialog(contact, "telegram-client") == dialog,
          "в пределах сессии диалог тот же")

    store.set_route(dialog, "service")
    store.add_message(dialog, "in", "Когда ТО?", route="service")
    store.add_message(dialog, "out", "ТО-3 при 45 000 км.", route="service", mode="ОТВЕТ",
                      prompt_version="0156b2fb", kb_version="4e58f4da")
    store.set_route(dialog, "sales")
    store.add_message(dialog, "in", "А что по новым моделям?", route="sales")

    sales_history = store.history(dialog, route="sales")
    check(len(sales_history) == 1, "после переключения ассистент видит только свою часть")
    check("ТО-3" not in sales_history[0]["content"], "чужая переписка не попадает в контекст")
    check(len(store.history(dialog)) == 3, "в базе сохранены все реплики")

    # Возвращение через сутки — новый разговор.
    store.conn.execute("UPDATE dialog SET last_at = ? WHERE id = ?",
                       ((datetime.now() - timedelta(hours=30)).isoformat(), dialog))
    store.conn.commit()
    check(store.current_dialog(contact, "telegram-client") != dialog,
          "после долгого молчания открывается новый диалог")


def test_restart_survives() -> None:
    path = Path(tempfile.mkdtemp()) / "restart.db"
    store = Store(path)
    contact = store.identify("telegram-client", 3001)
    dialog = store.current_dialog(contact, "telegram-client")
    store.add_message(dialog, "in", "Есть Vega Cross?", route="sales")
    store.close()

    revived = Store(path)  # как будто процесс перезапустили
    contact_again = revived.identify("telegram-client", 3001)
    dialog_again = revived.current_dialog(contact_again, "telegram-client")
    check(contact_again == contact, "контакт пережил перезапуск")
    check(dialog_again == dialog, "диалог пережил перезапуск")
    check(revived.history(dialog_again)[0]["content"] == "Есть Vega Cross?",
          "история пережила перезапуск")


def test_returning_summary() -> None:
    store = fresh()
    contact = store.identify("telegram-client", 4001)
    first = store.current_dialog(contact, "telegram-client")
    store.set_route(first, "service")
    store.close_dialog(first)
    second = store.current_dialog(contact, "telegram-client")
    check(second != first, "после закрытия открывается новый диалог")
    summary = store.contact_summary(contact)
    check(summary["dialogs"] == 2, "сводка считает разговоры")
    check("service" in summary["routes"], "сводка помнит прошлые маршруты")


def test_cache() -> None:
    store = fresh()
    marks = "2026-09-18|открыт|впервые"
    key = cache_key("sales", "  Какая ГАРАНТИЯ на новую машину?? ", "714ed756", "4e58f4da", marks)
    same = cache_key("sales", "какая гарантия на новую машину", "714ed756", "4e58f4da", marks)
    check(key == same, "регистр и пунктуация на ключ не влияют")

    check(cache_key("sales", "вопрос", "aaaaaaaa", "4e58f4da", marks)
          != cache_key("sales", "вопрос", "bbbbbbbb", "4e58f4da", marks),
          "правка промпта делает прошлый кэш недостижимым")
    check(cache_key("sales", "вопрос", "714ed756", "aaaaaaaa", marks)
          != cache_key("sales", "вопрос", "714ed756", "bbbbbbbb", marks),
          "правка базы знаний делает прошлый кэш недостижимым")
    check(cache_key("sales", "вопрос", "714ed756", "4e58f4da", "2026-09-18|открыт|впервые")
          != cache_key("sales", "вопрос", "714ed756", "4e58f4da", "2026-09-18|закрыт|впервые"),
          "ответ в рабочее и нерабочее время — разные записи кэша")

    payload = {"text": "Три года или 100 000 км.", "mode": "ОТВЕТ", "records": ["FAQ-SLS-011"],
               "prompt_version": "714ed756", "kb_version": "4e58f4da"}
    store.cache_put(key, "sales", "какая гарантия", payload, 600)
    hit = store.cache_get(key)
    check(hit and hit["text"] == payload["text"], "ответ достаётся из кэша")
    check(hit["records"] == ["FAQ-SLS-011"], "записи базы знаний сохраняются вместе с ответом")

    store.cache_put(key, "sales", "какая гарантия", payload, -1)  # уже просрочен
    check(store.cache_get(key) is None, "просроченная запись не выдаётся")

    store.cache_put(key, "sales", "вопрос", payload, 600)
    dropped = store.cache_drop_stale({"sales": "ffffffff"}, "4e58f4da")
    check(dropped == 1, "уборка выбрасывает кэш прошлых версий промпта")


def test_retention_and_forget() -> None:
    store = fresh()
    contact = store.identify("telegram-client", 5001)
    dialog = store.current_dialog(contact, "telegram-client")
    store.add_message(dialog, "in", "Мой телефон +7 900 000-21-01", route="sales")
    check(store.stats()["значений ПДн"] == 1, "значение ПДн сохранено отдельно от текста")

    store.conn.execute("UPDATE dialog SET last_at = ? WHERE id = ?",
                       ((datetime.now() - timedelta(days=200)).isoformat(), dialog))
    store.conn.commit()
    store.purge(days=90)
    check(store.stats()["диалогов"] == 0, "диалог старше срока хранения удалён")
    check(store.stats()["значений ПДн"] == 0, "значения ПДн удалены вместе с диалогом")

    other = store.identify("telegram-client", 5002)
    live = store.current_dialog(other, "telegram-client")
    store.add_message(live, "in", "Почта ivan@example.com", route="sales")
    store.forget(other)
    stats = store.stats()
    check(stats["контактов"] == 1 and stats["сообщений"] == 0,
          "забвение стирает контакт, диалоги и значения ПДн")
    check(store.conn.execute("SELECT COUNT(*) FROM channel_identity WHERE contact_id = ?",
                             (other,)).fetchone()[0] == 0,
          "адрес в канале тоже удаляется")


def main() -> int:
    for test in (test_pseudonymize, test_contacts, test_dialog_and_history,
                 test_restart_survives, test_returning_summary, test_cache,
                 test_retention_and_forget):
        test()
    print(f"Проверок выполнено: {passed + len(failed)}")
    if failed:
        print(f"\nПадений: {len(failed)}")
        for title in failed:
            print(f"  · {title}")
        return 1
    print("Падений нет. Хранилище ведёт себя как задумано.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
