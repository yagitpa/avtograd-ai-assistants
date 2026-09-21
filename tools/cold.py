"""Холодный контур: фоновые задачи, которые никто не ждёт в диалоге.

Этап 5 по плану. Граница из ADR-0002 здесь перестаёт быть декларацией и
становится кодом: **n8n решает «когда», ядро решает «что» и «кому»**.

Практически это значит, что наружу отдаются только номера и числа:

* `silent_dialogs` возвращает номера диалогов, а не карточки клиентов;
* `followup` принимает номер и сам поднимает адрес из базы — n8n не видит
  ни телефона, ни имени, ни текста переписки;
* `digest` отдаёт счётчики, в которых персональных данных нет по устройству.

Проверяется это не на слово: `tools/check_boundary.py` читает журналы n8n и
ищет в них телефоны, VIN и имена из базы. Пустой результат — доказательство,
непустой — нарушение ADR-0002.

**Тексты касаний — шаблоны, а не вызовы модели.** Причины три. Фоновое
сообщение уходит без человека на другом конце, и цена ошибки в нём выше, чем
в диалоге, где клиент тут же переспросит. Шаблон воспроизводим: его можно
показать заказчику до отправки. И он ничего не стоит — напоминание сотне
молчащих лидов не должно превращаться в сотню обращений к модели.
"""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "docs" / "knowledge-base" / "fixtures"
STOCK = FIX / "stock.csv"
STOCK_META = FIX / "stock_meta.json"

# Столбцы стока, без которых ассистенту нечего сказать о машине. Фид, в
# котором их нет, не «частично пригоден» — он не принимается целиком:
# половина карточки хуже отсутствия карточки.
STOCK_REQUIRED = ("vin", "dealer_id", "make", "model", "year", "price_rub", "status")

FOLLOWUP_TEXTS = {
    "sales": ("Здравствуйте! Вы интересовались подбором автомобиля в «АвтоГраде». "
              "Если вопрос ещё актуален — напишите, я подниму подходящие варианты "
              "из наличия. Если уже не нужно, просто не отвечайте: больше "
              "напоминать не буду."),
    "service": ("Здравствуйте! Вы обращались в сервис «АвтоГрада». Если вопрос "
                "ещё открыт — напишите, продолжим. Если всё решилось, отвечать "
                "не нужно: это единственное напоминание."),
    "hr": ("Здравствуйте! Вы интересовались вакансиями «АвтоГрада». Если "
           "по-прежнему интересно — напишите, продолжим разговор. Если нет, "
           "отвечать не нужно."),
}
FOLLOWUP_DEFAULT = ("Здравствуйте! Вы недавно писали в «АвтоГрад». Если вопрос ещё "
                    "актуален — напишите, продолжим. Если нет, отвечать не нужно: "
                    "это единственное напоминание.")


# --------------------------------------------------------------------------
# Сток и его свежесть
# --------------------------------------------------------------------------

def feed_meta() -> dict:
    with open(STOCK_META, encoding="utf-8") as fh:
        return json.load(fh).get("feed", {})


def feed_state(now: datetime | None = None) -> dict:
    """Возраст фида и вердикт: можно ли отвечать по наличию.

    Отдельной функцией, а не флагом в вызове: до этапа 5 признак `stale`
    выставляли только вручную в `chat.py` и в эталонных прогонах. Правило
    «данные протухли — по наличию не отвечать» было записано в промпте и в
    сборке контекста, но в живом боте не включалось никогда, потому что
    включать его было некому.
    """
    now = now or datetime.now()
    meta = feed_meta()
    limit_hours = float(meta.get("stale_after_hours", 4))
    raw = meta.get("generated_at")
    if not raw:
        return {"generated_at": None, "age_minutes": None, "stale": True,
                "stale_after_hours": limit_hours, "reason": "в метаданных нет generated_at"}
    if not STOCK.exists():
        return {"generated_at": raw, "age_minutes": None, "stale": True,
                "stale_after_hours": limit_hours, "reason": "файл стока недоступен"}

    generated = datetime.fromisoformat(raw)
    if generated.tzinfo is not None:
        generated = generated.astimezone().replace(tzinfo=None)
    age = (now - generated).total_seconds() / 60
    stale = age > limit_hours * 60
    return {
        "generated_at": generated.isoformat(timespec="seconds"),
        "age_minutes": round(age, 1),
        "stale": stale,
        "stale_after_hours": limit_hours,
        "reason": f"фид старше {limit_hours:g} ч" if stale else "",
    }


def is_stale(now: datetime | None = None) -> bool:
    return bool(feed_state(now)["stale"])


def sync_stock(body: str, generated_at: datetime | None = None) -> dict:
    """Принимает свежий фид и отмечает время обновления.

    Разбор до записи, а не после: принятый и тут же отвергнутый фид оставил бы
    ассистента без стока вовсе, а это хуже устаревших данных — устаревшие он
    хотя бы умеет обходить.
    """
    reader = csv.DictReader(io.StringIO(body), delimiter=";")
    rows = list(reader)
    if not rows:
        raise ValueError("фид пуст — не принят")
    missing = [c for c in STOCK_REQUIRED if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"в фиде нет обязательных столбцов: {', '.join(missing)}")
    blank = [r.get("vin") for r in rows if not (r.get("vin") or "").strip()]
    if blank:
        raise ValueError(f"строк без VIN: {len(blank)} — фид не принят")

    stamp = (generated_at or datetime.now()).astimezone()
    STOCK.write_text(body if body.endswith("\n") else body + "\n", encoding="utf-8-sig")
    meta = json.loads(STOCK_META.read_text(encoding="utf-8"))
    meta.setdefault("feed", {})["generated_at"] = stamp.isoformat(timespec="seconds")
    STOCK_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    in_stock = sum(1 for r in rows if (r.get("status") or "").strip() == "in_stock")
    return {"принято_строк": len(rows), "в_наличии": in_stock,
            "generated_at": stamp.isoformat(timespec="seconds")}


# --------------------------------------------------------------------------
# Касания молчащих собеседников
# --------------------------------------------------------------------------

def followup_text(route: str | None) -> str:
    return FOLLOWUP_TEXTS.get(route or "", FOLLOWUP_DEFAULT)


def send_followup(bots, store, dialog_id: int, kind: str = "followup") -> dict:
    """Одно напоминание в диалог, где клиент замолчал.

    Проверки идут до отправки и в таком порядке, чтобы ни одна не могла быть
    обойдена повтором запроса от n8n: диалог не должен вести человек, касание
    не должно повторяться, адрес должен существовать.
    """
    if store.owner_of(dialog_id) != "bot_owned":
        return {"dialog_id": dialog_id, "sent": False, "reason": "диалог ведёт человек"}
    address = store.channel_address(dialog_id)
    if not address:
        return {"dialog_id": dialog_id, "sent": False, "reason": "нет адреса канала"}
    channel, chat_id = address
    bot = (bots or {}).get(channel)
    if bot is None:
        return {"dialog_id": dialog_id, "sent": False, "reason": f"канал {channel} недоступен"}
    if not store.mark_followup(dialog_id, kind):
        return {"dialog_id": dialog_id, "sent": False, "reason": "касание уже было"}

    route = store.route_of(dialog_id)
    text = followup_text(route)
    delivered = bot.send(int(chat_id), text)
    store.add_message(dialog_id, "out", text, route=route, mode=f"ХОЛОДНЫЙ КОНТУР ({kind})")
    if not delivered:
        # Отметка о касании остаётся: повторная попытка через минуту упрётся
        # в ту же причину, а клиент получит два сообщения, если она была
        # временной. Разбирается это по журналу, а не молчаливым ретраем.
        return {"dialog_id": dialog_id, "sent": False, "route": route,
                "reason": "канал не принял сообщение"}
    return {"dialog_id": dialog_id, "sent": True, "route": route}


# --------------------------------------------------------------------------
# Сводка руководителю
# --------------------------------------------------------------------------

def digest_text(data: dict, feed: dict) -> str:
    """Сводка словами. Чисел здесь ровно столько, сколько вернуло ядро."""
    routes = ", ".join(f"{name} — {count}"
                       for name, count in sorted(data.get("по_маршрутам", {}).items(),
                                                 key=lambda kv: -kv[1])) or "нет"
    feed_line = ("данные стока свежие" if not feed.get("stale")
                 else f"ВНИМАНИЕ: сток устарел ({feed.get('reason') or 'причина не указана'})")
    return (f"Сводка за {data.get('период_дней', 1)} дн.\n"
            f"Диалогов: {data.get('диалогов', 0)} · "
            f"сообщений от людей: {data.get('сообщений_клиентов', 0)}\n"
            f"Эскалаций: {data.get('эскалаций', 0)}, "
            f"из них открытых: {data.get('эскалаций_открытых', 0)}\n"
            f"По маршрутам: {routes}\n"
            f"{feed_line}")


def env_digest_ids() -> set[int]:
    """Кому уходит сводка. Пустой список означает «никому» — как везде в проекте."""
    raw = os.environ.get("AVTOGRAD_DIGEST_IDS", "")
    return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().isdigit()}


# Токены каналов по имени канала. Отправители строятся по требованию и не
# опрашивают Telegram: polling должен оставаться в одном процессе, отправлять
# же сообщение вправе любой. Благодаря этому холодный контур работает и тогда,
# когда боты подняты отдельным процессом, и тогда, когда их нет вовсе.
CHANNEL_TOKENS = {
    "telegram-client": "AVTOGRAD_CLIENT_BOT_TOKEN",
    "telegram-staff": "AVTOGRAD_STAFF_BOT_TOKEN",
    "telegram-ops": "AVTOGRAD_OPS_BOT_TOKEN",
}


def senders() -> dict:
    from telegram_bots import Bot
    built = {}
    for channel, var in CHANNEL_TOKENS.items():
        token = os.environ.get(var)
        if token:
            built[channel] = Bot(token, "холодный контур", channel)
    return built


def _restamp(hours_ago: float) -> dict:
    """Двигает отметку выгрузки, не трогая сами данные.

    Режимы из `stock_meta.json` («fresh», «stale») перестают быть описанием
    и становятся командой: сценарий «фид протух» должен воспроизводиться за
    одну секунду, иначе его не проверяют, а обсуждают.
    """
    stamp = (datetime.now() - timedelta(hours=hours_ago)).astimezone()
    meta = json.loads(STOCK_META.read_text(encoding="utf-8"))
    meta.setdefault("feed", {})["generated_at"] = stamp.isoformat(timespec="seconds")
    STOCK_META.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    return feed_state()


def main() -> int:
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    command = sys.argv[1] if len(sys.argv) > 1 else "state"
    if command == "fresh":
        state = _restamp(0)
    elif command == "stale":
        state = _restamp(float(feed_meta().get("stale_after_hours", 4)) + 1)
    elif command == "state":
        state = feed_state()
    else:
        print("Команды: state (по умолчанию) · fresh · stale")
        return 2

    verdict = ("сток устарел — по наличию и статусу ассистент не отвечает"
               if state["stale"] else "сток свежий — ассистент отвечает по наличию")
    age = state["age_minutes"]
    print(f"Выгрузка: {state['generated_at']} · возраст: "
          f"{'неизвестен' if age is None else f'{age:.0f} мин'}")
    print(verdict + (f" ({state['reason']})" if state["reason"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
