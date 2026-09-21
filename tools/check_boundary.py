"""Протокол проверки границы: есть ли персональные данные в журналах n8n.

Критерий этапа 5 звучит так: «в логах n8n нет ни одного телефона и ни одного
имени клиента». Утверждение проверяемое, и проверять его должен код, а не
автор архитектуры, глядя на свою же схему.

Проверка идёт от противного. Мы не доказываем, что ядро отдаёт только номера
(это видно по исходнику и потому ничего не стоит), — мы берём то, что n8n
фактически записал у себя, и ищем там образцы персональных данных: шаблоны
телефона, VIN и e-mail плюс **настоящие имена из базы проекта**. Последнее
важнее шаблонов: имя не распознать регулярным выражением, зато можно сверить
со списком тех, чьи имена вообще могли бы утечь.

Два источника, потому что доступ к n8n есть не всегда:

    python tools/check_boundary.py                     через API n8n
    python tools/check_boundary.py executions.json     из выгруженного файла

Для первого нужны `N8N_BASE_URL` и `N8N_API_KEY` в `.env`. Ключ читается из
окружения и никуда не печатается.

Код возврата 1 — найдено нарушение ADR-0002.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from assistant import load_env  # noqa: E402

FIX = ROOT / "docs" / "knowledge-base" / "fixtures"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# Границы слова обязательны, и именно слова, а не разряда. Два ложных класса,
# найденные на первом же живом журнале:
#
#   1789994813661                      unix-время в мс — внутри читается «8 999 481 36 61»
#   4d1b696c4fc81606763771dabe6ba1dc   resumeToken n8n — тот же фокус, но соседи буквы
#
# Проверка, кричащая на каждую временную метку и каждый хеш, хуже отсутствующей:
# к ней привыкают и перестают читать. Ловушки заперты в tests/test_cold.py.
PATTERNS = {
    "телефон": re.compile(r"(?<!\w)(?:\+7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\w)"),
    "VIN": re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b"),
    "e-mail": re.compile(r"\b[\w.\-]+@[\w\-]+\.[a-zA-Zа-яА-Я]{2,}\b"),
    "госномер": re.compile(r"\b[АВЕКМНОРСТУХABEKMHOPCTYX]\s?\d{3}\s?"
                           r"[АВЕКМНОРСТУХABEKMHOPCTYX]{2}\s?\d{2,3}\b"),
}


def known_names() -> set[str]:
    """Имена и фамилии из фикстур — то, что вообще может утечь.

    Клиенты и кандидаты, но не сотрудники: имя руководителя сервиса в задаче
    холодного контура персональными данными клиента не является и появляться
    в журнале n8n имеет право.
    """
    names: set[str] = set()
    for file_name in ("customers.json", "candidates.json"):
        path = FIX / file_name
        if not path.exists():
            continue
        blob = json.loads(path.read_text(encoding="utf-8"))
        for chunk in re.findall(r'"(?:name|full_name|фио|имя)"\s*:\s*"([^"]+)"',
                                json.dumps(blob, ensure_ascii=False)):
            for part in chunk.split():
                if len(part) >= 4:
                    names.add(part.strip(".,«»\"'"))
    return names


def scan(text: str, names: set[str]) -> list[tuple[str, str]]:
    """Находки в одном куске журнала: вид нарушения и сам образец."""
    found: list[tuple[str, str]] = []
    for label, pattern in PATTERNS.items():
        for hit in pattern.findall(text):
            found.append((label, hit if isinstance(hit, str) else str(hit)))
    lowered = text.lower()
    for name in names:
        if name.lower() in lowered:
            found.append(("имя из базы", name))
    return found


# Наши сценарии узнаются по нумерованному имени. Инсталляция заказчика живёт
# своей жизнью: там есть сценарии, которые работают с телефонами совершенно
# законно. Аудит чужого контура — не предмет ADR-0002 и не наше дело.
OURS = ("01.", "02.", "03.", "04.")


def from_api(limit: int = 100, only_ours: bool = True) -> tuple[list[dict], dict[str, str]]:
    """Выполнения из n8n. Ключ берётся из окружения и не печатается."""
    import httpx

    base = (os.environ.get("N8N_BASE_URL") or "").rstrip("/")
    key = os.environ.get("N8N_API_KEY")
    if not base or not key:
        raise SystemExit("Нет N8N_BASE_URL или N8N_API_KEY в окружении — "
                         "либо задайте их в .env, либо передайте файл выгрузки.")
    headers = {"X-N8N-API-KEY": key, "ngrok-skip-browser-warning": "1"}
    with httpx.Client(timeout=60, headers=headers, follow_redirects=True) as client:
        flows = client.get(f"{base}/api/v1/workflows", params={"limit": 250})
        flows.raise_for_status()
        names = {w["id"]: w["name"] for w in flows.json().get("data", [])}

        listing = client.get(f"{base}/api/v1/executions",
                             params={"limit": limit, "includeData": "true"})
        listing.raise_for_status()
        runs = listing.json().get("data", [])

    if only_ours:
        runs = [r for r in runs
                if names.get(r.get("workflowId"), "").startswith(OURS)]
    return runs, names


def from_file(path: Path) -> list[dict]:
    blob = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(blob, dict):
        return blob.get("data", [blob])
    return blob


def main() -> int:
    load_env()
    args = [a for a in sys.argv[1:] if a != "--all"]
    only_ours = "--all" not in sys.argv
    names = known_names()

    if args:
        executions, flows = from_file(Path(args[0])), {}
    else:
        executions, flows = from_api(only_ours=only_ours)

    scope = "сценарии холодного контура" if only_ours else "весь журнал n8n"
    print(f"Область: {scope} · выполнений проверено: {len(executions)} · "
          f"имён в списке сверки: {len(names)}")
    if not executions:
        print("Журнал пуст: проверять нечего. Это не доказательство границы — "
              "запустите сценарии и повторите.")
        return 0

    violations: list[str] = []
    for item in executions:
        blob = json.dumps(item, ensure_ascii=False)
        flow = flows.get(item.get("workflowId")) or item.get("workflowId") or "без имени"
        where = f"выполнение {item.get('id', '?')} · {flow}"
        for label, sample in scan(blob, names):
            shown = sample if label == "имя из базы" else sample[:4] + "…"
            violations.append(f"{where}: {label} ({shown})")

    if violations:
        print(f"\nНАРУШЕНИЙ ГРАНИЦЫ: {len(violations)} — ADR-0002 не выполняется")
        for line in violations[:40]:
            print(f"  · {line}")
        if len(violations) > 40:
            print(f"  … и ещё {len(violations) - 40}")
        return 1

    print("Персональных данных в журналах n8n не найдено: граница ADR-0002 держится.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
