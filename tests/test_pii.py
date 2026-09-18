"""Тесты шлюза персональных данных: без сети и без вызовов модели.

Проверяют то, что должно быть верно всегда: значение не уходит в модель,
возвращается клиенту, один и тот же телефон получает один плейсхолдер,
испорченный моделью плейсхолдер обнаруживается до отправки человеку.

Запуск:  python tests/test_pii.py
Код возврата 1 — есть падения.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from pii import Vault, pseudonymize, restore  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

passed, failed = 0, []


def check(condition: bool, title: str) -> None:
    global passed
    if condition:
        passed += 1
    else:
        failed.append(title)


def test_kinds() -> None:
    vault = Vault()
    text = ("VIN TESTAG00000000901, телефон +7 900 000-21-01, "
            "госномер А001АА777, почта ivan@example.com")
    hidden = vault.hide(text)
    for value in ("TESTAG00000000901", "+7 900 000-21-01", "А001АА777", "ivan@example.com"):
        check(value not in hidden, f"значение не уходит в модель: {value}")
    check(vault.map.get("{{VIN_1}}") == "TESTAG00000000901",
          "VIN распознан целиком, а не разорван телефонным шаблоном")
    check(vault.show(hidden) == text, "текст разворачивается обратно без потерь")


def test_stability() -> None:
    vault = Vault()
    hidden = vault.hide("Звоните на +7 900 000-21-01 или на +7 900 000-21-01")
    check(hidden.count("{{PHONE_1}}") == 2, "одно значение — один плейсхолдер")

    # Вторая реплика диалога: словарь приходит из базы, нумерация продолжается.
    later = Vault(vault.map)
    hidden2 = later.hide("Ещё есть +7 900 000-55-55")
    check("{{PHONE_2}}" in hidden2, "новое значение получает следующий номер")
    check(later.map["{{PHONE_1}}"] == "+7 900 000-21-01",
          "старое значение сохраняет свой плейсхолдер")


def test_structured_values() -> None:
    """Имя человека шаблоном не поймать — оно приходит из карточки владельца."""
    vault = Vault()
    vault.add("NAME", "Николай Гордеев")
    hidden = vault.hide("Карточка владельца: Николай Гордеев, Aurora X5")
    check("Гордеев" not in hidden, "имя владельца не уходит в модель")
    check("Aurora X5" in hidden, "марка и модель остаются — это не персональные данные")
    check(vault.show(hidden).startswith("Карточка владельца: Николай Гордеев"),
          "имя возвращается в ответ клиенту")


def test_leftovers() -> None:
    vault = Vault()
    vault.hide("VIN TESTAG00000000901")
    # Модель придумала плейсхолдер, которого ей не давали.
    spoiled = vault.show("По {{VIN_1}} всё готово, позвоним на {{PHONE_7}}")
    check("TESTAG00000000901" in spoiled, "известный плейсхолдер развёрнут")
    check(vault.leftovers(spoiled) == ["{{PHONE_7}}"],
          "неизвестный плейсхолдер найден до отправки клиенту")
    check(vault.leftovers("Обычный ответ без скобок") == [],
          "чистый ответ не считается испорченным")


def test_money_is_not_pii() -> None:
    clean, mapping = pseudonymize("Цена 2 490 000 ₽, бюджет до 2 500 000, скидка 200 000")
    check(not mapping, "суммы за персональные данные не принимаются")
    check(clean.count("2 490 000") == 1, "текст с ценами не меняется")


def test_compat_helpers() -> None:
    clean, mapping = pseudonymize("Телефон +7 900 000-21-01")
    check(restore(clean, mapping) == "Телефон +7 900 000-21-01",
          "совместимые обёртки работают как пара")


def main() -> int:
    for test in (test_kinds, test_stability, test_structured_values, test_leftovers,
                 test_money_is_not_pii, test_compat_helpers):
        test()
    print(f"Проверок выполнено: {passed + len(failed)}")
    if failed:
        print(f"\nПадений: {len(failed)}")
        for title in failed:
            print(f"  · {title}")
        return 1
    print("Падений нет. Шлюз персональных данных держит границу.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
