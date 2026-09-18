"""HTTP-ядро: приём сообщений, перехват диалога человеком, состояние.

Завершение этапа 4 по плану. До этого ядро вызывалось только из Telegram-бота
напрямую; теперь у него есть входная точка, и бот становится одним из каналов,
а не единственным.

Зачем API нужен раньше, чем появились другие каналы:

* **дедупликация.** Telegram при обрыве long polling доставляет одно и то же
  сообщение дважды, шлюзы делают то же самое на ретраях. Повтор ловится по
  паре «диалог + идентификатор сообщения канала» и не стоит вызова модели;
* **перехват диалога человеком.** Сотрудник должен уметь забрать переписку
  себе одной командой, и ассистент обязан замолчать немедленно. Это критерий
  приёмки этапа, и он не может жить внутри бота: перехватывает человек из
  другого интерфейса;
* **холодный контур.** n8n по ADR-0002 не имеет права ходить в базу напрямую,
  только через API ядра. Без этой точки этап 5 начинать нечем.

Запуск:
    python tools/api.py                 порт 8080
    uvicorn api:app --app-dir tools     то же самое через uvicorn

Доступ: заголовок `X-API-Key` со значением `AVTOGRAD_API_KEY`. Если ключ в
окружении не задан, API не поднимается: сервис без аутентификации, стоящий
на пути персональных данных, хуже отсутствующего сервиса.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import Depends, FastAPI, Header, HTTPException  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from assistant import KB_VERSION, PROMPT_VERSIONS, ROLES, answer, load_env  # noqa: E402
from ports import FixtureCrm  # noqa: E402
from router import ROUTE_NAMES, route as pick_route  # noqa: E402
from store import Store  # noqa: E402

load_env()

app = FastAPI(title="АвтоГрад — ядро ассистентов", version="0.4.0",
              description="Учебный проект. Данные вымышлены.")
store = Store()
crm = FixtureCrm()


def api_key(x_api_key: str = Header(default="")) -> str:
    expected = os.environ.get("AVTOGRAD_API_KEY", "")
    if not expected:
        raise HTTPException(503, "AVTOGRAD_API_KEY не задан — API отключён")
    if x_api_key != expected:
        raise HTTPException(401, "неверный ключ")
    return x_api_key


class Incoming(BaseModel):
    channel: str = Field(description="канал: telegram-client, telegram-staff, whatsapp…")
    channel_user_id: str = Field(description="идентификатор человека в канале")
    text: str
    message_id: str | None = Field(default=None, description="идентификатор сообщения канала")
    handle: str = ""
    route: str | None = Field(default=None, description="маршрут, если он уже выбран")


class Reply(BaseModel):
    dialog_id: int
    contact_id: int
    route: str | None = None
    text: str = ""
    mode: str = ""
    prompt_version: str = ""
    kb_version: str = ""
    records: list[str] = []
    cached: bool = False
    duplicate: bool = False
    handover_id: str | None = None


class Takeover(BaseModel):
    by: str = Field(description="кто забирает диалог: идентификатор сотрудника")


@app.get("/v1/health")
def health() -> dict:
    """Живость и версии. Единственная ручка без ключа: ею проверяют, поднялся ли сервис."""
    return {"ok": True, "prompt_versions": PROMPT_VERSIONS, "kb_version": KB_VERSION,
            "time": datetime.now().isoformat(timespec="seconds")}


@app.post("/v1/message", response_model=Reply)
def message(incoming: Incoming, _: str = Depends(api_key)) -> Reply:
    contact_id = store.identify(incoming.channel, incoming.channel_user_id, incoming.handle)
    dialog_id = store.current_dialog(contact_id, incoming.channel)

    # Повтор доставки: тот же ответ клиенту уже отправлен, второй вызов модели
    # не нужен — ни клиенту, ни счёту за токены.
    if store.seen_message(dialog_id, incoming.message_id):
        return Reply(dialog_id=dialog_id, contact_id=contact_id,
                     route=store.route_of(dialog_id), duplicate=True,
                     mode="ПОВТОР ДОСТАВКИ")

    owner = store.owner_of(dialog_id)
    if owner != "bot_owned":
        # Диалог ведёт человек. Ассистент молчит — не «отвечает коротко»,
        # а не производит ответа вообще (ADR-0004).
        store.add_message(dialog_id, "in", incoming.text, route=store.route_of(dialog_id),
                          channel_message_id=incoming.message_id)
        return Reply(dialog_id=dialog_id, contact_id=contact_id,
                     route=store.route_of(dialog_id), mode="МОЛЧАНИЕ",
                     text="")

    chosen = incoming.route or pick_route(incoming.text,
                                          current=store.route_of(dialog_id))["route"]
    if chosen not in ROLES:
        chosen = "sales"
    store.set_route(dialog_id, chosen)
    store.add_message(dialog_id, "in", incoming.text, route=chosen,
                      channel_message_id=incoming.message_id)

    result = answer(chosen, store.history(dialog_id, route=chosen), now=datetime.now(),
                    cache=store, pii_map=store.mapping(dialog_id))

    handover_id = None
    if result["mode"].startswith("ЭСКАЛАЦИЯ"):
        # «Передам менеджеру» перестаёт быть фигурой речи: запись о передаче
        # появляется в CRM-исходящих, её можно открыть и пересчитать.
        handover_id = crm.handover({"dialog_id": dialog_id, "contact_id": contact_id,
                                    "route": chosen, "reason": result["mode"],
                                    "channel": incoming.channel})

    store.add_message(dialog_id, "out", result["text"], route=chosen,
                      mode=result.get("mode", ""),
                      prompt_version=result.get("prompt_version", ""),
                      kb_version=result.get("kb_version", ""),
                      records=result.get("records"))

    return Reply(dialog_id=dialog_id, contact_id=contact_id, route=chosen,
                 text=result["text"], mode=result.get("mode", ""),
                 prompt_version=result.get("prompt_version", ""),
                 kb_version=result.get("kb_version", ""),
                 records=result.get("records") or [],
                 cached=bool(result.get("cached")), handover_id=handover_id)


@app.post("/v1/dialog/{dialog_id}/takeover")
def takeover(dialog_id: int, body: Takeover, _: str = Depends(api_key)) -> dict:
    """Сотрудник забирает диалог. Ассистент замолкает со следующего сообщения."""
    store.take_over(dialog_id, body.by)
    return {"dialog_id": dialog_id, "owner": store.owner_of(dialog_id), "by": body.by}


@app.post("/v1/dialog/{dialog_id}/release")
def release(dialog_id: int, _: str = Depends(api_key)) -> dict:
    """Возврат диалога ассистенту — только явным действием человека."""
    store.release(dialog_id)
    return {"dialog_id": dialog_id, "owner": store.owner_of(dialog_id)}


@app.get("/v1/dialog/{dialog_id}")
def dialog(dialog_id: int, _: str = Depends(api_key)) -> dict:
    """Состояние диалога без содержания реплик: для внешних систем этого хватает."""
    return {"dialog_id": dialog_id, "owner": store.owner_of(dialog_id),
            "route": store.route_of(dialog_id),
            "route_name": ROUTE_NAMES.get(store.route_of(dialog_id) or "", "не определён"),
            "messages": len(store.history(dialog_id, limit=1000))}


@app.get("/v1/stats")
def stats(_: str = Depends(api_key)) -> dict:
    return {"store": store.stats(), "handovers": len(crm.pending()),
            "prompt_versions": PROMPT_VERSIONS, "kb_version": KB_VERSION}


def main() -> int:
    import uvicorn
    if not os.environ.get("AVTOGRAD_API_KEY"):
        print("Нет AVTOGRAD_API_KEY — задайте его в .env, иначе API откажет на каждом запросе.")
    port = int(os.environ.get("AVTOGRAD_API_PORT", "8080"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
