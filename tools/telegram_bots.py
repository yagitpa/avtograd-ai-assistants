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

from assistant import ROLES, answer  # noqa: E402
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
    def __init__(self, token: str, label: str):
        self.token = token
        self.label = label
        self.offset = 0
        self.client = httpx.Client(timeout=70)
        self.sessions: dict[int, dict] = {}

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

    def session(self, chat_id: int) -> dict:
        return self.sessions.setdefault(chat_id, {"route": None, "history": []})

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


def trim(session: dict) -> None:
    session["history"] = session["history"][-HISTORY_LIMIT:]


def handle_client(bot: Bot, upd: dict) -> None:
    if "callback_query" in upd:
        cq = upd["callback_query"]
        chat_id = cq["message"]["chat"]["id"]
        bot.call("answerCallbackQuery", callback_query_id=cq["id"])
        chosen = cq.get("data", "").split(":")[-1]
        if chosen in ROLES:
            session = bot.session(chat_id)
            session["route"] = chosen
            bot.send(chat_id, f"{ROUTE_NAMES[chosen]}. Слушаю вас — что подсказать?")
        return

    msg = upd.get("message") or {}
    text = (msg.get("text") or "").strip()
    chat_id = msg.get("chat", {}).get("id")
    if not chat_id or not text:
        return

    session = bot.session(chat_id)

    if text.startswith("/start"):
        session["route"] = None
        session["history"] = []
        bot.send(chat_id, GREETING, keyboard=MENU)
        return
    if text.startswith("/menu"):
        bot.send(chat_id, "К кому вас направить?", keyboard=MENU)
        return

    decision = route(text, current=session["route"])

    if decision["ask"]:
        bot.send(chat_id, "Уточните, пожалуйста, с чем помочь — так я направлю вас точнее.",
                 keyboard=MENU)
        return

    if decision["switched"]:
        # Клиент должен понимать, что говорит уже с другим отделом.
        bot.send(chat_id, f"— перевожу: {ROUTE_NAMES[decision['route']]} —")
        session["history"] = []

    session["route"] = decision["route"]
    session["history"].append({"role": "user", "content": text})
    trim(session)

    bot.typing(chat_id)
    result = answer(session["route"], session["history"], now=datetime.now())

    if not result["text"]:
        return
    bot.send(chat_id, result["text"])
    session["history"].append({"role": "assistant", "content": result["text"]})
    trim(session)

    tag = f"[{bot.label}] chat {chat_id} · {session['route']}"
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
    allowed = env_staff_ids()
    if not allowed:
        bot.send(chat_id,
                 "Внутренний контур закрыт: список сотрудников не задан.\n"
                 f"Ваш идентификатор: {user_id}. Добавьте его в AVTOGRAD_STAFF_IDS "
                 "и перезапустите бота.")
        print(f"[{bot.label}] список сотрудников не задан, доступ закрыт "
              f"(обращался {user_id})")
        return
    if user_id not in allowed:
        bot.send(chat_id,
                 "Этот бот — внутренний контур отдела кадров, доступ по списку сотрудников.\n"
                 f"Ваш идентификатор: {user_id}. Передайте его администратору для добавления.")
        print(f"[{bot.label}] отказано в доступе: {user_id}")
        return
    session = bot.session(chat_id)
    if text.startswith("/start"):
        session["history"] = []
        bot.send(chat_id, STAFF_GREETING)
        return

    session["history"].append({"role": "user", "content": text})
    trim(session)
    bot.typing(chat_id)
    result = answer("hr", session["history"], now=datetime.now(), extra_context=STAFF_CONTEXT)
    if not result["text"]:
        return
    bot.send(chat_id, result["text"])
    session["history"].append({"role": "assistant", "content": result["text"]})
    trim(session)
    print(f"[{bot.label}] chat {chat_id} · внутренний контур")


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
            bot = Bot(client_token, "клиентский")
            threads.append(threading.Thread(target=bot.poll, args=(handle_client,), daemon=True))
    if which in ("all", "staff"):
        if not staff_token:
            print("Нет AVTOGRAD_STAFF_BOT_TOKEN — внутренний бот не запущен.")
        else:
            bot = Bot(staff_token, "внутренний")
            threads.append(threading.Thread(target=bot.poll, args=(handle_staff,), daemon=True))

    if not threads:
        print("Нечего запускать: не задан ни один токен.")
        return 1

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
