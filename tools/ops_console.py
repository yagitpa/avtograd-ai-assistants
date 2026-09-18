"""Консоль эскалаций: третий бот, через который человек забирает диалог.

Отдельный бот, а не режим внутри HR-бота, по трём причинам:

* **разные аудитории** — внутренний контур открыт всем сотрудникам, консоль
  нужна узкому кругу: дежурный, руководители отделов, менеджер по качеству;
* **разные данные** — в карточке видны настоящие имя, телефон и VIN клиента,
  а кадровый канал таких данных не носит и носить не должен;
* **разные контуры** — диалог клиента принадлежит клиентскому контуру
  (ADR-0002), и консоль, которая им управляет, тоже. Внутри HR-бота она
  размыла бы границу, объявленную в архитектуре.

Побочная выгода: у чата сотрудника не появляется состояния «я сейчас говорю
с кадровиком или отвечаю клиенту». Один бот — одно назначение.

Что делает консоль:

1. показывает карточку эскалации с кнопками «Взять» и «Оставить ассистенту»;
2. по «Взять» переключает владение — ассистент немедленно замолкает,
   а клиенту уходит строка о том, что подключился человек;
3. пересылает реплики оператора клиенту и реплики клиента оператору;
4. возвращает диалог ассистенту по команде или по таймауту: оператор
   отвлёкся — клиент не должен остаться в тишине.

Операторы намеренно **не заводятся как контакты**: контакт — это клиент, а
работа оператора пишется в клиентский диалог полем владения. Иначе сотрудники
попали бы в ту же таблицу, что и клиенты, и статистика поехала бы.
"""

from __future__ import annotations

import os
from datetime import datetime

# Сколько минут диалог может молча висеть у оператора. Срок из карты
# эскалаций считается в рабочих часах, здесь же нужен грубый предохранитель:
# лучше вернуть разговор ассистенту, чем оставить клиента без ответа.
HOLD_MINUTES = int(os.environ.get("AVTOGRAD_HOLD_MINUTES", "30"))

OPS_GREETING = (
    "Консоль эскалаций «АвтоГрад».\n\n"
    "Сюда приходят обращения, которые ассистент передал человеку. "
    "Кнопка «Взять» переключает диалог на вас: ассистент замолкает, "
    "а всё, что вы пишете здесь, уходит клиенту.\n\n"
    "/queue — очередь · /status — что у вас в работе · /done — вернуть ассистенту"
)

CLIENT_HUMAN_JOINED = ("— к разговору подключился сотрудник «АвтоГрада» —")
CLIENT_HUMAN_LEFT = ("— дальше снова отвечает ассистент; сотрудник остаётся на связи "
                     "по вашему обращению —")


def env_ops_ids() -> set[int]:
    """Список операторов. Пустой означает «никого», как и во внутреннем боте."""
    raw = os.environ.get("AVTOGRAD_OPS_IDS", "")
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().isdigit()}


def card_keyboard(dialog_id: int) -> dict:
    return {"inline_keyboard": [[
        {"text": "Взять диалог", "callback_data": f"take:{dialog_id}"},
        {"text": "Оставить ассистенту", "callback_data": f"skip:{dialog_id}"},
    ]]}


def card_text(store, dialog_id: int, route: str | None, reason: str, contour: str) -> str:
    """Карточка эскалации: чего хватит, чтобы решить, вмешиваться ли.

    Две последние реплики, а не весь разговор: оператору нужно понять суть за
    несколько секунд, полная переписка доступна по запросу.
    """
    from router import ROUTE_NAMES
    tail = store.history(dialog_id, limit=4)
    lines = [f"Эскалация · диалог {dialog_id}",
             f"Контур: {'клиентский' if contour == 'client' else 'внутренний'}",
             f"Маршрут: {ROUTE_NAMES.get(route or '', 'не определён')}",
             f"Причина: {reason}", ""]
    for turn in tail[-4:]:
        who = "Клиент" if turn["role"] == "user" else "Ассистент"
        text = turn["content"]
        lines.append(f"{who}: {text[:300]}{'…' if len(text) > 300 else ''}")
    return "\n".join(lines)


def notify_operators(bot, store, dialog_id: int, route: str | None,
                     reason: str, contour: str = "client") -> None:
    """Рассылает карточку дежурным. Без адресата эскалация остаётся файлом."""
    if bot is None:
        return
    store.add_escalation(dialog_id, route, reason, contour)
    text = card_text(store, dialog_id, route, reason, contour)
    for operator_id in sorted(env_ops_ids()):
        bot.send(operator_id, text, keyboard=card_keyboard(dialog_id))


def relay_to_client(bots, store, dialog_id: int, text: str, operator: str) -> bool:
    """Отправляет реплику оператора клиенту в его же чат.

    Клиент остаётся там, где начал разговор: переход в другой бот означал бы,
    что человека переадресовали, а не помогли ему.
    """
    address = store.channel_address(dialog_id)
    if not address:
        return False
    channel, chat_id = address
    target = bots.get(channel)
    if target is None:
        return False
    target.send(int(chat_id), text)
    store.add_message(dialog_id, "out", text, route=store.route_of(dialog_id),
                      mode=f"ЧЕЛОВЕК ({operator})")
    return True


def handle_ops(bot, upd: dict) -> None:
    store = bot.store
    bots = bot.registry

    if "callback_query" in upd:
        cq = upd["callback_query"]
        user_id = cq.get("from", {}).get("id")
        chat_id = cq["message"]["chat"]["id"]
        bot.call("answerCallbackQuery", callback_query_id=cq["id"])
        if user_id not in env_ops_ids():
            return
        action, _, raw_id = cq.get("data", "").partition(":")
        if not raw_id.isdigit():
            return
        dialog_id = int(raw_id)
        operator = str(user_id)

        if action == "take":
            busy = store.take_escalation(dialog_id, operator)
            if busy:
                bot.send(chat_id, f"Диалог {dialog_id} уже ведёт оператор {busy}.")
                return
            relay_to_client(bots, store, dialog_id, CLIENT_HUMAN_JOINED, operator)
            bot.send(chat_id, f"Диалог {dialog_id} у вас. Всё, что напишете здесь, "
                              f"уйдёт клиенту. /done — вернуть ассистенту.")
            print(f"[{bot.label}] диалог {dialog_id} взял оператор {operator}")
        elif action == "skip":
            store.close_escalation(dialog_id, f"{operator} (оставлен ассистенту)")
            bot.send(chat_id, f"Диалог {dialog_id} остаётся у ассистента.")
        return

    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = msg.get("chat", {}).get("id")
    user_id = msg.get("from", {}).get("id")
    if not chat_id or not text:
        return

    # Посторонний не получает ответа вообще — как и во внутреннем боте.
    # Любая реплика подтверждает, что бот существует, и даёт повод пробовать.
    if user_id not in env_ops_ids():
        who = msg.get("from", {})
        handle = who.get("username")
        print(f"[{bot.label}] обращение отклонено молча: id {user_id}"
              + (f", @{handle}" if handle else ""))
        return

    operator = str(user_id)
    held = store.held_dialog(operator)

    if text.startswith("/start") or text.startswith("/help"):
        bot.send(chat_id, OPS_GREETING)
        return

    if text.startswith("/queue"):
        queue = store.open_escalations()
        if not queue:
            bot.send(chat_id, "Очередь пуста.")
            return
        for card in queue[:10]:
            bot.send(chat_id, card_text(store, card["dialog_id"], card["route"],
                                        card["reason"] or "", card["contour"]),
                     keyboard=card_keyboard(card["dialog_id"]))
        return

    if text.startswith("/status"):
        if held:
            bot.send(chat_id, f"У вас в работе диалог {held}. /done — вернуть ассистенту.")
        else:
            bot.send(chat_id, "У вас нет диалогов в работе. /queue — посмотреть очередь.")
        return

    if text.startswith("/done"):
        if not held:
            bot.send(chat_id, "У вас нет диалогов в работе.")
            return
        relay_to_client(bots, store, held, CLIENT_HUMAN_LEFT, operator)
        store.close_escalation(held, operator)
        bot.send(chat_id, f"Диалог {held} возвращён ассистенту.")
        print(f"[{bot.label}] диалог {held} возвращён оператором {operator}")
        return

    if not held:
        bot.send(chat_id, "Это консоль эскалаций, а не ассистент — на вопросы здесь "
                          "никто не отвечает. /queue — очередь, /status — что у вас в работе.")
        return

    if relay_to_client(bots, store, held, text, operator):
        print(f"[{bot.label}] оператор {operator} → клиент, диалог {held}")
    else:
        bot.send(chat_id, f"Не смог доставить сообщение в диалог {held}: "
                          f"канал клиента недоступен. Ответ не отправлен.")


def release_stale(bots, store) -> None:
    """Возвращает ассистенту диалоги, заброшенные оператором.

    Вызывается по таймеру. Молчание оператора не должно превращаться в
    молчание в адрес клиента: лучше ответ по регламенту, чем тишина.
    """
    for dialog in store.stale_holds(HOLD_MINUTES):
        dialog_id = int(dialog["id"])
        relay_to_client(bots, store, dialog_id, CLIENT_HUMAN_LEFT,
                        dialog.get("owner_by") or "—")
        store.close_escalation(dialog_id, "возврат по таймауту")
        print(f"[консоль] диалог {dialog_id} возвращён ассистенту: оператор молчал "
              f"дольше {HOLD_MINUTES} мин · {datetime.now():%H:%M}")
