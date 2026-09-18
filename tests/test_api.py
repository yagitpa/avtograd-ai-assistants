"""Тесты HTTP-ядра: аутентификация, дедупликация, перехват диалога.

Модель не вызывается: `answer` подменяется заглушкой. Проверяется то, за чем
API и заводился, — поведение вокруг ответа, а не сам ответ.

Запуск:  python tests/test_api.py
Код возврата 1 — есть падения.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

os.environ["AVTOGRAD_API_KEY"] = "test-key"

import api  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from ports import FixtureCrm  # noqa: E402
from store import Store  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

passed, failed = 0, []
calls = {"answer": 0}


def check(condition: bool, title: str) -> None:
    global passed
    if condition:
        passed += 1
    else:
        failed.append(title)


def fake_answer(role, history, **kwargs):
    """Заглушка вместо модели: считает вызовы и отвечает предсказуемо."""
    calls["answer"] += 1
    last = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    if "жалоб" in last.lower():  # «жалобу», «жалоба», «жалобы»
        return {"text": "Передаю обращение профильному специалисту — он свяжется с вами.",
                "mode": "ЭСКАЛАЦИЯ (валидатор)", "records": [], "violations": ["компенсация"],
                "prompt_version": "test", "kb_version": "test"}
    return {"text": f"Ответ по маршруту {role}.", "mode": "ОТВЕТ", "records": ["FAQ-SLS-001"],
            "violations": [], "prompt_version": "test", "kb_version": "test"}


def setup() -> TestClient:
    tmp = Path(tempfile.mkdtemp())
    api.store = Store(tmp / "api.db")
    api.crm = FixtureCrm(tmp / "outbox.jsonl")
    api.answer = fake_answer
    calls["answer"] = 0
    return TestClient(api.app)


HEAD = {"X-API-Key": "test-key"}


def test_auth() -> None:
    client = setup()
    check(client.get("/v1/health").status_code == 200, "живость доступна без ключа")
    check(client.post("/v1/message", json={"channel": "telegram-client",
                                           "channel_user_id": "1", "text": "привет"}
                      ).status_code == 401, "без ключа сообщение не принимается")
    # Значение заголовка — только ASCII: кириллица не проходит уже на уровне HTTP.
    check(client.post("/v1/message", headers={"X-API-Key": "wrong-key"},
                      json={"channel": "telegram-client", "channel_user_id": "1",
                            "text": "привет"}).status_code == 401,
          "с чужим ключом сообщение не принимается")


def test_message_and_dedup() -> None:
    client = setup()
    body = {"channel": "telegram-client", "channel_user_id": "42", "text": "Есть Vega Cross?",
            "message_id": "tg-100"}
    first = client.post("/v1/message", headers=HEAD, json=body).json()
    check(first["text"].startswith("Ответ по маршруту"), "ответ возвращается")
    check(first["route"] == "sales", "маршрут определён")
    check(first["duplicate"] is False, "первое сообщение не считается повтором")

    second = client.post("/v1/message", headers=HEAD, json=body).json()
    check(second["duplicate"] is True, "повторная доставка распознана")
    check(second["dialog_id"] == first["dialog_id"], "повтор попадает в тот же диалог")
    check(calls["answer"] == 1, "на повтор модель не вызывается")


def test_takeover_silences_bot() -> None:
    """Критерий приёмки этапа 4: сообщение менеджера немедленно глушит бота."""
    client = setup()
    body = {"channel": "telegram-client", "channel_user_id": "77", "text": "Здравствуйте",
            "message_id": "m-1"}
    first = client.post("/v1/message", headers=HEAD, json=body).json()
    dialog_id = first["dialog_id"]
    before = calls["answer"]

    taken = client.post(f"/v1/dialog/{dialog_id}/takeover", headers=HEAD,
                        json={"by": "emp-03"}).json()
    check(taken["owner"] == "human_owned", "диалог перешёл человеку")

    body2 = dict(body, text="А что по цене?", message_id="m-2")
    silent = client.post("/v1/message", headers=HEAD, json=body2).json()
    check(silent["text"] == "", "ассистент не отвечает, пока диалог у человека")
    check(silent["mode"] == "МОЛЧАНИЕ", "режим молчания назван прямо")
    check(calls["answer"] == before, "модель не вызывается при чужом владении")

    state = client.get(f"/v1/dialog/{dialog_id}", headers=HEAD).json()
    check(state["messages"] >= 3, "реплика клиента сохранена, хотя ответа не было")

    client.post(f"/v1/dialog/{dialog_id}/release", headers=HEAD)
    body3 = dict(body, text="Так что с ценой?", message_id="m-3")
    back = client.post("/v1/message", headers=HEAD, json=body3).json()
    check(back["text"] != "", "после возврата ассистент снова отвечает")


def test_handover_recorded() -> None:
    """Эскалация оставляет запись: «передам менеджеру» подтверждается фактом."""
    client = setup()
    body = {"channel": "telegram-client", "channel_user_id": "99",
            "text": "Пишу жалобу, требую компенсацию", "message_id": "m-9"}
    reply = client.post("/v1/message", headers=HEAD, json=body).json()
    check(reply["mode"].startswith("ЭСКАЛАЦИЯ"), "обращение ушло в эскалацию")
    check(reply["handover_id"], "передача получила идентификатор")
    pending = api.crm.pending()
    check(len(pending) == 1, "запись о передаче появилась")
    check(pending[0]["dialog_id"] == reply["dialog_id"], "запись ссылается на диалог")
    check("reason" in pending[0], "в записи есть причина передачи")

    stats = client.get("/v1/stats", headers=HEAD).json()
    check(stats["handovers"] == 1, "передача видна в сводке")


def main() -> int:
    for test in (test_auth, test_message_and_dedup, test_takeover_silences_bot,
                 test_handover_recorded):
        test()
    print(f"Проверок выполнено: {passed + len(failed)}")
    if failed:
        print(f"\nПадений: {len(failed)}")
        for title in failed:
            print(f"  · {title}")
        return 1
    print("Падений нет. API принимает, не дублирует и замолкает по требованию.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
