"""Версии промпта и базы знаний (ADR-0016).

Версия считается по содержимому, а не назначается руками: забыть поднять
номер невозможно, потому что поднимать нечего — хеш меняется вместе с
текстом. Версия промпта складывается из двух файлов сразу, каркаса и роли,
потому что именно их склейка уходит в модель.

Версия нужна в трёх местах:

* в логе ответа — чтобы через месяц можно было сказать, какая формулировка
  отвечала клиенту в конкретный вечер;
* в ключе кэша (ADR-0017) — чтобы исправленная красная линия не осталась
  жить в кэше на весь его срок;
* в журнале правок — запись обязательна, иначе изменение промпта
  неотличимо от случайного.

Запуск:
    python tools/versions.py            текущие версии
    python tools/versions.py --check    есть ли записи в журнале правок
"""

from __future__ import annotations

import hashlib
import sys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "docs" / "prompts"
KB = ROOT / "docs" / "knowledge-base"
CHANGELOG = PROMPTS / "CHANGELOG.md"

ROLE_FILES = {"sales": "sales.md", "service": "service.md", "hr": "hr.md"}

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


def digest(*chunks: str) -> str:
    h = hashlib.sha256()
    for chunk in chunks:
        # Перевод строки нормализуется: иначе одна и та же правка на Windows
        # и на Linux давала бы разные версии одного и того же текста.
        h.update(chunk.replace("\r\n", "\n").encode("utf-8"))
    return h.hexdigest()[:8]


def prompt_version(role: str) -> str:
    core = (PROMPTS / "_core.md").read_text(encoding="utf-8")
    part = (PROMPTS / ROLE_FILES[role]).read_text(encoding="utf-8")
    return digest(core, part)


def prompt_versions() -> dict[str, str]:
    return {role: prompt_version(role) for role in ROLE_FILES}


# Поля, которые меняются сами и знанием не являются. Время выгрузки стока —
# отметка свежести, а не факт о машинах: от него ответ не меняется, меняется
# только право на него отвечать, и это отдельный признак (`stale`).
#
# Без этого исключения версия знаний менялась каждые десять минут — при
# каждой синхронизации фида. А версия входит в ключ кэша, значит кэш,
# заведённый ради экономии (ADR-0017), протухал целиком с той же частотой
# и не экономил ничего.
VOLATILE = re.compile(r'"generated_at"\s*:\s*"[^"]*"')


def kb_version() -> str:
    """Версия слоя знаний: записи и фикстуры вместе.

    Фикстуры входят в версию, потому что ответ меняется и от них: цена в
    стоке правится без единого слова в промпте, а клиент видит другой ответ.
    Самообновляющиеся отметки времени — не входят.
    """
    files = sorted(list(KB.rglob("*.md")) + list(KB.rglob("*.json"))
                   + list(KB.rglob("*.csv")), key=lambda p: str(p).lower())
    return digest(*(VOLATILE.sub('"generated_at": ""', f.read_text(encoding="utf-8"))
                    for f in files))


def changelog_versions() -> set[str]:
    if not CHANGELOG.exists():
        return set()
    text = CHANGELOG.read_text(encoding="utf-8")
    return {token.strip("`") for token in text.split() if len(token.strip("`")) == 8
            and all(c in "0123456789abcdef" for c in token.strip("`"))}


def check() -> int:
    """Каждая текущая версия промпта должна быть описана в журнале правок."""
    recorded = changelog_versions()
    missing = {role: ver for role, ver in prompt_versions().items() if ver not in recorded}
    for role, ver in prompt_versions().items():
        mark = "есть в журнале" if ver in recorded else "НЕТ ЗАПИСИ В ЖУРНАЛЕ"
        print(f"  {role:8} {ver}  {mark}")
    print(f"  база знаний {kb_version()}")
    if missing:
        print("\nНе описаны правки промптов: "
              + ", ".join(f"{r} ({v})" for r, v in missing.items()))
        print(f"Добавьте запись в {CHANGELOG.relative_to(ROOT)}: что изменилось, "
              "почему и какой прогон это подтвердил.")
        return 1
    print("\nВсе текущие версии промптов описаны в журнале правок.")
    return 0


def main() -> int:
    if "--check" in sys.argv:
        return check()
    for role, ver in prompt_versions().items():
        print(f"{role:8} {ver}")
    print(f"{'знания':8} {kb_version()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
