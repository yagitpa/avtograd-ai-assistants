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

PATTERNS = {
    "телефон": re.compile(r"(?:\+7|8)[\s(\-]*\d{3}[\s)\-]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}"),
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


def from_api(limit: int = 100) -> list[dict]:
    """Выполнения из n8n. Ключ берётся из окружения и не печатается."""
    import httpx

    base = (os.environ.get("N8N_BASE_URL") or "").rstrip("/")
    key = os.environ.get("N8N_API_KEY")
    if not base or not key:
        raise SystemExit("Нет N8N_BASE_URL или N8N_API_KEY в окружении — "
                         "либо задайте их в .env, либо передайте файл выгрузки.")
    headers = {"X-N8N-API-KEY": key, "ngrok-skip-browser-warning": "1"}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as client:
        listing = client.get(f"{base}/api/v1/executions",
                             params={"limit": limit, "includeData": "true"})
        listing.raise_for_status()
        return listing.json().get("data", [])


def from_file(path: Path) -> list[dict]:
    blob = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(blob, dict):
        return blob.get("data", [blob])
    return blob


def main() -> int:
    load_env()
    names = known_names()
    source = sys.argv[1] if len(sys.argv) > 1 else None
    executions = from_file(Path(source)) if source else from_api()

    print(f"Выполнений проверено: {len(executions)} · "
          f"имён в списке сверки: {len(names)}")
    if not executions:
        print("Журнал пуст: проверять нечего. Это не доказательство границы — "
              "запустите сценарии и повторите.")
        return 0

    violations: list[str] = []
    for item in executions:
        blob = json.dumps(item, ensure_ascii=False)
        where = f"выполнение {item.get('id', '?')} · {item.get('workflowId', 'без имени')}"
        for label, sample in scan(blob, names):
            shown = sample[:4] + "…" if label != "имя из базы" else sample
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
