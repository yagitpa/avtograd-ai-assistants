"""Генератор фикстур «АвтоГрада» (учебный проект, данные вымышлены).

Запуск:  python tools/build_fixtures.py
Выход:   docs/knowledge-base/fixtures/*.json, stock.csv

Все данные тестовые: марки, VIN, телефоны, имена и цены не существуют.
Телефоны выбраны в заведомо нероутируемых диапазонах.
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

# Консоль Windows по умолчанию в cp1251 — без этого падает вывод кириллицы и стрелок
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "knowledge-base" / "fixtures"
TODAY = "2026-09-11"
DISCLAIMER = "Учебный проект. Данные вымышлены, совпадения случайны."


def vin(seq: int) -> str:
    """Заведомо невалидный VIN: 17 символов, префикс TESTAG."""
    return f"TESTAG{seq:011d}"


def write(name: str, payload) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"  {name:<24} {path.stat().st_size:>7} байт")


# --------------------------------------------------------------------------
# Компания и площадки
# --------------------------------------------------------------------------

DEALER = {
    "_meta": {"disclaimer": DISCLAIMER, "generated_for": "ADR-0003", "updated_at": TODAY},
    "company": {
        "legal_name": 'ООО «АвтоГрад» (тестовая организация)',
        "brand": "АвтоГрад",
        "official_brands": ["Aurora", "Vega"],
        "used_cars_brands": "любые марки, включая непредставленные в ДЦ",
        "timezone": "Europe/Moscow",
        "site": "avtograd-demo.ru",
        "common_phone": "+7 495 000-10-00",
        "roadside_assistance": "8 800 000-00-11 (круглосуточно)",
    },
    "locations": [
        {
            "id": "dc-north",
            "name": "ДЦ «АвтоГрад-Север»",
            "address": "ул. Кузнецова, 14",
            "phone": "+7 495 000-12-00",
            "brands": ["Aurora"],
            "departments": ["sales_new", "service", "parts", "credit"],
            "service_bays": 12,
            "note": "Основной сервисный центр сети",
        },
        {
            "id": "dc-west",
            "name": "ДЦ «АвтоГрад-Запад»",
            "address": "Варшавское шоссе, 71",
            "phone": "+7 495 000-14-00",
            "brands": ["Vega"],
            "departments": ["sales_new", "sales_used", "credit", "hr"],
            "service_bays": 0,
            "note": "Отдел автомобилей с пробегом и трейд-ин всей сети",
        },
    ],
    "departments": {
        "sales_new": "Отдел продаж новых автомобилей",
        "sales_used": "Отдел автомобилей с пробегом и трейд-ин",
        "service": "Сервис и постпродажное обслуживание",
        "parts": "Склад запчастей",
        "credit": "Отдел кредитования и страхования",
        "hr": "Отдел персонала",
        "quality": "Служба качества",
    },
}

STAFF = {
    "_meta": {"disclaimer": DISCLAIMER, "updated_at": TODAY},
    "people": [
        {"id": "emp-01", "name": "Ирина Тимофеева", "role": "Руководитель сервиса",
         "department": "service", "location": "dc-north", "phone": "+7 495 000-12-34",
         "escalation_for": ["гарантийный спор", "повторный ремонт", "срок ремонта"]},
        {"id": "emp-02", "name": "Артём Лебедев", "role": "Менеджер по качеству",
         "department": "quality", "location": "dc-north", "phone": "+7 495 000-12-40",
         "escalation_for": ["жалоба", "упоминание суда", "публичный отзыв"]},
        {"id": "emp-03", "name": "Сергей Панов", "role": "Руководитель отдела продаж",
         "department": "sales_new", "location": "dc-north", "phone": "+7 495 000-12-10",
         "escalation_for": ["торг", "скидка", "корпоративный клиент"]},
        {"id": "emp-04", "name": "Дарья Волкова", "role": "Руководитель отдела с пробегом",
         "department": "sales_used", "location": "dc-west", "phone": "+7 495 000-14-10",
         "escalation_for": ["оценка трейд-ин", "автомобиль в залоге"]},
        {"id": "emp-05", "name": "Марина Ласточкина", "role": "Специалист по кредитованию",
         "department": "credit", "location": "dc-west", "phone": "+7 495 000-14-18",
         "escalation_for": ["кредитная заявка", "лизинг", "отказ банка"]},
        {"id": "emp-06", "name": "Ольга Крылова", "role": "HR бизнес-партнёр",
         "department": "hr", "location": "dc-west", "phone": "+7 495 000-14-12",
         "escalation_for": ["конфликт сотрудника", "нетиповой кандидат", "оффер"]},
        {"id": "emp-07", "name": "Тамара Ковалёва", "role": "Начальник склада",
         "department": "parts", "location": "dc-north", "phone": "+7 495 000-12-52",
         "escalation_for": ["наличие запчасти", "срок поставки"]},
        {"id": "emp-08", "name": "Павел Ершов", "role": "Мастер-приёмщик",
         "department": "service", "location": "dc-north", "phone": "+7 495 000-12-36",
         "escalation_for": ["запись в сервис", "статус ремонта"]},
    ],
}

# --------------------------------------------------------------------------
# Часы работы и дежурства — опора ADR-0004, пункт 4
# --------------------------------------------------------------------------

WORKING_HOURS = {
    "_meta": {
        "disclaimer": DISCLAIMER,
        "updated_at": TODAY,
        "owner": "emp-03",
        "purpose": "ADR-0004: обещания ассистентов параметризуются рабочим временем отдела",
        "sla_basis": "Все SLA считаются в рабочих часах отдела, а не в астрономических",
    },
    "timezone": "Europe/Moscow",
    "schedule": {
        "sales_new":  {"mon-sun": ["09:00", "21:00"]},
        "sales_used": {"mon-sun": ["09:00", "21:00"]},
        "service":    {"mon-fri": ["08:00", "20:00"], "sat": ["09:00", "18:00"], "sun": None},
        "parts":      {"mon-fri": ["08:00", "19:00"], "sat": ["09:00", "16:00"], "sun": None},
        "credit":     {"mon-sun": ["10:00", "20:00"]},
        "hr":         {"mon-fri": ["09:00", "18:00"], "sat": None, "sun": None},
        "quality":    {"mon-fri": ["09:00", "18:00"], "sat": None, "sun": None},
    },
    "holidays_2026": [
        {"date": "2026-11-04", "name": "День народного единства", "mode": "сокращённый день до 18:00"},
        {"date": "2026-12-31", "name": "31 декабря", "mode": "до 17:00"},
        {"date": "2027-01-01", "name": "Новый год", "mode": "выходной"},
    ],
    "on_call": {
        "description": "Дежурный по «красным» обращениям вне часов работы отдела",
        "red_sla_hours": 2,
        "roster": [
            {"weekday": "mon", "service": "emp-01", "quality": "emp-02"},
            {"weekday": "tue", "service": "emp-08", "quality": "emp-02"},
            {"weekday": "wed", "service": "emp-01", "quality": "emp-02"},
            {"weekday": "thu", "service": "emp-08", "quality": "emp-02"},
            {"weekday": "fri", "service": "emp-01", "quality": "emp-02"},
            {"weekday": "sat", "service": "emp-08", "quality": None},
            {"weekday": "sun", "service": None, "quality": None},
        ],
    },
    "out_of_hours_policy": {
        "promise_allowed": False,
        "must_state": "реальное время ответа в следующий рабочий час отдела",
        "queue": "обращение ставится в очередь с меткой времени и адресатом",
        "exception": "вопросы безопасности — контакт помощи на дороге круглосуточно",
    },
}

# --------------------------------------------------------------------------
# Модельный ряд
# --------------------------------------------------------------------------

MODELS = {
    "_meta": {"disclaimer": DISCLAIMER, "updated_at": TODAY},
    "models": [
        {"make": "Aurora", "model": "X3", "body": "кроссовер", "engine": "1.6 турбо, 150 л.с.",
         "transmission": "АКПП", "drive": "передний", "segment": "компактный кроссовер",
         "trims": {"Base": 2490000, "Comfort": 2790000, "Style": 2990000}},
        {"make": "Aurora", "model": "X5", "body": "кроссовер", "engine": "2.0 турбо, 200 л.с.",
         "transmission": "АКПП", "drive": "полный", "segment": "среднеразмерный кроссовер",
         "trims": {"Comfort": 2890000, "Style": 3290000, "Prestige": 3690000}},
        {"make": "Aurora", "model": "S7", "body": "седан", "engine": "2.5, 249 л.с.",
         "transmission": "АКПП", "drive": "полный", "segment": "бизнес-седан",
         "trims": {"Style": 3990000, "Prestige": 4890000}},
        {"make": "Vega", "model": "Sedan", "body": "седан", "engine": "1.6, 122 л.с.",
         "transmission": "МКПП / АКПП", "drive": "передний", "segment": "массовый седан",
         "trims": {"Base": 1790000, "Comfort": 1990000}},
        {"make": "Vega", "model": "Cross", "body": "кроссовер", "engine": "2.0, 150 л.с.",
         "transmission": "АКПП", "drive": "передний", "segment": "массовый кроссовер",
         "trims": {"Comfort": 2190000, "Style": 2490000}},
        {"make": "Vega", "model": "Sport", "body": "кроссовер купе", "engine": "2.0 турбо, 190 л.с.",
         "transmission": "АКПП", "drive": "полный", "segment": "спортивный кроссовер",
         "trims": {"Style": 2890000}},
        {"make": "Vector", "model": "Sedan", "body": "седан", "engine": "1.6, 113 л.с.",
         "transmission": "МКПП", "drive": "передний", "segment": "только с пробегом", "trims": {}},
        {"make": "Vector", "model": "Hatch", "body": "хэтчбек", "engine": "1.4, 99 л.с.",
         "transmission": "МКПП", "drive": "передний", "segment": "только с пробегом", "trims": {}},
        {"make": "Vector", "model": "Van", "body": "фургон", "engine": "2.2 дизель, 136 л.с.",
         "transmission": "МКПП", "drive": "задний", "segment": "только с пробегом", "trims": {}},
    ],
}

# --------------------------------------------------------------------------
# Сток: 40 автомобилей
# (dealer, condition, make, model, trim, year, mileage, color, price, status,
#  owners, accident, location, extra, count)
# --------------------------------------------------------------------------

NEW = [
    ("dc-north", "Aurora", "X3", "Base",     2026, "белый",       2490000, "in_stock", "площадка", {}, 3),
    ("dc-north", "Aurora", "X3", "Comfort",  2026, "серый",       2790000, "in_stock", "площадка", {}, 2),
    ("dc-north", "Aurora", "X3", "Style",    2026, "синий",       2990000, "in_transit", "в пути",
     {"eta": "2026-09-25"}, 1),
    ("dc-north", "Aurora", "X5", "Comfort",  2026, "белый",       2890000, "in_stock", "площадка", {}, 2),
    ("dc-north", "Aurora", "X5", "Style",    2026, "чёрный",      3290000, "in_stock", "площадка", {}, 2),
    ("dc-north", "Aurora", "X5", "Prestige", 2026, "серебристый", 3690000, "reserved", "площадка",
     {"reserved_until": "2026-09-14"}, 1),
    ("dc-north", "Aurora", "S7", "Style",    2026, "чёрный",      3990000, "in_stock", "площадка", {}, 1),
    ("dc-north", "Aurora", "S7", "Prestige", 2026, "белый",       4890000, "prep", "предпродажка",
     {"ready_at": "2026-09-13"}, 1),
    ("dc-west",  "Vega", "Sedan", "Base",    2026, "белый",       1790000, "in_stock", "площадка", {}, 3),
    ("dc-west",  "Vega", "Sedan", "Comfort", 2026, "серый",       1990000, "in_stock", "площадка", {}, 2),
    ("dc-west",  "Vega", "Cross", "Comfort", 2026, "синий",       2190000, "in_stock", "площадка", {}, 2),
    ("dc-west",  "Vega", "Cross", "Style",   2026, "серый",       2490000, "in_stock", "площадка", {}, 2),
    ("dc-west",  "Vega", "Sport", "Style",   2026, "красный",     2890000, "in_stock", "площадка", {}, 1),
]

USED = [
    ("Vector", "Sedan", "", 2022, 61400, "серебристый", 1340000, "reserved", 1, "нет",
     {"reserved_until": "2026-09-14"}),
    ("Vector", "Hatch", "", 2019, 112000, "синий",       890000, "in_stock", 2, "перекрас 1 элемента", {}),
    ("Vector", "Sedan", "", 2018, 148500, "чёрный",      720000, "in_stock", 3, "перекрас 3 элементов", {}),
    ("Vega", "Sedan", "Comfort", 2021, 78300, "белый",   1390000, "in_stock", 1, "нет", {}),
    ("Vega", "Cross", "Comfort", 2020, 95700, "серый",   1690000, "in_stock", 2, "перекрас 2 элементов", {}),
    ("Aurora", "X3", "Comfort", 2021, 64200, "белый",    2090000, "in_stock", 1, "нет", {}),
    ("Aurora", "X5", "Style",   2019, 121400, "чёрный",  2190000, "in_stock", 2, "нет", {}),
    ("Vector", "Van", "",       2020, 187000, "белый",   1090000, "in_stock", 2, "ремонт после ДТП", {}),
    ("Vega", "Sport", "Style",  2022, 44800, "красный",  2390000, "in_stock", 1, "нет", {}),
    ("Aurora", "S7", "Style",   2020, 88600, "серый",    2590000, "sold", 2, "нет", {"sold_at": "2026-09-10"}),
    ("Vega", "Sedan", "Base",   2019, 134000, "серебристый", 890000, "in_stock", 3, "перекрас 2 элементов", {}),
    ("Aurora", "X3", "Style",   2023, 38900, "синий",    2390000, "in_stock", 1, "нет", {}),
    ("Vector", "Sedan", "",     2021, 83200, "серый",    1190000, "in_stock", 2, "нет", {}),
    ("Vega", "Cross", "Style",  2023, 29400, "белый",    2090000, "prep", 1, "нет", {"ready_at": "2026-09-12"}),
    ("Aurora", "X5", "Comfort", 2022, 57600, "белый",    2690000, "in_stock", 1, "нет", {}),
    ("Vega", "Sedan", "Comfort", 2022, 51300, "чёрный",  1490000, "in_stock", 1, "нет", {}),
    ("Vector", "Hatch", "",     2023, 26700, "красный",  1190000, "in_stock", 1, "нет", {}),
]

# Палитра для разведения одинаковых комплектаций по цветам
PALETTE = ["белый", "серый", "чёрный", "синий", "серебристый", "красный"]

STOCK_COLUMNS = [
    "vin", "dealer_id", "condition", "make", "model", "trim", "year", "mileage_km",
    "color", "price_rub", "status", "owners", "accident_history", "location",
    "arrived_at", "reserved_until", "ready_at", "eta", "sold_at", "photos",
]


def build_stock() -> list[dict]:
    rows: list[dict] = []
    seq = 1
    for dealer, make, model, trim, year, color, price, status, loc, extra, count in NEW:
        for i in range(count):
            # Одинаковые комплектации разводим по цветам: иначе вопрос «какие есть цвета»
            # упирается в три белых автомобиля подряд
            color_i = color if count == 1 else PALETTE[(PALETTE.index(color) + i) % len(PALETTE)]
            rows.append({
                "vin": vin(seq), "dealer_id": dealer, "condition": "new", "make": make,
                "model": model, "trim": trim, "year": year, "mileage_km": 12,
                "color": color_i, "price_rub": price, "status": status, "owners": 0,
                "accident_history": "нет", "location": loc,
                "arrived_at": "2026-08-%02d" % (10 + seq % 18),
                "reserved_until": extra.get("reserved_until", ""),
                "ready_at": extra.get("ready_at", ""), "eta": extra.get("eta", ""),
                "sold_at": extra.get("sold_at", ""), "photos": 14,
            })
            seq += 1
    for make, model, trim, year, mileage, color, price, status, owners, accident, extra in USED:
        rows.append({
            "vin": vin(seq), "dealer_id": "dc-west", "condition": "used", "make": make,
            "model": model, "trim": trim, "year": year, "mileage_km": mileage,
            "color": color, "price_rub": price, "status": status, "owners": owners,
            "accident_history": accident,
            "location": "предпродажка" if status == "prep" else "площадка",
            "arrived_at": "2026-08-%02d" % (5 + seq % 20),
            "reserved_until": extra.get("reserved_until", ""),
            "ready_at": extra.get("ready_at", ""), "eta": "",
            "sold_at": extra.get("sold_at", ""), "photos": 22,
        })
        seq += 1
    return rows


# --------------------------------------------------------------------------
# Сервис: прайс, гарантия, машины клиентов, заказ-наряды
# --------------------------------------------------------------------------

SERVICE_PRICE = {
    "_meta": {"disclaimer": DISCLAIMER, "owner": "emp-01", "updated_at": "2026-09-01",
              "valid_until": "2026-12-31", "location": "dc-north"},
    "labor_rate_rub": 3200,
    "code_note": "Коды латиницей (TO-1). Подписи для клиента — в display_name. "
                 "Кириллическое «ТО-1» визуально неотличимо от латинского и в коды не допускается.",
    "maintenance": [
        {"code": "TO-1", "display_name": "ТО-1 (15 000 км)", "interval_km": 15000,
         "price_rub": 11400, "duration_h": 2.0,
         "works": ["замена масла и фильтра", "салонный фильтр", "диагностика тормозов"]},
        {"code": "TO-2", "display_name": "ТО-2 (30 000 км)", "interval_km": 30000,
         "price_rub": 17900, "duration_h": 3.0,
         "works": ["работы ТО-1", "воздушный фильтр", "тормозная жидкость", "диагностика подвески"]},
        {"code": "TO-3", "display_name": "ТО-3 (45 000 км)", "interval_km": 45000,
         "price_rub": 23500, "duration_h": 3.5,
         "works": ["работы ТО-2", "свечи зажигания", "замена антифриза"]},
        {"code": "TO-4", "display_name": "ТО-4 (60 000 км)", "interval_km": 60000,
         "price_rub": 29900, "duration_h": 4.0,
         "works": ["работы ТО-3", "ремень привода", "масло АКПП"]},
    ],
    "common_works": [
        {"name": "Диагностика подвески", "price_rub": 2400, "note": "бесплатно при последующем ремонте"},
        {"name": "Компьютерная диагностика", "price_rub": 2900, "duration_h": 1.0},
        {"name": "Шиномонтаж (комплект)", "price_rub": 4200, "duration_h": 1.0},
        {"name": "Замена тормозных колодок (ось)", "price_rub": 3800, "note": "без стоимости запчастей"},
    ],
    "tolerance": {"km": 2000, "days": 30,
                  "note": "Допуск на прохождение ТО для сохранения гарантии"},
}

WARRANTY = {
    "_meta": {"disclaimer": DISCLAIMER, "owner": "emp-01", "updated_at": "2026-09-01",
              "review_by": "2027-03-01"},
    "base": {"years": 3, "km": 100000, "rule": "что наступит раньше"},
    "components": [
        {"item": "Лакокрасочное покрытие", "years": 3, "km": None},
        {"item": "Сквозная коррозия кузова", "years": 6, "km": None},
        {"item": "Аккумуляторная батарея", "years": 1, "km": None},
        {"item": "Расходные материалы", "years": 1, "km": 20000},
    ],
    "conditions": [
        "Прохождение ТО у официального дилера в допуске ±2 000 км / ±30 дней",
        "Использование оригинальных запчастей и жидкостей",
        "Отсутствие несогласованных изменений конструкции и ПО",
    ],
    "not_covered": [
        "последствия ДТП", "тюнинг и несогласованное ПО", "эксплуатация на треке",
        "повреждения от внешних воздействий", "естественный износ расходников",
    ],
    "decision_rule": {
        "who_decides": "emp-01",
        "assistant_may": "изложить условия регламента и зафиксировать обращение",
        "assistant_may_not": "признать или отклонить гарантийный случай, назвать срок и стоимость",
    },
    "extended": {"name": "АвтоГрад Гарантия+", "years": 2, "price_from_rub": 64000,
                 "note": "оформляется до окончания заводской гарантии"},
}

CUSTOMERS = [
    ("cus-01", "Николай Гордеев",  "+7 900 000-21-01", "dc-north"),
    ("cus-02", "Елена Сафронова",  "+7 900 000-21-02", "dc-north"),
    ("cus-03", "Игорь Демидов",    "+7 900 000-21-03", "dc-north"),
    ("cus-04", "Алина Бирюкова",   "+7 900 000-21-04", "dc-west"),
    ("cus-05", "Пётр Ремезов",     "+7 900 000-21-05", "dc-north"),
    ("cus-06", "Юлия Карпова",     "+7 900 000-21-06", "dc-west"),
    ("cus-07", "Роман Щукин",      "+7 900 000-21-07", "dc-north"),
    ("cus-08", "Светлана Ивлева",  "+7 900 000-21-08", "dc-north"),
    ("cus-09", "Денис Аверин",     "+7 900 000-21-09", "dc-west"),
    ("cus-10", "Марина Осипова",   "+7 900 000-21-10", "dc-north"),
]

# (id, customer, make, model, year, purchased_at, mileage, plate, сценарная роль)
VEHICLES = [
    ("veh-01", "cus-01", "Aurora", "X5", 2024, "2024-03-12", 41200, "А001АА777",
     "ТО-3 подходит по пробегу — базовый сценарий записи"),
    ("veh-02", "cus-02", "Vega", "Cross", 2025, "2025-04-20", 18900, "В002ВВ777",
     "ТО-1 пройдено, ТО-2 приближается"),
    ("veh-03", "cus-03", "Aurora", "X3", 2023, "2023-06-05", 52300, "С003СС777",
     "ТО-3 пропущено, клиент заявляет обслуживание в стороннем сервисе"),
    ("veh-04", "cus-04", "Vega", "Sedan", 2019, "2019-09-14", 168000, "Е004ЕЕ777",
     "гарантия истекла по сроку и пробегу"),
    ("veh-05", "cus-05", "Aurora", "S7", 2025, "2025-02-11", 12400, "К005КК777",
     "три обращения по одному стуку — конфликтный сценарий"),
    ("veh-06", "cus-06", "Vega", "Sport", 2024, "2024-11-30", 33000, "М006ММ777",
     "гарантия активна, плановое обслуживание"),
    ("veh-07", "cus-07", "Vector", "Sedan", 2021, "2021-07-19", 96500, "Н007НН777",
     "непредставленная марка, обслуживается платно"),
    ("veh-08", "cus-08", "Aurora", "X5", 2026, "2026-06-02", 4800, "О008ОО777",
     "новая машина, первое ТО впереди"),
    ("veh-09", "cus-09", "Vega", "Cross", 2022, "2022-08-08", 87000, "Р009РР777",
     "гарантия истекла по сроку в августе 2025"),
    ("veh-10", "cus-10", "Aurora", "X3", 2026, "2026-01-22", 8200, "Т010ТТ777",
     "сейчас в ремонте — сценарий «что с моей машиной»"),
]

# (id, vehicle, opened, status, type, works, sum, closed/plan, note)
WORK_ORDERS = [
    ("wo-1001", "veh-01", "2025-09-18", "closed", "TO-2", "ТО-2 (30 000 км)", 17900,
     "2025-09-18", ""),
    ("wo-1002", "veh-02", "2026-02-14", "closed", "TO-1", "ТО-1 (15 000 км)", 11400,
     "2026-02-14", ""),
    ("wo-1003", "veh-03", "2024-08-03", "closed", "TO-2", "ТО-2 (30 000 км)", 17900,
     "2024-08-03", "последнее обращение в ДЦ"),
    ("wo-1004", "veh-04", "2024-05-21", "closed", "repair", "замена тормозных дисков", 24600,
     "2024-05-22", "гарантия уже истекла, работы платные"),
    ("wo-1005", "veh-05", "2026-06-09", "closed", "warranty", "диагностика стука подвески", 0,
     "2026-06-10", "обращение 1 из 3, дефект не выявлен"),
    ("wo-1006", "veh-05", "2026-07-21", "closed", "warranty", "замена стойки стабилизатора", 0,
     "2026-07-23", "обращение 2 из 3"),
    ("wo-1007", "veh-05", "2026-09-01", "waiting_parts", "warranty", "повторная диагностика, заказ рычага",
     0, "2026-09-16", "обращение 3 из 3, простой 11 дней — красный приоритет"),
    ("wo-1008", "veh-06", "2026-03-12", "closed", "TO-1", "ТО-1 (15 000 км)", 11400,
     "2026-03-12", ""),
    ("wo-1009", "veh-07", "2026-04-05", "closed", "repair", "замена сцепления", 68400,
     "2026-04-08", "непредставленная марка, платный ремонт"),
    ("wo-1010", "veh-09", "2025-10-02", "closed", "TO-4", "ТО-4 (60 000 км)", 29900,
     "2025-10-02", ""),
    ("wo-1011", "veh-10", "2026-09-09", "in_progress", "repair", "замена переднего бампера после ДТП",
     112000, "2026-09-12", "страховой случай, не гарантия"),
    ("wo-1012", "veh-01", "2026-09-11", "scheduled", "TO-3", "ТО-3 (45 000 км)", 23500,
     "2026-09-16", "запись создана ассистентом — демонстрационный сценарий"),
]

# --------------------------------------------------------------------------
# HR: вакансии, профили ролей, кандидаты
# --------------------------------------------------------------------------

VACANCIES = [
    ("vac-01", "Продавец-консультант (новые автомобили)", "sales_new", "dc-north",
     60000, 180000, "2/2, 09:00–21:00", 3,
     "Оклад 60 000 ₽ + процент, совокупно 110 000–180 000 ₽ при выполнении плана"),
    ("vac-02", "Продавец-консультант (автомобили с пробегом)", "sales_used", "dc-west",
     55000, 170000, "2/2, 09:00–21:00", 2,
     "Оклад 55 000 ₽ + процент от маржи"),
    ("vac-03", "Мастер-приёмщик", "service", "dc-north",
     90000, 130000, "2/2, 08:00–20:00", 2,
     "Оклад 70 000 ₽ + процент от нормо-часов и допродаж"),
    ("vac-04", "Автомеханик", "service", "dc-north",
     80000, 140000, "5/2, 08:00–17:00", 3,
     "Сдельная оплата от выработки нормо-часов"),
    ("vac-05", "Менеджер отдела дополнительного оборудования", "sales_new", "dc-north",
     50000, 120000, "5/2, 10:00–19:00", 1,
     "Оклад 50 000 ₽ + процент с установленного оборудования"),
    ("vac-06", "Специалист отдела кредитования", "credit", "dc-west",
     70000, 130000, "2/2, 10:00–20:00", 1,
     "Оклад 70 000 ₽ + вознаграждение за одобренные заявки"),
]

ROLE_PROFILES = {
    "vac-01": {
        "stop_factors": ["нет опыта активных продаж от 1 года", "нет прав категории B",
                         "не готов к графику 2/2"],
        "screening": ["Опыт активных продаж и в какой сфере", "Работали ли с CRM и какой",
                      "Средний чек и число сделок в месяц", "Готовность к графику 2/2",
                      "Права категории B и стаж", "Ожидания по доходу",
                      "Готовность к испытательному сроку 3 месяца"],
        "nice_to_have": ["опыт в авторетейле", "знание кредитных продуктов"],
    },
    "vac-02": {
        "stop_factors": ["нет опыта продаж от 1 года", "нет прав категории B"],
        "screening": ["Опыт продаж и сфера", "Опыт оценки автомобилей",
                      "Знание работы с трейд-ин", "Готовность к графику 2/2",
                      "Ожидания по доходу", "Права категории B"],
        "nice_to_have": ["опыт выкупа автомобилей", "навык осмотра ЛКП"],
    },
    "vac-03": {
        "stop_factors": ["нет опыта в автосервисе от 1 года", "нет прав категории B",
                         "не готов к графику 2/2"],
        "screening": ["Опыт работы в сервисе и в каком качестве",
                      "Опыт работы с системами расчёта ремонта", "Число машин в день",
                      "Опыт допродаж работ", "Готовность к графику 2/2", "Ожидания по доходу"],
        "nice_to_have": ["опыт работы у официального дилера", "техническое образование"],
    },
    "vac-04": {
        "stop_factors": ["нет профильного опыта от 2 лет", "нет допуска к работе с электрооборудованием"],
        "screening": ["Специализация и опыт", "Опыт работы с гарантийным ремонтом",
                      "Выработка нормо-часов в месяц", "Наличие собственного инструмента",
                      "Ожидания по доходу"],
        "nice_to_have": ["опыт диагностики", "профильный сертификат"],
    },
    "vac-05": {
        "stop_factors": ["нет опыта продаж от 6 месяцев"],
        "screening": ["Опыт продаж", "Опыт работы с дополнительным оборудованием",
                      "Готовность к графику 5/2", "Ожидания по доходу"],
        "nice_to_have": ["технический кругозор", "опыт в автобизнесе"],
    },
    "vac-06": {
        "stop_factors": ["нет опыта работы с банковскими продуктами", "нет высшего образования"],
        "screening": ["Опыт работы с кредитными заявками", "Знание банков-партнёров",
                      "Опыт работы со страховыми продуктами", "Готовность к графику 2/2",
                      "Ожидания по доходу"],
        "nice_to_have": ["опыт в автокредитовании", "опыт работы с лизингом"],
    },
}

CAND_NAMES = [
    "Антон Мерзляков", "Виктория Панина", "Глеб Останин", "Дина Ярцева", "Егор Сухов",
    "Жанна Литвинова", "Захар Бобылёв", "Инна Рогачёва", "Кирилл Насонов", "Лидия Ковригина",
    "Максим Ельцов", "Наталья Дробышева", "Олег Трифонов", "Полина Хомякова", "Руслан Асланов",
    "София Верещагина", "Тимур Бадретдинов", "Ульяна Морозова", "Фёдор Клюев", "Эльвира Нуриева",
]

# (vacancy, опыт лет, текущая роль, город, права, CRM, график 2/2, ожидания, источник, статус)
CAND_ROWS = [
    ("vac-01", 3, "Менеджер по продажам в электронике", "Москва", True, True, True, 150000, "hh.ru", "new"),
    ("vac-01", 0, "Студент", "Москва", False, False, True, 120000, "hh.ru", "stop_factor"),
    ("vac-01", 5, "Продавец-консультант в автосалоне", "Москва", True, True, True, 180000, "рекомендация", "new"),
    ("vac-01", 2, "Риелтор", "Подольск", True, False, False, 140000, "hh.ru", "stop_factor"),
    ("vac-01", 4, "Менеджер по продажам B2C", "Москва", True, True, True, 160000, "Telegram", "new"),
    ("vac-02", 6, "Специалист по выкупу автомобилей", "Москва", True, True, True, 170000, "hh.ru", "new"),
    ("vac-02", 1, "Автомойщик", "Москва", True, False, True, 120000, "hh.ru", "stop_factor"),
    ("vac-02", 3, "Продавец автомобилей с пробегом", "Химки", True, True, True, 165000, "hh.ru", "new"),
    ("vac-03", 4, "Мастер-приёмщик независимого СТО", "Москва", True, True, True, 125000, "hh.ru", "new"),
    ("vac-03", 0, "Менеджер по логистике", "Москва", True, False, True, 110000, "hh.ru", "stop_factor"),
    ("vac-03", 7, "Мастер-приёмщик официального дилера", "Москва", True, True, True, 130000, "рекомендация", "new"),
    ("vac-03", 2, "Автомеханик", "Люберцы", True, False, True, 115000, "hh.ru", "new"),
    ("vac-04", 8, "Автомеханик официального дилера", "Москва", True, False, True, 140000, "hh.ru", "new"),
    ("vac-04", 3, "Слесарь по ремонту", "Москва", True, False, True, 120000, "hh.ru", "new"),
    ("vac-04", 1, "Шиномонтажник", "Балашиха", True, False, True, 100000, "hh.ru", "stop_factor"),
    ("vac-05", 2, "Продавец в магазине автозапчастей", "Москва", True, False, True, 110000, "hh.ru", "new"),
    ("vac-05", 4, "Менеджер по продажам аксессуаров", "Москва", True, True, True, 120000, "Telegram", "new"),
    ("vac-06", 5, "Кредитный специалист банка", "Москва", True, True, True, 130000, "hh.ru", "new"),
    ("vac-06", 2, "Операционист банка", "Москва", True, True, True, 115000, "hh.ru", "new"),
    ("vac-06", 0, "Продавец-кассир", "Москва", False, False, True, 100000, "hh.ru", "stop_factor"),
]

PROMOTIONS = {
    "_meta": {"disclaimer": DISCLAIMER, "owner": "emp-03", "updated_at": TODAY,
              "rule": "Акция с истёкшей датой не предлагается ни при каких условиях"},
    "promotions": [
        {"id": "ACT-114", "name": "Трейд-ин Осень", "from": "2026-09-01", "to": "2026-10-31",
         "benefit_rub": 120000, "applies_to": ["Vega Cross", "Vega Sedan"],
         "conditions": "сдаваемый автомобиль не старше 10 лет",
         "not_combinable_with": ["ACT-109"], "status": "active"},
        {"id": "ACT-109", "name": "Кредитная ставка 0,1%", "from": "2026-08-01", "to": "2026-09-30",
         "benefit_rub": None, "applies_to": ["Aurora X3", "Aurora X5"],
         "conditions": "первоначальный взнос от 40%, срок до 36 месяцев",
         "not_combinable_with": ["ACT-114"], "status": "active"},
        {"id": "ACT-102", "name": "Летний сервисный пакет", "from": "2026-06-01", "to": "2026-08-31",
         "benefit_rub": 15000, "applies_to": ["сервис"],
         "conditions": "при прохождении ТО в период акции",
         "not_combinable_with": [], "status": "expired",
         "note": "Оставлена намеренно: проверка правила про недействующие акции"},
    ],
}

STOCK_META = {
    "_meta": {"disclaimer": DISCLAIMER},
    "feed": {
        "source": "fixtures/stock.csv",
        "refresh_minutes": 10,
        "generated_at": f"{TODAY}T09:40:00+03:00",
        "stale_after_hours": 4,
        "on_stale": "Ассистент отказывается отвечать по наличию и статусу, предлагает связь с менеджером",
        "test_modes": {
            "fresh": "generated_at = текущее время",
            "stale": "generated_at = текущее время минус 5 часов — проверка правила рассинхрона",
            "down": "файл недоступен — проверка уровня деградации L1",
        },
    },
    "statuses": {
        "in_stock": "в наличии на площадке",
        "reserved": "в резерве до даты reserved_until",
        "prep": "на предпродажной подготовке, готовность ready_at",
        "in_transit": "в пути, ожидаемая дата eta",
        "sold": "продан, дата sold_at",
    },
}


HR_POLICIES = {
    "_meta": {
        "disclaimer": DISCLAIMER,
        "owner": "emp-06",
        "updated_at": TODAY,
        "review_by": "2027-03-01",
        "purpose": "Внутренний контур HR-ассистента (ADR-0001): вопросы действующих сотрудников",
        "portal": "АвтоГрад.Кадры",
        "contact": {"person": "emp-06", "email": "hr@avtograd-demo.ru", "ext": "4412"},
    },
    "vacation": {
        "days_per_year": 28,
        "request_lead_days": 14,
        "how": "Заявление в портале «АвтоГрад.Кадры», раздел «Отпуска»",
        "approval": "Руководитель подразделения, срок согласования 3 рабочих дня",
        "note": "Остаток дней виден в личном кабинете портала",
    },
    "documents": [
        {"name": "Справка 2-НДФЛ", "how": "Портал, раздел «Документы»", "days": 3},
        {"name": "Справка с места работы", "how": "Портал, раздел «Документы»", "days": 3},
        {"name": "Копия трудовой книжки", "how": "Заявка в портале", "days": 5},
    ],
    "sick_leave": {
        "notify": "Сообщить руководителю до начала смены",
        "document": "Электронный листок нетрудоспособности, номер передаётся в кадры",
        "note": "Расчёт выплат ведёт расчётная группа, сроки индивидуальны",
    },
    "shift_change": {
        "lead_days": 3,
        "approval": "Руководитель подразделения",
        "note": "Обмен сменами между сотрудниками согласуется обеими сторонами",
    },
    "probation_months": 3,
    "dms_from_month": 6,
    "onboarding": {
        "duration_days": 14,
        "checkpoints": [
            {"days": "1–3", "items": ["оформление документов", "выдача доступов",
                                       "знакомство с наставником", "экскурсия по ДЦ"]},
            {"days": "4–7", "items": ["модельный ряд и комплектации",
                                       "стандарт общения с клиентом", "работа в CRM"]},
            {"days": "8–14", "items": ["самостоятельные диалоги под наставником",
                                        "тест по модельному ряду", "первая встреча с руководителем"]},
        ],
        "mentor_rule": "Наставник закрепляется в первый рабочий день, контакт — в карточке сотрудника",
    },
    "assistant_limits": {
        "may": ["ответить по регламенту из этого файла", "назвать срок и способ подачи заявки",
                "подсказать адресата согласования"],
        "may_not": ["давать юридические консультации по ТК РФ",
                    "называть суммы выплат и считать зарплату",
                    "трактовать спорные ситуации: увольнение, дисциплинарные меры",
                    "раскрывать данные одного сотрудника другому"],
        "escalate_to": "emp-06",
    },
}


def main() -> None:
    print(f"Фикстуры «АвтоГрада» → {OUT}")
    write("dealer.json", DEALER)
    write("staff.json", STAFF)
    write("working_hours.json", WORKING_HOURS)
    write("models.json", MODELS)
    write("service_price.json", SERVICE_PRICE)
    write("warranty.json", WARRANTY)
    write("promotions.json", PROMOTIONS)
    write("hr_policies.json", HR_POLICIES)
    write("stock_meta.json", STOCK_META)

    stock = build_stock()
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "stock.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STOCK_COLUMNS, delimiter=";")
        writer.writeheader()
        writer.writerows(stock)
    print(f"  {'stock.csv':<24} {(OUT / 'stock.csv').stat().st_size:>7} байт  ({len(stock)} авто)")

    write("customers.json", {
        "_meta": {"disclaimer": DISCLAIMER,
                  "pii_note": "Вымышленные клиенты. Телефоны в нероутируемом диапазоне.",
                  "updated_at": TODAY},
        "customers": [{"id": i, "name": n, "phone": p, "home_location": loc,
                       "consent_at": "2026-01-15", "consent_version": "v1.2"}
                      for i, n, p, loc in CUSTOMERS],
    })

    write("vehicles.json", {
        "_meta": {"disclaimer": DISCLAIMER, "updated_at": TODAY,
                  "note": "Автомобили клиентов в обслуживании — не путать со стоком"},
        "vehicles": [{"id": i, "customer_id": c, "vin": vin(900 + n), "make": mk, "model": md,
                      "year": y, "purchased_at": pa, "mileage_km": km, "plate": pl,
                      "scenario_role": role}
                     for n, (i, c, mk, md, y, pa, km, pl, role) in enumerate(VEHICLES, start=1)],
    })

    write("work_orders.json", {
        "_meta": {"disclaimer": DISCLAIMER, "updated_at": TODAY,
                  "statuses": ["scheduled", "in_progress", "waiting_parts", "closed"]},
        "work_orders": [{"id": i, "vehicle_id": v, "opened_at": o, "status": s, "type": t,
                         "works": w, "sum_rub": amount, "closed_or_planned": d, "note": note}
                        for i, v, o, s, t, w, amount, d, note in WORK_ORDERS],
    })

    write("vacancies.json", {
        "_meta": {"disclaimer": DISCLAIMER, "owner": "emp-06", "updated_at": TODAY},
        "vacancies": [{"id": i, "title": t, "department": d, "location": loc,
                       "salary_from_rub": lo, "salary_to_rub": hi, "schedule": sch,
                       "openings": op, "compensation": comp, "probation_months": 3,
                       "benefits": ["ДМС с 6-го месяца", "корпоративное обучение",
                                    "скидка на обслуживание автомобиля"]}
                      for i, t, d, loc, lo, hi, sch, op, comp in VACANCIES],
    })

    write("role_profiles.json", {
        "_meta": {"disclaimer": DISCLAIMER, "owner": "emp-06", "updated_at": TODAY,
                  "bias_note": "Скоринг только по перечисленным критериям. "
                               "Возраст, пол, гражданство и семейное положение не учитываются."},
        "profiles": ROLE_PROFILES,
    })

    write("candidates.json", {
        "_meta": {"disclaimer": DISCLAIMER, "updated_at": TODAY,
                  "pii_note": "Вымышленные кандидаты. Резюме сокращены до полей скрининга.",
                  "retention": "6 месяцев с даты отклика"},
        "candidates": [
            {"id": f"cand-{n:02d}", "name": CAND_NAMES[n - 1], "vacancy_id": vac,
             "experience_years": exp, "current_role": role, "city": city,
             "driving_license_b": lic, "crm_experience": crm, "ready_shift_2_2": shift,
             "salary_expectation_rub": pay, "source": src, "status": st,
             "applied_at": "2026-09-%02d" % (1 + n % 10)}
            for n, (vac, exp, role, city, lic, crm, shift, pay, src, st)
            in enumerate(CAND_ROWS, start=1)
        ],
    })

    print("\nГотово.")


if __name__ == "__main__":
    main()
