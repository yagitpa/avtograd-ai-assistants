"""Шлюз персональных данных: замена значений плейсхолдерами и обратно.

Правило из ADR-0002: персональные данные не уходят в модель. В учебном
контуре данные вымышлены, и соблазн отложить шлюз велик — но отправка ПДн
граждан РФ в OpenAI это трансграничная передача, и переписывать ядро под
это правило позже дороже, чем собрать его сразу.

Шлюз двусторонний и работает на всём, что видит модель: реплики
собеседника, блок `ЗНАНИЯ` с карточкой владельца, блок `КОНТЕКСТ`.
Ответ модели приходит с плейсхолдерами и разворачивается обратно перед
отправкой человеку — клиент видит свой VIN, а не `{{VIN_1}}`.

Используется из `tools/assistant.py` (путь к модели) и `tools/store.py`
(хранение). Это один и тот же словарь значений: текст, сохранённый в базе,
и текст, ушедший в модель, обезличены одинаково.
"""

from __future__ import annotations

import re

# Порядок важен: телефонный шаблон находит одиннадцать цифр и внутри VIN.
# Сначала распознаётся длинное и структурное, потом свободное.
PII_PATTERNS = [
    ("VIN", re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.I)),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", re.I)),
    ("PLATE", re.compile(r"\b[авекмнорстухabekmhopctyx]\s?\d{3}\s?[авекмнорстухabekmhopctyx]{2}"
                         r"\s?\d{2,3}\b", re.I)),
    ("PHONE", re.compile(r"(?:\+7|\+?\d{1,3}|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}")),
]

PLACEHOLDER = re.compile(r"\{\{([A-Z]+)_(\d+)\}\}")
# Модель иногда портит плейсхолдер: теряет скобку, ставит пробел вместо
# подчёркивания, переводит в нижний регистр. Такой обломок нужно заметить
# до отправки клиенту — скобки в ответе выглядят как поломка.
BROKEN = re.compile(r"\{\{?\s*[A-Za-z]+[\s_]*\d*\s*\}?\}?")


class Vault:
    """Словарь значений одного диалога: плейсхолдер ↔ настоящее значение."""

    def __init__(self, known: dict[str, str] | None = None):
        self.map: dict[str, str] = dict(known or {})
        self.back: dict[str, str] = {v: k for k, v in self.map.items()}
        self.counters: dict[str, int] = {}
        for placeholder in self.map:
            match = PLACEHOLDER.fullmatch(placeholder)
            if match:
                kind, number = match.group(1), int(match.group(2))
                self.counters[kind] = max(self.counters.get(kind, 0), number)

    def token(self, kind: str, value: str) -> str:
        if value in self.back:
            return self.back[value]
        self.counters[kind] = self.counters.get(kind, 0) + 1
        placeholder = "{{%s_%d}}" % (kind, self.counters[kind])
        self.map[placeholder] = value
        self.back[value] = placeholder
        return placeholder

    def add(self, kind: str, value: str) -> str:
        """Заводит плейсхолдер для значения, известного из структурных данных.

        Имя клиента регулярным выражением не поймать, но карточку владельца
        ядро собирает само и знает, что имя — это имя.
        """
        value = (value or "").strip()
        if not value:
            return ""
        return self.token(kind, value)

    def hide(self, text: str) -> str:
        out = text
        for kind, pattern in PII_PATTERNS:
            out = pattern.sub(lambda m, k=kind: self.token(k, m.group(0)), out)
        # Значения, заведённые через add (имена), шаблонами не ловятся —
        # подставляем их прямой заменой, от длинных к коротким, чтобы
        # «Николай Гордеев» не распался на два плейсхолдера.
        for value in sorted(self.back, key=len, reverse=True):
            if value in out:
                out = out.replace(value, self.back[value])
        return out

    def show(self, text: str) -> str:
        for placeholder, value in self.map.items():
            text = text.replace(placeholder, value)
        return text

    def leftovers(self, text: str) -> list[str]:
        """Обломки плейсхолдеров, оставшиеся после разворачивания."""
        return [m.group(0) for m in PLACEHOLDER.finditer(text)]


def pseudonymize(text: str, known: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """Совместимая обёртка: текст и карта значений."""
    vault = Vault(known)
    return vault.hide(text), vault.map


def restore(text: str, mapping: dict[str, str]) -> str:
    return Vault(mapping).show(text)
