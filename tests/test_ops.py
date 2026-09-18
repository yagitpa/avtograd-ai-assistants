"""Тесты консоли эскалаций: очередь, перехват, пересылка, возврат.

Telegram не вызывается: вместо ботов — заглушки, запоминающие отправленное.
Проверяется то, ради чего консоль и делалась: что клиент получает ответ
человека в свой чат, что двое операторов не берут один диалог и что
заброшенный разговор возвращается ассистенту.

Запуск:  python tests/test_ops.py
Код возврата 1 — есть падения.
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

os.environ["AVTOGRAD_OPS_IDS"] = "501,502"

import ops_console  # noqa: E402
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
    """Бот-заглушка: вместо Telegram складывает отправленное в список."""

    def __init__(self, store, label="консоль", channel="telegram-ops"):
        self.store = store
        self.label = label
        self.channel = channel
        self.sent: list[tuple[int, str]] = []
        self.registry: dict = {}

    def send(self, chat_id, text, keyboard=None):
        self.sent.append((int(chat_id), text))

    def call(self, method, **payload):
        return {"ok": True}

    def last(self) -> str:
        return self.sent[-1][1] if self.sent else ""


def scene():
    """Клиент с открытым диалогом, консоль и клиентский бот."""
    store = Store(Path(tempfile.mkdtemp()) / "ops.db")
    contact = store.identify("telegram-client", 9001, "client")
    dialog = store.current_dialog(contact, "telegram-client")
    store.set_route(dialog, "service")
    store.add_message(dialog, "in", "Третий раз сдаю машину с одним стуком", route="service")
    store.add_message(dialog, "out", "Передаю обращение руководителю сервиса.",
                      route="service", mode="ЭСКАЛАЦИЯ (валидатор)")

    ops = FakeBot(store)
    client_bot = FakeBot(store, "клиентский", "telegram-client")
    ops.registry = {"telegram-ops": ops, "telegram-client": client_bot}
    return store, dialog, ops, client_bot


def upd_message(user_id: int, text: str) -> dict:
    return {"message": {"text": text, "chat": {"id": user_id},
                        "from": {"id": user_id, "username": "op"}}}


def upd_callback(user_id: int, data: str) -> dict:
    return {"callback_query": {"id": "cb-1", "data": data,
                               "from": {"id": user_id},
                               "message": {"chat": {"id": user_id}}}}


def test_card_goes_to_operators() -> None:
    store, dialog, ops, _client = scene()
    ops_console.notify_operators(ops, store, dialog, "service", "ЭСКАЛАЦИЯ (ESC-003)")
    check(len(ops.sent) == 2, "карточка ушла обоим дежурным")
    card = ops.last()
    check(f"диалог {dialog}" in card, "в карточке есть номер диалога")
    check("Сервис" in card, "в карточке назван маршрут")
    check("стуком" in card, "в карточке видна последняя реплика клиента")
    check(len(store.open_escalations()) == 1, "карточка встала в очередь")

    ops_console.notify_operators(ops, store, dialog, "service", "ещё одна")
    check(len(store.open_escalations()) == 1, "повторная эскалация не плодит карточки")


def test_take_and_relay() -> None:
    store, dialog, ops, client_bot = scene()
    ops_console.notify_operators(ops, store, dialog, "service", "ЭСКАЛАЦИЯ (ESC-003)")

    ops_console.handle_ops(ops, upd_callback(501, f"take:{dialog}"))
    check(store.owner_of(dialog) == "human_owned", "диалог перешёл оператору")
    check(any("подключился сотрудник" in text for _, text in client_bot.sent),
          "клиенту сказано, что подключился человек")

    ops_console.handle_ops(ops, upd_message(501, "Здравствуйте, это Ирина, разберусь сама."))
    check(client_bot.sent[-1][0] == 9001, "ответ ушёл в чат клиента")
    check("Ирина" in client_bot.sent[-1][1], "ответ оператора доставлен дословно")

    saved = store.history(dialog, limit=10)[-1]
    check(saved["content"].startswith("Здравствуйте, это Ирина"),
          "реплика человека сохранена в диалоге")
    row = store.conn.execute(
        "SELECT mode FROM message WHERE dialog_id = ? ORDER BY id DESC LIMIT 1",
        (dialog,)).fetchone()
    check(row["mode"].startswith("ЧЕЛОВЕК"), "в записи видно, что отвечал человек")


def test_client_reaches_operator() -> None:
    """Перехват обязан работать в обе стороны.

    Регресс живой проверки 19 сентября: оператор забрал диалог, написал
    клиенту, клиент ответил — и ответ осел в базе. Ассистент молчал по праву
    владения, оператор не видел ни слова. Клиент разговаривал с пустотой.
    """
    store, dialog, ops, _client = scene()
    ops_console.handle_ops(ops, upd_callback(501, f"take:{dialog}"))
    ops.sent.clear()

    delivered = ops_console.relay_to_operator(ops, store, dialog, "Стук тот же, что и в прошлый раз")
    check(delivered, "реплика клиента доставлена")
    check(ops.sent[-1][0] == 501, "доставлена именно тому, кто держит диалог")
    check("Стук тот же" in ops.last(), "текст клиента передан дословно")
    check(str(dialog) in ops.last(), "в пересылке виден номер диалога")

    check(store.holder_of(dialog) == "501", "держатель диалога определяется по номеру")
    ops_console.handle_ops(ops, upd_message(501, "/done"))
    check(store.holder_of(dialog) is None, "после возврата держателя нет")
    ops.sent.clear()
    check(not ops_console.relay_to_operator(ops, store, dialog, "И ещё вопрос"),
          "диалог у ассистента — оператору ничего не шлётся")
    check(not ops.sent, "лишнего сообщения дежурному не ушло")


def test_one_dialog_one_owner() -> None:
    store, dialog, ops, _client = scene()
    ops_console.handle_ops(ops, upd_callback(501, f"take:{dialog}"))
    ops.sent.clear()
    ops_console.handle_ops(ops, upd_callback(502, f"take:{dialog}"))
    check("уже ведёт" in ops.last(), "второму оператору сказано, кто ведёт диалог")
    check(store.held_dialog("502") is None, "второй оператор диалог не получил")
    check(store.held_dialog("501") == dialog, "диалог остался у первого")


def test_stranger_gets_silence() -> None:
    store, dialog, ops, _client = scene()
    ops_console.handle_ops(ops, upd_message(777, "Что тут происходит?"))
    check(not ops.sent, "посторонний не получает ответа вообще")


def test_done_returns_dialog() -> None:
    store, dialog, ops, client_bot = scene()
    ops_console.handle_ops(ops, upd_callback(501, f"take:{dialog}"))
    ops_console.handle_ops(ops, upd_message(501, "/done"))
    check(store.owner_of(dialog) == "bot_owned", "диалог вернулся ассистенту")
    check(any("снова отвечает ассистент" in text for _, text in client_bot.sent),
          "клиент предупреждён о возврате")
    check(not store.open_escalations(), "карточка закрыта")


def test_stale_hold_released() -> None:
    store, dialog, ops, client_bot = scene()
    ops_console.handle_ops(ops, upd_callback(501, f"take:{dialog}"))
    store.conn.execute("UPDATE dialog SET last_at = ? WHERE id = ?",
                       ((datetime.now() - timedelta(hours=2)).isoformat(), dialog))
    store.conn.commit()

    ops_console.release_stale(ops.registry, store)
    check(store.owner_of(dialog) == "bot_owned",
          "заброшенный диалог возвращён ассистенту")
    check(any("снова отвечает ассистент" in text for _, text in client_bot.sent),
          "клиент не остался в тишине")


def test_message_without_dialog() -> None:
    store, dialog, ops, _client = scene()
    ops_console.handle_ops(ops, upd_message(502, "Привет, как дела?"))
    check("не ассистент" in ops.last(), "консоль не притворяется ассистентом")


def main() -> int:
    for test in (test_card_goes_to_operators, test_take_and_relay, test_one_dialog_one_owner,
                 test_stranger_gets_silence, test_done_returns_dialog,
                 test_client_reaches_operator,
                 test_stale_hold_released, test_message_without_dialog):
        test()
    print(f"Проверок выполнено: {passed + len(failed)}")
    if failed:
        print(f"\nПадений: {len(failed)}")
        for title in failed:
            print(f"  · {title}")
        return 1
    print("Падений нет. Консоль передаёт диалог человеку и возвращает обратно.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
