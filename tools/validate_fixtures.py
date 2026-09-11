"""Проверка фикстур «АвтоГрада» на связность и на критерий приёмки этапа 0.

Критерий: любой факт, который может назвать ассистент, имеет источник в фикстурах,
ссылки между файлами не битые, данные заведомо тестовые.

Запуск:  python tools/validate_fixtures.py
Код 0 — проверки пройдены, код 1 — есть нарушения.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

FIX = Path(__file__).resolve().parents[1] / "docs" / "knowledge-base" / "fixtures"
TODAY = "2026-09-11"
TEST_PHONE = re.compile(r"^(\+7 495 000-|\+7 900 000-|8 800 000-)")

errors: list[str] = []
checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        errors.append(message)


def load(name: str):
    with (FIX / name).open(encoding="utf-8") as fh:
        return json.load(fh)


def load_stock() -> list[dict]:
    with (FIX / "stock.csv").open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, delimiter=";"))


dealer = load("dealer.json")
staff = load("staff.json")
hours = load("working_hours.json")
models = load("models.json")
price = load("service_price.json")
warranty = load("warranty.json")
promos = load("promotions.json")
meta = load("stock_meta.json")
customers = load("customers.json")
vehicles = load("vehicles.json")
orders = load("work_orders.json")
vacancies = load("vacancies.json")
profiles = load("role_profiles.json")
candidates = load("candidates.json")
stock = load_stock()

location_ids = {loc["id"] for loc in dealer["locations"]}
department_ids = set(dealer["departments"])
staff_ids = {p["id"] for p in staff["people"]}

# --- 1. Дисклеймер и тестовость данных -----------------------------------
for name in ["dealer", "staff", "working_hours", "models", "service_price", "warranty",
             "promotions", "stock_meta", "customers", "vehicles", "work_orders",
             "vacancies", "role_profiles", "candidates"]:
    doc = load(f"{name}.json")
    check("disclaimer" in doc.get("_meta", {}), f"{name}.json: нет _meta.disclaimer")

phones = ([p["phone"] for p in staff["people"]]
          + [c["phone"] for c in customers["customers"]]
          + [loc["phone"] for loc in dealer["locations"]]
          + [dealer["company"]["common_phone"], dealer["company"]["roadside_assistance"]])
for phone in phones:
    check(bool(TEST_PHONE.match(phone)), f"Телефон вне тестового диапазона: {phone}")

# --- 2. Сток -------------------------------------------------------------
vins = [row["vin"] for row in stock]
check(len(vins) == len(set(vins)), "Сток: VIN повторяются")
check(len(stock) == 40, f"Сток: ожидалось 40 автомобилей, найдено {len(stock)}")

model_index = {(m["make"], m["model"]): m for m in models["models"]}
allowed_statuses = set(meta["statuses"])

for row in stock:
    vin_id = row["vin"]
    check(len(vin_id) == 17 and vin_id.startswith("TESTAG"),
          f"{vin_id}: VIN не в тестовом формате")
    check(row["dealer_id"] in location_ids, f"{vin_id}: неизвестный dealer_id {row['dealer_id']}")
    key = (row["make"], row["model"])
    check(key in model_index, f"{vin_id}: модель {key} отсутствует в models.json")
    check(row["status"] in allowed_statuses, f"{vin_id}: статус {row['status']} вне словаря")
    check(int(row["price_rub"]) > 0, f"{vin_id}: цена не положительна")

    if row["condition"] == "new":
        trims = model_index.get(key, {}).get("trims", {})
        check(row["trim"] in trims, f"{vin_id}: комплектация {row['trim']} не в прайсе модели")
        if row["trim"] in trims:
            check(int(row["price_rub"]) == trims[row["trim"]],
                  f"{vin_id}: цена {row['price_rub']} не совпадает с прайсом {trims[row['trim']]}")
        check(int(row["mileage_km"]) < 100, f"{vin_id}: новый автомобиль с пробегом")
        check(int(row["owners"]) == 0, f"{vin_id}: у нового автомобиля указаны владельцы")
    else:
        check(int(row["mileage_km"]) > 10000, f"{vin_id}: подозрительный пробег для б/у")
        check(int(row["owners"]) >= 1, f"{vin_id}: у б/у не указан владелец")

    required = {"reserved": "reserved_until", "prep": "ready_at",
                "in_transit": "eta", "sold": "sold_at"}.get(row["status"])
    if required:
        check(bool(row[required]), f"{vin_id}: статус {row['status']} без поля {required}")

for status in ["in_stock", "reserved", "prep", "in_transit", "sold"]:
    check(any(r["status"] == status for r in stock),
          f"Сток: нет ни одного автомобиля со статусом {status} — сценарий непроверяем")

# --- 3. Сервис: машины клиентов и заказ-наряды ---------------------------
customer_ids = {c["id"] for c in customers["customers"]}
vehicle_ids = {v["id"] for v in vehicles["vehicles"]}
stock_vins = set(vins)

for veh in vehicles["vehicles"]:
    check(veh["customer_id"] in customer_ids,
          f"{veh['id']}: владелец {veh['customer_id']} не найден")
    check(veh["vin"] not in stock_vins, f"{veh['id']}: VIN клиента пересекается со стоком")
    check((veh["make"], veh["model"]) in model_index,
          f"{veh['id']}: модель {veh['make']} {veh['model']} отсутствует в models.json")

order_statuses = set(orders["_meta"]["statuses"])
for wo in orders["work_orders"]:
    check(wo["vehicle_id"] in vehicle_ids, f"{wo['id']}: автомобиль {wo['vehicle_id']} не найден")
    check(wo["status"] in order_statuses, f"{wo['id']}: статус {wo['status']} вне словаря")
    check(wo["sum_rub"] >= 0, f"{wo['id']}: отрицательная сумма")

check(any(w["status"] == "in_progress" for w in orders["work_orders"]),
      "Заказ-наряды: нет открытого ремонта — сценарий «что с моей машиной» непроверяем")
check(sum(1 for w in orders["work_orders"] if w["vehicle_id"] == "veh-05") >= 3,
      "Заказ-наряды: конфликтный сценарий требует трёх обращений по одному автомобилю")

to_codes = {m["code"] for m in price["maintenance"]}
allowed_wo_types = to_codes | {"repair", "warranty"}
for wo in orders["work_orders"]:
    check(wo["type"] in allowed_wo_types,
          f"{wo['id']}: вид работ {wo['type']} отсутствует в прайсе и вне словаря типов")
    # Коды ТО только латиницей: кириллическое «ТО» визуально неотличимо и ломает поиск
    check("ТО" not in wo["type"], f"{wo['id']}: код работ содержит кириллицу — {wo['type']}")

# --- 4. Часы работы и дежурства (ADR-0004) -------------------------------
for dept in department_ids:
    check(dept in hours["schedule"], f"working_hours: нет расписания для отдела {dept}")

roster_days = [d["weekday"] for d in hours["on_call"]["roster"]]
check(roster_days == ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
      "working_hours: график дежурств не покрывает неделю")
for day in hours["on_call"]["roster"]:
    for key in ("service", "quality"):
        person = day.get(key)
        check(person is None or person in staff_ids,
              f"working_hours: дежурный {person} ({day['weekday']}, {key}) не найден в staff.json")

check(hours["out_of_hours_policy"]["promise_allowed"] is False,
      "working_hours: обещания вне рабочих часов должны быть запрещены (ADR-0004)")

for person in staff["people"]:
    check(person["location"] in location_ids, f"{person['id']}: неизвестная площадка")
    check(person["department"] in department_ids, f"{person['id']}: неизвестный отдел")

# --- 5. HR ---------------------------------------------------------------
vacancy_ids = {v["id"] for v in vacancies["vacancies"]}
check(set(profiles["profiles"]) == vacancy_ids,
      "role_profiles: набор профилей не совпадает со списком вакансий")

for vac in vacancies["vacancies"]:
    check(vac["department"] in department_ids, f"{vac['id']}: неизвестный отдел")
    check(vac["location"] in location_ids, f"{vac['id']}: неизвестная площадка")
    check(vac["salary_from_rub"] < vac["salary_to_rub"], f"{vac['id']}: вилка перевёрнута")

for prof_id, prof in profiles["profiles"].items():
    check(len(prof["screening"]) >= 4, f"{prof_id}: чек-лист скрининга короче четырёх вопросов")
    check(len(prof["stop_factors"]) >= 1, f"{prof_id}: не заданы стоп-факторы")

for cand in candidates["candidates"]:
    check(cand["vacancy_id"] in vacancy_ids, f"{cand['id']}: вакансия не найдена")
    check(cand["status"] in {"new", "stop_factor"}, f"{cand['id']}: неизвестный статус")
    forbidden = {"age", "gender", "citizenship", "marital_status"}
    check(not (forbidden & set(cand)), f"{cand['id']}: в карточке есть поле из запрещённых (bias)")

check(any(c["status"] == "stop_factor" for c in candidates["candidates"]),
      "Кандидаты: нет ни одного со стоп-фактором — отсев непроверяем")

# --- 6. Акции ------------------------------------------------------------
for promo in promos["promotions"]:
    check(promo["from"] <= promo["to"], f"{promo['id']}: дата начала позже даты окончания")
    expected = "expired" if promo["to"] < TODAY else "active"
    check(promo["status"] == expected,
          f"{promo['id']}: статус {promo['status']} не соответствует датам (ожидался {expected})")
    for other in promo["not_combinable_with"]:
        check(other in {p["id"] for p in promos["promotions"]},
              f"{promo['id']}: ссылка на несуществующую акцию {other}")

check(any(p["status"] == "expired" for p in promos["promotions"]),
      "Акции: нет истёкшей акции — правило про недействующие акции непроверяемо")

# --- 7. Гарантия и прайс -------------------------------------------------
check(warranty["decision_rule"]["who_decides"] in staff_ids,
      "warranty: решение по гарантии закреплено за несуществующим сотрудником")
intervals = [m["interval_km"] for m in price["maintenance"]]
check(intervals == sorted(intervals), "service_price: интервалы ТО не по возрастанию")

# --- Итог ----------------------------------------------------------------
print(f"Проверок выполнено: {checks}")
if errors:
    print(f"\nНАРУШЕНИЙ: {len(errors)}\n")
    for err in errors:
        print(f"  ✗ {err}")
    sys.exit(1)

print("Нарушений нет. Фикстуры связны, критерий этапа 0 выполнен.")
