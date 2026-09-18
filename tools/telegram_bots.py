"""Два бота «АвтоГрада»: клиентский и внутренний.

Клиентский бот — одна точка входа для трёх ассистентов: продажи, найм,
сервис. Маршрут определяет router.py; клиент, пришедший в сервис, может
тут же спросить про новые модели — маршрут переключится и бот об этом
скажет, чтобы человек понимал, с кем говорит.

Внутренний бот — только внутренний контур HR: онбординг, отпуск, справки.
Доступ по списку сотрудников: внутренние регламенты не должны открываться
любому, кто нашёл бота.

Запуск:
    python tools/telegram_bots.py            оба бота
    python tools/telegram_bots.py client     только клиентский
    python tools/telegram_bots.py staff      только внутренний

Переменные окружения:
    AVTOGRAD_CLIENT_BOT_TOKEN   токен клиентского бота
    AVTOGRAD_STAFF_BOT_TOKEN    токен внутреннего бота
    AVTOGRAD_STAFF_IDS          telegram id сотрудников через запятую
    OPENAI_API_KEY              ключ модели

Long polling, без вебхука: демо работает с ноутбука, белый IP и домен
не нужны (ADR-0003).
"""

from __future__ import annotations

import os
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from assistant import PROMPT_VERSIONS, KB_VERSION, ROLES, answer, load_env  # noqa: E402
from store import Store  # noqa: E402

load_env()
from router import ROUTE_NAMES, route  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

API = "https://api.telegram.org/bot{token}/{method}"
HISTORY_LIMIT = 12
TELEGRAM_LIMIT = 4000

GREETING = (
    "Здравствуйте! Это ассистент автоцентра «АвтоГрад».\n\n"
    "Помогу с тремя вещами:\n"
    "• подобрать автомобиль, узнать наличие и цены\n"
    "• ответить по вакансиям и откликам\n"
    "• записать в сервис, рассказать про ТО и гарантию\n\n"
    "Просто напишите, что нужно — сам пойму, к кому вас направить."
)

MENU = {"inline_keyboard": [
    [{"text": "Купить автомобиль", "callback_data": "route:sales"}],
    [{"text": "Работа и вакансии", "callback_data": "route:hr"}],
    [{"text": "Сервис и ТО", "callback_data": "route:service"}],
]}

STAFF_GREETING = (
    "Внутренний ассистент отдела кадров «АвтоГрад».\n\n"
    "Отвечу по отпуску, справкам, больничному, переносу смен и плану адаптации.\n"
    "Расчёты, юридические вопросы и спорные ситуации передаю HR бизнес-партнёру."
)

STAFF_CONTEXT = ("КОНТУР: внутренний — с вами общается действующий сотрудник компании. "
                 "Вопросы кандидатов в этом канале не обрабатываются.")


def env_staff_ids() -> set[int]:
    raw = os.environ.get("AVTOGRAD_STAFF_IDS", "")
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().isdigit()}


class Bot:
    def __init__(self, token: str, label: str, channel: str):
        self.token = token
        self.label = label
        # Канал — часть адреса человека: один и тот же telegram id в клиентском
        # и внутреннем боте это разные роли, и склеивать их нельзя.
        self.channel = channel
        self.offset = 0
        self.client = httpx.Client(timeout=70)
        # Своё подключение к базе на каждый бот: боты живут в разных потоках.
        self.store = Store()

    def call(self, method: str, **payload):
        try:
            r = self.client.post(API.format(token=self.token, method=method), json=payload)
            return r.json()
        except Exception as exc:  # сеть моргнула — цикл продолжится
            print(f"[{self.label}] ошибка {method}: {exc}")
            return {"ok": False}

    def send(self, chat_id: int, text: str, keyboard=None):
        for chunk in (text[i:i + TELEGRAM_LIMIT] for i in range(0, len(text), TELEGRAM_LIMIT)) or [""]:
            payload = {"chat_id": chat_id, "text": chunk}
            if keyboard:
                payload["reply_markup"] = keyboard
            self.call("sendMessage", **payload)

    def typing(self, chat_id: int):
        self.call("sendChatAction", chat_id=chat_id, action="typing")

    def poll(self, handler):
        me = self.call("getMe").get("result", {})
        print(f"[{self.label}] запущен как @{me.get('username', '?')}")
        while True:
            data = self.call("getUpdates", offset=self.offset, timeout=50)
            for upd in data.get("result", []):
                self.offset = upd["update_id"] + 1
                try:
                    handler(self, upd)
                except Exception:
                    traceback.print_exc()
            if not data.get("ok"):
                time.sleep(3)


def returning_note(store: Store, contact_id: int, dialog_id: int) -> str:
    """Строка контекста о вернувшемся человеке — без содержания прошлых разговоров.

    Требование заказчика: клиент, обращавшийся когда-то в сервис, должен иметь
    возможность говорить о новых моделях как в первый раз. Поэтому в контекст
    уходит только факт возвращения и давность, без темы: прошлый маршрут не
    должен подсказывать ассистенту, о чём человек хочет говорить сегодня.
    """
    summary = store.contact_summary(contact_id)
    if summary["dialogs"] <= 1:
        return ""
    try:
        days = (datetime.now() - datetime.fromisoformat(summary["last_at"])).days
    except (TypeError, ValueError):
        return ""
    when = "сегодня" if days == 0 else f"{days} дн. назад"
    return ("СОБЕСЕДНИК: обращался к нам раньше, последний раз " + when
            + ". Тему прошлого обращения не упоминать и не угадывать по ней текущую.")


def handle_client(bot: Bot, upd: dict) -> None:
    if "callback_query" in upd:
        cq = upd["callback_query"]
        chat_id = cq["message"]["chat"]["id"]
        user_id = cq.get("from", {}).get("id", chat_id)
        bot.call("answerCallbackQuery", callback_query_id=cq["id"])
        chosen = cq.get("data", "").split(":")[-1]
        if chosen in ROLES:
            contact_id = bot.store.identify(bot.channel, user_id,
                                            cq.get("from", {}).get("username", ""))
            dialog_id = bot.store.current_dialog(contact_id, bot.channel)
            bot.store.set_route(dialog_id, chosen)
            bot.send(chat_id, f"{ROUTE_NAMES[chosen]}. Слушаю вас — что подсказать?")
        return

    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = msg.get("chat", {}).get("id")
    who = msg.get("from", {})
    if not chat_id or not text:
        return

    contact_id = bot.store.identify(bot.channel, who.get("id", chat_id),
                                    who.get("username", ""))
    dialog_id = bot.store.current_dialog(contact_id, bot.channel)

    if text.startswith("/start"):
        bot.store.close_dialog(dialog_id)
        bot.send(chat_id, GREETING, keyboard=MENU)
        return
    if text.startswith("/menu"):
        bot.send(chat_id, "К кому вас направить?", keyboard=MENU)
        return

    current = bot.store.route_of(dialog_id)
    decision = route(text, current=current)

    if decision["ask"]:
        bot.send(chat_id, "Уточните, пожалуйста, с чем помочь — так я направлю вас точнее.",
                 keyboard=MENU)
        return

    if decision["switched"]:
        # Клиент должен понимать, что говорит уже с другим отделом.
        bot.send(chat_id, f"— перевожу: {ROUTE_NAMES[decision['route']]} —")

    chosen = decision["route"]
    bot.store.set_route(dialog_id, chosen)
    bot.store.add_message(dialog_id, "in", text, route=chosen)

    history = bot.store.history(dialog_id, limit=HISTORY_LIMIT, route=chosen)

    bot.typing(chat_id)
    result = answer(chosen, history, now=datetime.now(), cache=bot.store,
                    extra_context=returning_note(bot.store, contact_id, dialog_id))

    if not result["text"]:
        return
    bot.send(chat_id, result["text"])
    bot.store.add_message(dialog_id, "out", result["text"], route=chosen,
                          mode=result.get("mode", ""),
                          prompt_version=result.get("prompt_version", ""),
                          kb_version=result.get("kb_version", ""),
                          records=result.get("records"))

    tag = (f"[{bot.label}] контакт {contact_id} · диалог {dialog_id} · {chosen}"
           f" · промпт {result.get('prompt_version', '—')}")
    if result.get("cached"):
        tag += " · из кэша"
    if decision["switched"]:
        tag += " · переключение"
    if result["violations"]:
        tag += f" · валидатор заблокировал: {', '.join(result['violations'])}"
    print(tag)


def handle_staff(bot: Bot, upd: dict) -> None:
    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = msg.get("chat", {}).get("id")
    user_id = msg.get("from", {}).get("id")
    if not chat_id or not text:
        return

    # Внутренний контур закрыт по умолчанию: пустой список означает «никого»,
    # а не «всех». Иначе забытая переменная открывает регламенты компании любому.
    #
    # Посторонний не получает ответа вообще. Любая реплика — даже отказ —
    # подтверждает, что бот существует и работает, и даёт повод пробовать
    # дальше. Идентификатор пишется в журнал: сотрудника администратор
    # добавит оттуда, а не со слов обратившегося.
    allowed = env_staff_ids()
    if not allowed or user_id not in allowed:
        who = msg.get("from", {})
        handle = who.get("username")
        reason = "список сотрудников не задан" if not allowed else "нет в списке"
        print(f"[{bot.label}] обращение отклонено молча ({reason}): id {user_id}"
              + (f", @{handle}" if handle else ""))
        return

    contact_id = bot.store.identify(bot.channel, user_id,
                                    msg.get("from", {}).get("username", ""))
    dialog_id = bot.store.current_dialog(contact_id, bot.channel)

    if text.startswith("/start"):
        bot.store.close_dialog(dialog_id)
        bot.send(chat_id, STAFF_GREETING)
        return

    bot.store.add_message(dialog_id, "in", text, route="hr")
    history = bot.store.history(dialog_id, limit=HISTORY_LIMIT, route="hr")

    bot.typing(chat_id)
    # Внутренний контур мимо кэша: вопросы сотрудников про отпуск и справки
    # почти всегда касаются их самих, а выигрыш от кэша здесь исчезающе мал.
    result = answer("hr", history, now=datetime.now(), extra_context=STAFF_CONTEXT)
    if not result["text"]:
        return
    bot.send(chat_id, result["text"])
    bot.store.add_message(dialog_id, "out", result["text"], route="hr",
                          mode=result.get("mode", ""),
                          prompt_version=result.get("prompt_version", ""),
                          kb_version=result.get("kb_version", ""),
                          records=result.get("records"))
    print(f"[{bot.label}] контакт {contact_id} · диалог {dialog_id} · внутренний контур"
          f" · промпт {result.get('prompt_version', '—')}")


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    client_token = os.environ.get("AVTOGRAD_CLIENT_BOT_TOKEN")
    staff_token = os.environ.get("AVTOGRAD_STAFF_BOT_TOKEN")

    if not os.environ.get("OPENAI_API_KEY"):
        print("Нет OPENAI_API_KEY — ассистенты отвечать не смогут.")
        return 1

    threads = []
    if which in ("all", "client"):
        if not client_token:
            print("Нет AVTOGRAD_CLIENT_BOT_TOKEN — клиентский бот не запущен.")
        else:
            bot = Bot(client_token, "клиентский", "telegram-client")
            threads.append(threading.Thread(target=bot.poll, args=(handle_client,), daemon=True))
    if which in ("all", "staff"):
        if not staff_token:
            print("Нет AVTOGRAD_STAFF_BOT_TOKEN — внутренний бот не запущен.")
        else:
            bot = Bot(staff_token, "внутренний", "telegram-staff")
            threads.append(threading.Thread(target=bot.poll, args=(handle_staff,), daemon=True))

    if not threads:
        print("Нечего запускать: не задан ни один токен.")
        return 1

    # Уборка кэша чужих версий. На правильность не влияет — версия входит в
    # ключ, — но не даёт базе копить мусор после каждой правки промпта.
    housekeeping = Store()
    dropped = housekeeping.cache_drop_stale(PROMPT_VERSIONS, KB_VERSION)
    aged = housekeeping.purge()
    print(f"Версии промптов: " + ", ".join(f"{r} {v}" for r, v in PROMPT_VERSIONS.items())
          + f" · знания {KB_VERSION}")
    if dropped or aged["dialogs"] or aged["cache"]:
        print(f"Уборка: кэш прошлых версий {dropped}, просроченный кэш {aged['cache']}, "
              f"диалогов старше срока хранения {aged['dialogs']}")
    housekeeping.close()

    for t in threads:
        t.start()
    print("Боты работают. Ctrl+C — остановить.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nОстановлено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
