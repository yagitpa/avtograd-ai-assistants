"""Порты внешних систем: DMS и CRM (ADR-0003).

Каждая внешняя система живёт за портом — узким интерфейсом, у которого есть
две реализации: фикстурная для учебного контура и боевая для подключения к
дилеру. Ядро знает только порт. Когда появится 1С:Альфа-Авто, меняется
реализация, а не ядро — именно ради этого этап 0 начинался с фикстур.

Порт отвечает за три вещи, которых нет у прямого чтения JSON:

* **таймаут** — DMS дилера медленный, и ждать его вечно нельзя;
* **отказ** — недоступность внешней системы это норма, а не исключение,
  и она должна быть воспроизводима в тесте (`FixtureDms(fail=True)`);
* **честный ответ о недоступности** — ядро отличает «нет такой машины» от
  «не смог спросить», потому что клиенту это разные ответы.

CRM отдельно: это система записи, а не чтения. Фикстурная реализация
складывает намерение в файл `var/crm_outbox.jsonl`. Смысл не в имитации —
благодаря ей фраза «передам менеджеру» перестаёт быть фигурой речи: запись
о передаче действительно появляется, её можно открыть и пересчитать.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "docs" / "knowledge-base" / "fixtures"
OUTBOX = ROOT / "var" / "crm_outbox.jsonl"

# Таймауты из ADR-0002. DMS медленный по своей природе: полторы секунды —
# это граница, за которой клиент замечает паузу.
DMS_TIMEOUT_S = 1.5


class PortUnavailable(RuntimeError):
    """Внешняя система не ответила. Это ожидаемое состояние, а не авария."""


class DmsPort(Protocol):
    def vehicle(self, vin: str = "", plate: str = "") -> dict | None: ...
    def orders(self, vehicle_id: str) -> list[dict]: ...
    def customer(self, customer_id: str) -> dict | None: ...


class CrmPort(Protocol):
    def handover(self, payload: dict) -> str: ...


class FixtureDms:
    """DMS на фикстурах: те же данные, тот же контракт, никакого 1С.

    `fail=True` и `latency` существуют не для красоты: уровни деградации
    L1–L4 нужно уметь воспроизводить по требованию, а на живой системе
    отказ DMS по заказу не устроишь.
    """

    def __init__(self, fail: bool = False, latency: float = 0.0,
                 timeout: float = DMS_TIMEOUT_S):
        self.fail = fail
        self.latency = latency
        self.timeout = timeout
        self._cache: dict[str, dict] = {}

    def _load(self, name: str) -> dict:
        if self.fail:
            raise PortUnavailable(f"DMS недоступен при чтении {name}")
        if self.latency:
            # Ожидание считается здесь, а не в вызывающем коде: порт обязан
            # сам решать, что он не уложился в срок.
            time.sleep(min(self.latency, self.timeout))
            if self.latency > self.timeout:
                raise PortUnavailable(f"DMS не ответил за {self.timeout} с")
        if name not in self._cache:
            self._cache[name] = json.loads((FIX / name).read_text(encoding="utf-8"))
        return self._cache[name]

    def vehicle(self, vin: str = "", plate: str = "") -> dict | None:
        vin_key = (vin or "").strip().upper()
        plate_key = (plate or "").strip().upper().replace(" ", "")
        for v in self._load("vehicles.json")["vehicles"]:
            if vin_key and v["vin"].upper() == vin_key:
                return v
            if plate_key and v.get("plate", "").upper().replace(" ", "") == plate_key:
                return v
        return None

    def orders(self, vehicle_id: str) -> list[dict]:
        return [o for o in self._load("work_orders.json")["work_orders"]
                if o["vehicle_id"] == vehicle_id]

    def customer(self, customer_id: str) -> dict | None:
        for c in self._load("customers.json")["customers"]:
            if c["id"] == customer_id:
                return c
        return None


class FixtureCrm:
    """CRM на файле: намерение передать обращение человеку записывается.

    Боевая реализация уйдёт в amoCRM или Битрикс24 через n8n (ADR-0002),
    но контракт останется тот же: одна запись на одну передачу, с
    идентификатором диалога и причиной.
    """

    def __init__(self, path: Path | None = None):
        self.path = path or OUTBOX

    def handover(self, payload: dict) -> str:
        record = dict(payload)
        record["created_at"] = datetime.now().isoformat(timespec="seconds")
        record["id"] = f"ho-{int(time.time() * 1000)}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record["id"]

    def pending(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in
                self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


def owner_card(dms: DmsPort, vin: str = "", plate: str = "") -> dict | None:
    """Карточка владельца через порт: машина, её наряды, её владелец.

    Возвращает None, если такой машины нет. Если DMS недоступен, исключение
    не гасится: «не нашёл» и «не смог спросить» — разные ответы клиенту,
    и решать, какой из них дать, должно ядро, а не порт.
    """
    vehicle = dms.vehicle(vin=vin, plate=plate)
    if not vehicle:
        return None
    return {"vehicle": vehicle,
            "orders": dms.orders(vehicle["id"]),
            "customer": dms.customer(vehicle["customer_id"]) or {}}
