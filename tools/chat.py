"""Запуск ассистента в диалоге.

Примеры:
  python tools/chat.py sales                      диалог с ассистентом продаж
  python tools/chat.py service --time 23:40       проверить поведение вне рабочих часов
  python tools/chat.py sales --stale              проверить правило устаревших данных
  python tools/chat.py hr --owner human_owned     проверить молчание при чужом владении
  python tools/chat.py sales -m "Vega Cross есть?"  один вопрос без диалога

Требуется переменная окружения OPENAI_API_KEY.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from assistant import ROLES, answer  # noqa: E402

# Консоль Windows по умолчанию cp1251 — символ рубля и кириллица в неё не влезают.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

GREY, BOLD, RESET = "\033[90m", "\033[1m", "\033[0m"


def parse_when(value: str | None) -> datetime:
    now = datetime.now()
    if not value:
        return now
    if len(value) == 5 and ":" in value:  # ЧЧ:ММ
        h, m = value.split(":")
        return now.replace(hour=int(h), minute=int(m))
    return datetime.fromisoformat(value)


def show(result: dict, verbose: bool) -> None:
    if result["mode"] == "МОЛЧАНИЕ":
        print(f"{GREY}[{result['mode']}] {result['note']}{RESET}")
        return
    if result["violations"]:
        print(f"{GREY}[валидатор заблокировал ответ: {', '.join(result['violations'])}]{RESET}")
        if verbose:
            print(f"{GREY}  что модель хотела отправить: {result['blocked'][:200]}{RESET}")
    print(f"{BOLD}Ассистент:{RESET} {result['text']}")
    if verbose and result["records"]:
        print(f"{GREY}  записи базы знаний: {', '.join(result['records'])}{RESET}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Диалог с ассистентом «АвтоГрада»")
    ap.add_argument("role", choices=sorted(ROLES), help="какой ассистент")
    ap.add_argument("-m", "--message", help="один вопрос вместо интерактивного диалога")
    ap.add_argument("--time", help="время диалога: ЧЧ:ММ или ISO-дата")
    ap.add_argument("--owner", default="bot_owned",
                    choices=["bot_owned", "human_owned", "returning"],
                    help="кто владеет диалогом (ADR-0004)")
    ap.add_argument("--stale", action="store_true", help="данные стока устарели")
    ap.add_argument("--model", default="gpt-5-mini")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="показывать найденные записи и работу валидатора")
    args = ap.parse_args()

    when = parse_when(args.time)
    kwargs = dict(now=when, owner=args.owner, stale=args.stale, model=args.model)

    print(f"{GREY}Ассистент «{ROLES[args.role]['name']}» · {when:%Y-%m-%d %H:%M} · "
          f"модель {args.model}"
          + (" · данные устарели" if args.stale else "")
          + (f" · владение: {args.owner}" if args.owner != "bot_owned" else "")
          + RESET)

    if args.message:
        show(answer(args.role, [{"role": "user", "content": args.message}], **kwargs),
             args.verbose)
        return 0

    print(f"{GREY}Пустая строка или Ctrl+C — выход.{RESET}\n")
    history: list[dict] = []
    while True:
        try:
            text = input(f"{BOLD}Вы:{RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            return 0
        history.append({"role": "user", "content": text})
        result = answer(args.role, history, **kwargs)
        show(result, args.verbose)
        if result["text"]:
            history.append({"role": "assistant", "content": result["text"]})
        print()


if __name__ == "__main__":
    sys.exit(main())
