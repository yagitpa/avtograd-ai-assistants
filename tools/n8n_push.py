"""Заливка сценариев из репозитория в n8n и чтение того, что там уже есть.

Сценарии живут в `n8n/workflows/` как JSON и попадают в инсталляцию отсюда,
а не наоборот. Причина та же, по которой промпты лежат в файлах: сценарий,
существующий только в чужом облаке, невоспроизводим, не рецензируем и
исчезает вместе с аккаунтом.

    python tools/n8n_push.py list       что уже есть в n8n
    python tools/n8n_push.py push       залить или обновить по имени
    python tools/n8n_push.py diff       чем инсталляция отличается от репозитория

Нужны `N8N_BASE_URL` (по умолчанию `http://localhost:5678`) и `N8N_API_KEY`
в `.env`. Ключ читается из окружения и никуда не печатается.

**Сценарии заливаются выключенными.** Включает их человек, посмотрев на
расписание: сценарий, который начал слать сообщения клиентам в момент
импорта, — это не автоматизация, а происшествие.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from assistant import load_env  # noqa: E402

WORKFLOWS = ROOT / "n8n" / "workflows"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def client():
    import httpx

    base = (os.environ.get("N8N_BASE_URL") or "http://localhost:5678").rstrip("/")
    key = os.environ.get("N8N_API_KEY")
    if not key:
        raise SystemExit(
            "Нет N8N_API_KEY. Создайте ключ в n8n: Settings → n8n API → Create an API key,\n"
            "затем добавьте в .env две строки:\n"
            "    N8N_BASE_URL=http://localhost:5678\n"
            "    N8N_API_KEY=<ключ>")
    return httpx.Client(base_url=base, timeout=30,
                        headers={"X-N8N-API-KEY": key,
                                 "ngrok-skip-browser-warning": "1"})


def local() -> list[tuple[Path, dict]]:
    return [(p, json.loads(p.read_text(encoding="utf-8")))
            for p in sorted(WORKFLOWS.glob("*.json"))]


def remote(http) -> dict[str, dict]:
    r = http.get("/api/v1/workflows", params={"limit": 250})
    r.raise_for_status()
    return {w["name"]: w for w in r.json().get("data", [])}


def payload(doc: dict) -> dict:
    """То, что принимает API: имя, узлы, связи, настройки.

    Теги и признак активности не передаются: первый — отдельная ручка, второй
    сознательно оставлен человеку.
    """
    return {"name": doc["name"], "nodes": doc["nodes"],
            "connections": doc["connections"], "settings": doc.get("settings", {})}


CRED_NAME_HINT = "автоград"


def core_credential(http) -> dict | None:
    """Учётная запись с ключом ядра, найденная по имени.

    Ищется по подстроке и без учёта регистра: имя вводит человек, и «АвтоГрад
    — Ядро» через тире вместо дефиса не повод падать. Значение ключа сюда не
    приезжает — API n8n отдаёт только идентификатор, имя и тип.
    """
    r = http.get("/api/v1/credentials")
    if r.status_code >= 400:
        return None
    for cred in r.json().get("data", []):
        if cred.get("type") == "httpHeaderAuth" and CRED_NAME_HINT in cred["name"].lower():
            return {"id": cred["id"], "name": cred["name"]}
    return None


def bind(nodes: list[dict], cred: dict | None) -> int:
    """Прописывает учётку узлам, которые ходят в ядро, и только им.

    Признак — адрес, собранный из узла «Настройки». Узел, который забирает
    фид у дилера, ключа ядра не получает: секрет не должен уезжать туда,
    где он не нужен, даже если адрес выглядит безобидно.
    """
    if not cred:
        return 0
    touched = 0
    for node in nodes:
        if not node.get("type", "").endswith("httpRequest"):
            continue
        if "Настройки" not in str(node.get("parameters", {}).get("url", "")):
            continue
        node["credentials"] = {"httpHeaderAuth": cred}
        touched += 1
    return touched


def cmd_list(http) -> int:
    existing = remote(http)
    if not existing:
        print("В n8n нет ни одного сценария.")
        return 0
    print(f"Сценариев в n8n: {len(existing)}")
    for name, wf in sorted(existing.items()):
        state = "включён" if wf.get("active") else "выключен"
        print(f"  · {name} — {state}, узлов {len(wf.get('nodes', []))}")
    return 0


def cmd_push(http) -> int:
    existing = remote(http)
    cred = core_credential(http)
    if cred:
        print(f"Учётная запись ядра: «{cred['name']}» — пропишу узлам, ходящим в ядро.")
    else:
        print("Учётной записи с «АвтоГрад» в имени нет — узлы останутся без ключа.")
    for path, doc in local():
        body = payload(doc)
        wired = bind(body["nodes"], cred)
        found = existing.get(doc["name"])
        if found:
            r = http.put(f"/api/v1/workflows/{found['id']}", json=body)
            verb = "обновлён"
        else:
            r = http.post("/api/v1/workflows", json=body)
            verb = "создан"
        if r.status_code >= 400:
            print(f"  · {doc['name']}: ОШИБКА {r.status_code} · {r.text[:200]}")
            continue
        print(f"  · {doc['name']} — {verb} (выключен), "
              f"узлов с ключом {wired}, из {path.name}")
    if not cred:
        print("\nУзлы остались без ключа. Создайте учётную запись Header Auth:\n"
              "  имя заголовка  X-API-Key\n"
              "  значение       AVTOGRAD_API_KEY из .env\n"
              "  название       со словом «АвтоГрад»\n"
              "Секрет вводите вы, а не код. Затем повторите push.")
    print("\nСценарии залиты выключенными: расписание включает человек, "
          "посмотрев на него.")
    return 0


def cmd_diff(http) -> int:
    existing = remote(http)
    mine = {doc["name"] for _, doc in local()}
    only_here = sorted(mine - set(existing))
    only_there = sorted(set(existing) - mine)
    both = sorted(mine & set(existing))

    print(f"В репозитории: {len(mine)} · в n8n: {len(existing)} · общих: {len(both)}")
    for name in only_here:
        print(f"  · только в репозитории: {name}")
    for name in only_there:
        print(f"  · только в n8n (сюда не заливалось): {name}")
    for name in both:
        here = next(d for _, d in local() if d["name"] == name)
        there = existing[name]
        same = len(here["nodes"]) == len(there.get("nodes", []))
        print(f"  · {name}: узлов {len(here['nodes'])} против "
              f"{len(there.get('nodes', []))}{'' if same else '  ← расходятся'}")
    return 0


def main() -> int:
    load_env()
    command = sys.argv[1] if len(sys.argv) > 1 else "list"
    if command not in ("list", "push", "diff"):
        print("Команды: list · push · diff")
        return 2
    with client() as http:
        return {"list": cmd_list, "push": cmd_push, "diff": cmd_diff}[command](http)


if __name__ == "__main__":
    sys.exit(main())
