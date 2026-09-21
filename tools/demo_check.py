"""Предполётная проверка перед демонстрацией.

Демо срывается не на содержании, а на мелочах: боты не подняты, сток
протух за ночь, в очереди висит вчерашняя эскалация, диалог остался за
оператором и ассистент молчит. Каждая из мелочей стоит минуты растерянности
перед заказчиком, а минута из десяти — это десять процентов встречи.

Проверка ничего не чинит сама. Она говорит, что не так и какой командой
это лечится: перед демонстрацией нужен человек, понимающий состояние
стенда, а не скрипт, тихо всё поправивший.

    python tools/demo_check.py

Код возврата 1 — есть препятствия. Вызовов модели ноль.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import cold  # noqa: E402
from assistant import KB_VERSION, PROMPT_VERSIONS, load_env  # noqa: E402
from ops_console import env_ops_ids  # noqa: E402
from store import Store  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

OK, WARN, BAD = "готово", "внимание", "мешает"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, state: str, what: str, detail: str, fix: str = "") -> None:
        self.rows.append((state, what, detail, fix))

    @property
    def blocking(self) -> list[tuple[str, str, str, str]]:
        return [r for r in self.rows if r[0] == BAD]


def check_env(rep: Report) -> None:
    """Наличие, не значения. Секреты не печатаются даже частично."""
    needed = {
        "OPENAI_API_KEY": ("ключ модели", True),
        "AVTOGRAD_CLIENT_BOT_TOKEN": ("клиентский бот", True),
        "AVTOGRAD_OPS_BOT_TOKEN": ("консоль эскалаций", True),
        "AVTOGRAD_STAFF_BOT_TOKEN": ("внутренний бот", False),
        "AVTOGRAD_API_KEY": ("ключ HTTP-ядра", True),
    }
    for var, (label, required) in needed.items():
        if os.environ.get(var, "").strip():
            rep.add(OK, label, "ключ задан")
        else:
            rep.add(BAD if required else WARN, label, f"{var} пуст",
                    f"впишите {var} в .env")

    ops = env_ops_ids()
    if ops:
        rep.add(OK, "дежурные консоли", f"адресатов: {len(ops)}")
    else:
        rep.add(BAD, "дежурные консоли", "AVTOGRAD_OPS_IDS пуст — карточка никому не уйдёт",
                "впишите свой telegram id в AVTOGRAD_OPS_IDS")

    if os.environ.get("AVTOGRAD_DIGEST_IDS", "").strip():
        rep.add(OK, "адресаты сводки", "заданы")
    else:
        rep.add(WARN, "адресаты сводки", "AVTOGRAD_DIGEST_IDS пуст — сводка вернётся "
                "в ответе, но никому не уйдёт", "впишите id, если показываете дайджест")


def check_core(rep: Report) -> None:
    import httpx

    port = os.environ.get("AVTOGRAD_API_PORT", "8080")
    url = f"http://127.0.0.1:{port}/v1/health"
    try:
        r = httpx.get(url, timeout=5)
        body = r.json()
        rep.add(OK, "HTTP-ядро", f"отвечает на {port}, знания {body.get('kb_version')}")
    except Exception as exc:
        rep.add(BAD, "HTTP-ядро", f"{type(exc).__name__} на {url}",
                "запустите: python tools/api.py")


def check_bots(rep: Report) -> None:
    """Токены проверяются через getMe: живой бот назовёт своё имя.

    Работает ли при этом опрос сообщений, отсюда не видно — процесс может
    быть не запущен. Об этом и сказано прямо, чтобы не создавать ложной
    уверенности: демо проваливается именно так.
    """
    import httpx

    for var, label in (("AVTOGRAD_CLIENT_BOT_TOKEN", "клиентский бот"),
                       ("AVTOGRAD_OPS_BOT_TOKEN", "консоль эскалаций")):
        token = os.environ.get(var, "").strip()
        if not token:
            continue
        try:
            r = httpx.post(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
            data = r.json()
            if data.get("ok"):
                rep.add(OK, label, f"@{data['result']['username']} — токен живой")
            else:
                rep.add(BAD, label, f"Telegram отверг токен: {data.get('description')}",
                        "перевыпустите токен у @BotFather")
        except Exception as exc:
            rep.add(WARN, label, f"сеть недоступна: {type(exc).__name__}")


def check_stock(rep: Report) -> None:
    state = cold.feed_state()
    age = state["age_minutes"]
    if state["stale"]:
        rep.add(BAD, "сток", f"устарел ({state['reason']}) — ассистент откажется "
                "отвечать по наличию", "python tools/cold.py fresh")
    else:
        rep.add(OK, "сток", f"свежий, выгрузка {age:.0f} мин назад")


def check_scene(rep: Report) -> None:
    """Чистота сцены: вчерашние хвосты ломают показ эскалации."""
    store = Store()

    queue = store.open_escalations()
    if queue:
        rep.add(WARN, "очередь эскалаций", f"висит карточек: {len(queue)} — новая "
                "потеряется среди старых",
                "закройте их в консоли кнопкой «Оставить ассистенту»")
    else:
        rep.add(OK, "очередь эскалаций", "пуста — карточка демо будет единственной")

    held = store.conn.execute(
        "SELECT id, owner_by FROM dialog WHERE owner = 'human_owned'").fetchall()
    if held:
        which = ", ".join(str(r["id"]) for r in held)
        rep.add(BAD, "владение диалогами", f"за оператором числятся: {which} — "
                "ассистент в них молчит",
                "верните их командой /done в консоли эскалаций")
    else:
        rep.add(OK, "владение диалогами", "все у ассистента")

    silent = store.silent_dialogs(hours=24)
    if silent:
        rep.add(OK, "молчащие диалоги", f"есть что показать холодному контуру: {silent}")
    else:
        rep.add(WARN, "молчащие диалоги", "пусто — сценарий касаний показать не на чем",
                "либо пропустите этот пункт, либо напишите боту и не отвечайте сутки")


def check_n8n(rep: Report) -> None:
    import httpx

    base = (os.environ.get("N8N_BASE_URL") or "").rstrip("/")
    key = os.environ.get("N8N_API_KEY", "").strip()
    if not base or not key:
        rep.add(WARN, "n8n", "адрес или ключ не заданы — холодный контур не показать")
        return
    try:
        r = httpx.get(f"{base}/api/v1/workflows", params={"limit": 250}, timeout=15,
                      headers={"X-N8N-API-KEY": key, "ngrok-skip-browser-warning": "1"})
        names = [w["name"] for w in r.json().get("data", [])
                 if w["name"].startswith(("01.", "02.", "03.", "04."))]
        if len(names) == 4:
            rep.add(OK, "n8n", "все четыре сценария на месте")
        else:
            rep.add(WARN, "n8n", f"сценариев найдено {len(names)} из 4",
                    "python tools/n8n_push.py push")
    except Exception as exc:
        rep.add(WARN, "n8n", f"{type(exc).__name__} — инсталляция недоступна")


def check_versions(rep: Report) -> None:
    """Версии в кадре: заказчик увидит их в отчётах, пусть сходятся."""
    import contextlib
    import io

    from versions import check as versions_check

    # versions.check печатает свой разбор — здесь нужен только итог,
    # иначе предполётный список тонет в чужом выводе.
    with contextlib.redirect_stdout(io.StringIO()):
        missing = versions_check()
    if missing:
        rep.add(WARN, "журнал промптов", f"нет записи о версиях: {', '.join(missing)}",
                "опишите версии в docs/prompts/CHANGELOG.md")
    else:
        versions = " · ".join(f"{r} {v}" for r, v in PROMPT_VERSIONS.items())
        rep.add(OK, "версии", f"{versions} · знания {KB_VERSION}")


def main() -> int:
    load_env()
    rep = Report()
    for step in (check_env, check_core, check_bots, check_stock,
                 check_scene, check_n8n, check_versions):
        try:
            step(rep)
        except Exception as exc:  # noqa: BLE001 — проверка не должна падать сама
            rep.add(WARN, step.__name__, f"проверка не отработала: "
                    f"{type(exc).__name__}: {exc}")

    width = max(len(r[1]) for r in rep.rows)
    for state, what, detail, fix in rep.rows:
        mark = {OK: " ", WARN: "~", BAD: "!"}[state]
        print(f" {mark} {what:<{width}}  {detail}")
        if fix:
            print(f"   {'':<{width}}  → {fix}")

    blocking = rep.blocking
    print()
    if blocking:
        print(f"Мешает начать: {len(blocking)}. Демонстрацию не начинайте, "
              "пока не закрыты.")
        return 1
    warns = sum(1 for r in rep.rows if r[0] == WARN)
    print("Стенд готов." + (f" Замечаний, не мешающих показу: {warns}." if warns else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
