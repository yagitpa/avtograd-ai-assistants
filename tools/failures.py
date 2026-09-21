"""Намеренные отказы: проверка лестницы деградации L0–L4.

Этап 6 по плану. Проверяется не то, что система работает, а то, как она
ломается. На фикстурах это делается за секунду; на живой системе отключить
DMS или обоих провайдеров модели почти невозможно — а именно эти сценарии
и решают, получит ли клиент ответ в плохой день.

**Ни одного платного вызова.** Модель подменяется заглушкой: она либо
послушно отвечает, либо падает, либо возвращает пустоту — по сценарию.
Проверяется при этом настоящий код ядра, а не его пересказ.

    python tools/failures.py            прогнать и напечатать
    python tools/failures.py --report   плюс записать docs/failures/report.md

Лестница объявлена в `docs/architecture/`:

    L0  ничего не отказало      полный ответ
    L1  DMS или сток-фид        ответ с оговоркой, без выдуманного статуса
    L2  основной LLM            переход на резервного провайдера, прозрачно
    L3  все LLM                 шаблонный ответ, диалог в очередь оператору
    L4  ядро целиком            автоответ из канала, сообщение в очередь

Код возврата 1 — на каком-то уровне клиент остался без ответа. Это и есть
критерий приёмки этапа: «ни при одном из отказов клиент не остался без
ответа». Уровень, не реализованный вовсе, отмечается отдельно и провалом
не считается — он считается долгом.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import assistant  # noqa: E402
import cold  # noqa: E402
from ports import FixtureDms  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass


# --------------------------------------------------------------------------
# Заглушка модели
# --------------------------------------------------------------------------

class StubReply:
    def __init__(self, text: str):
        self.choices = [type("C", (), {"message": type("M", (), {"content": text})()})()]


class StubLLM:
    """Модель, которой можно приказать ответить, промолчать или упасть.

    Запоминает, что ей прислали: половина проверок этого стенда — не про
    ответ клиенту, а про то, получила ли модель указание о недоступности
    данных. Оговорку в ответе пишет модель, но основание для неё обязано
    прийти из кода.
    """

    def __init__(self, text: str = "", fail: bool = False, label: str = "основной"):
        self.text = text
        self.fail = fail
        self.label = label
        self.calls = 0
        self.seen: list[str] = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, model=None, messages=None, **kwargs):
        self.calls += 1
        self.seen = [m.get("content", "") for m in (messages or [])]
        if self.fail:
            raise RuntimeError(f"провайдер «{self.label}» недоступен")
        return StubReply(self.text)

    def prompt_has(self, *needles: str) -> bool:
        blob = "\n".join(self.seen).lower()
        return all(n.lower() in blob for n in needles)


ANSWER_OK = ("Стоимость ТО-2 для вашей модели — 18 900 ₽, занимает около трёх часов. "
             "Записать вас на свободное время?\n#РЕЖИМ: ОТВЕТ")
ANSWER_HONEST = ("Сейчас не могу поднять карточку автомобиля: система обслуживания не "
                 "отвечает. Статус и сроки назвать не берусь — передам мастеру, он "
                 "свяжется с вами.\n#РЕЖИМ: ЭСКАЛАЦИЯ")


def ask(stub: StubLLM, question: str, role: str = "service", **kwargs) -> dict:
    """Один вопрос к ядру с подменённым клиентом модели."""
    original = assistant.llm_client
    assistant.llm_client = lambda: stub
    try:
        return assistant.answer(role, [{"role": "user", "content": question}], **kwargs)
    finally:
        assistant.llm_client = original


# --------------------------------------------------------------------------
# Сценарии отказов
# --------------------------------------------------------------------------

def l0_nothing_broken() -> dict:
    stub = StubLLM(ANSWER_OK)
    result = ask(stub, "Сколько стоит ТО-2?")
    return {
        "уровень": "L0", "что отказало": "ничего",
        "клиент получил": result["text"],
        "режим": result["mode"],
        "ответ есть": bool(result["text"]),
        "разбор": "Опорная точка: без неё непонятно, деградация перед нами или обычный ответ.",
    }


def l1_dms_down() -> dict:
    stub = StubLLM(ANSWER_HONEST)
    result = ask(stub, "Что с моей машиной? VIN TESTAG00000000910",
                 dms=FixtureDms(fail=True))
    told = stub.prompt_has("система обслуживания сейчас не отвечает", "статус и сроки не называть")
    return {
        "уровень": "L1", "что отказало": "DMS дилера",
        "клиент получил": result["text"],
        "режим": result["mode"],
        "ответ есть": bool(result["text"]),
        "разбор": ("Ядро сказало модели о недоступности прямым указанием — "
                   if told else "ЯДРО НЕ ПРЕДУПРЕДИЛО МОДЕЛЬ — ")
                  + "оговорку пишет модель, но основание обязано прийти из кода.",
        "исправен": told,
    }


def l1_stock_stale() -> dict:
    stub = StubLLM("По наличию сейчас не отвечу: данные обновляются.\n#РЕЖИМ: УТОЧНЕНИЕ")
    result = ask(stub, "Aurora X5 есть в наличии?", role="sales", stale=True)
    told = stub.prompt_has("данные стока устарели", "по наличию и статусу не отвечать")
    return {
        "уровень": "L1", "что отказало": "сток-фид (данные старше 4 ч)",
        "клиент получил": result["text"],
        "режим": result["mode"],
        "ответ есть": bool(result["text"]),
        "разбор": ("Запрет отвечать по наличию доехал до модели. "
                   if told else "ЗАПРЕТ НЕ ДОЕХАЛ ДО МОДЕЛИ. ")
                  + "Неверный статус хуже отсутствия: он превращается в обещание.",
        "исправен": told,
    }


def l2_primary_llm_down() -> dict:
    """Лестница обещает прозрачный переход на резервного провайдера."""
    primary = StubLLM(fail=True, label="основной")
    backup = StubLLM(ANSWER_OK, label="резервный")

    original = assistant.llm_backup
    assistant.llm_backup = lambda: (backup, "backup-model")
    try:
        result = ask(primary, "Сколько стоит ТО-2?")
    finally:
        assistant.llm_backup = original

    switched = not result["mode"].startswith("ЭСКАЛАЦИЯ") and backup.calls > 0
    return {
        "уровень": "L2", "что отказало": "основной провайдер модели",
        "клиент получил": result["text"],
        "режим": result["mode"],
        "ответ есть": bool(result["text"]),
        "разбор": (f"Основного дёрнули {primary.calls} раза, затем резервного — "
                   f"{backup.calls}. Клиент получил обычный ответ и о переключении "
                   "не узнал. Промпт и знания те же: ADR-0005 обещает переносимость "
                   "без правки текстов, здесь это обещание и используется."
                   if switched else
                   "Переключения не произошло: ядро ушло сразу на L3. Клиент без "
                   "ответа не остался, но вместо ответа получил эскалацию."),
        "исправен": switched,
        "попыток к провайдеру": primary.calls,
    }


def l2_no_backup_configured() -> dict:
    """Резерв не настроен — законное состояние, лестница просто короче."""
    primary = StubLLM(fail=True, label="единственный")
    original = assistant.llm_backup
    assistant.llm_backup = lambda: (None, None)
    try:
        result = ask(primary, "Сколько стоит ТО-2?")
    finally:
        assistant.llm_backup = original
    return {
        "уровень": "L2", "что отказало": "провайдер модели, резерв не настроен",
        "клиент получил": result["text"],
        "режим": result["mode"],
        "ответ есть": bool(result["text"]),
        "разбор": ("Резерва в окружении нет — ядро честно уходит на L3, не притворяясь, "
                   "что ступень существует. Заказчик вправе жить на одном провайдере; "
                   "цена этого решения видна здесь: вместо ответа эскалация."),
        "исправен": bool(result["text"]),
    }


def l3_all_llm_down() -> dict:
    stub = StubLLM(fail=True, label="все провайдеры")
    result = ask(stub, "Сколько стоит ТО-2?")
    return {
        "уровень": "L3", "что отказало": "все провайдеры модели",
        "клиент получил": result["text"],
        "режим": result["mode"],
        "ответ есть": bool(result["text"]),
        "разбор": ("Шаблонный ответ и режим ЭСКАЛАЦИЯ — значит, карточка уйдёт "
                   "дежурному: боты вызывают консоль по префиксу режима."),
        "исправен": bool(result["text"]) and result["mode"].startswith("ЭСКАЛАЦИЯ"),
    }


def l3_empty_reply() -> dict:
    """Отдельный случай: модель отвечает, но пустотой. Видели живьём."""
    stub = StubLLM("")
    result = ask(stub, "Сколько стоит ТО-2?")
    return {
        "уровень": "L3", "что отказало": "модель вернула пустой текст",
        "клиент получил": result["text"],
        "режим": result["mode"],
        "ответ есть": bool(result["text"]),
        "разбор": (f"Повторных попыток: {stub.calls}. Молчание в ответ клиенту "
                   "неотличимо от поломки, поэтому одна повторная попытка и честная "
                   "деградация."),
        "исправен": bool(result["text"]),
    }


def l4_core_crash() -> dict:
    """Ядро падает целиком — не отказ модели, а исключение в самом коде."""
    def explode():
        raise MemoryError("ядро упало")

    original = assistant.llm_client
    assistant.llm_client = explode
    crashed = False
    text = ""
    mode = "—"
    try:
        # Каналы зовут ядро через страховку: L1–L3 удерживает ядро,
        # L4 удерживает канал. Здесь проверяется именно канальная граница.
        result = assistant.safe_answer(
            "service", [{"role": "user", "content": "Сколько стоит ТО-2?"}])
        text, mode = result["text"], result["mode"]
    except Exception as exc:  # noqa: BLE001 — ровно это и проверяем
        crashed = True
        text = f"{type(exc).__name__}: {exc}"
    finally:
        assistant.llm_client = original

    return {
        "уровень": "L4", "что отказало": "ядро целиком",
        "клиент получил": "" if crashed else text,
        "режим": "исключение наружу" if crashed else mode,
        "ответ есть": not crashed and bool(text),
        "разбор": ("Исключение вышло в канал: автоответа нет, клиент молчания не отличит "
                   "от поломки." if crashed else
                   "Канал удержал сбой ядра: клиент получил автоответ, режим ЭСКАЛАЦИЯ — "
                   "значит, карточка уйдёт дежурному. Входящее сообщение записано в "
                   "диалог до вызова ядра, поэтому очередь на дообработку — сама "
                   "переписка, отдельного хранилища не нужно."),
        "исправен": not crashed and bool(text),
    }


SCENARIOS = (l0_nothing_broken, l1_dms_down, l1_stock_stale,
             l2_primary_llm_down, l2_no_backup_configured,
             l3_all_llm_down, l3_empty_reply, l4_core_crash)


# --------------------------------------------------------------------------
# Прогон и отчёт
# --------------------------------------------------------------------------

def run() -> list[dict]:
    return [scenario() for scenario in SCENARIOS]


def report(rows: list[dict]) -> str:
    lines = [
        "# Поведение под отказами",
        "",
        "> Учебный проект «АвтоГрад». Файл создаётся `tools/failures.py`, руками не правится.",
        "",
        f"**Дата прогона:** {datetime.now():%Y-%m-%d %H:%M} · "
        f"**вызовов модели:** 0 (модель подменена заглушкой)",
        "",
        "Проверяется не работоспособность, а поведение при поломке. Критерий приёмки "
        "этапа: ни при одном отказе клиент не остался без ответа.",
        "",
        "| Уровень | Что отказало | Клиент без ответа | Как отработало |",
        "|---|---|---|---|",
    ]
    for r in rows:
        verdict = "нет" if r["ответ есть"] else "**ДА**"
        state = "как задумано" if r.get("исправен", True) else "**не реализовано**"
        lines.append(f"| {r['уровень']} | {r['что отказало']} | {verdict} | {state} |")

    lines += ["", "## Что именно получает клиент", ""]
    for r in rows:
        lines += [f"### {r['уровень']} — {r['что отказало']}", ""]
        if r["клиент получил"]:
            lines += ["> " + r["клиент получил"].replace("\n", "\n> "), ""]
        else:
            lines += ["> *(ничего)*", ""]
        lines += [f"Режим: `{r['режим']}`", "", r["разбор"], ""]

    mute = [r for r in rows if not r["ответ есть"]]
    gaps = [r for r in rows if not r.get("исправен", True) and r["ответ есть"]]
    lines += ["## Итог", ""]
    if mute:
        lines.append(f"**Клиент остался без ответа на уровнях: "
                     f"{', '.join(r['уровень'] for r in mute)}.** Критерий не выполнен.")
    else:
        lines.append("Ни при одном отказе клиент не остался без ответа.")
    if gaps:
        lines += ["", "Уровни, объявленные в лестнице, но не реализованные: "
                  + ", ".join(f"{r['уровень']} ({r['что отказало']})" for r in gaps)
                  + ". Это долг, а не поломка: система деградирует глубже, чем обещано."]
    return "\n".join(lines) + "\n"


def main() -> int:
    rows = run()
    width = max(len(r["что отказало"]) for r in rows)
    print(f"Сценариев отказа: {len(rows)} · вызовов модели: 0\n")
    for r in rows:
        mark = "—" if r["ответ есть"] else "КЛИЕНТ БЕЗ ОТВЕТА"
        state = "ok" if r.get("исправен", True) else "не реализовано"
        print(f"  {r['уровень']}  {r['что отказало']:<{width}}  {state:<15} {mark}")

    if "--report" in sys.argv:
        out = ROOT / "docs" / "failures" / "report.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report(rows), encoding="utf-8")
        print(f"\nОтчёт: {out.relative_to(ROOT)}")

    mute = [r for r in rows if not r["ответ есть"]]
    if mute:
        print(f"\nКлиент остался без ответа: {', '.join(r['уровень'] for r in mute)}")
        return 1
    print("\nНи при одном отказе клиент не остался без ответа.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
