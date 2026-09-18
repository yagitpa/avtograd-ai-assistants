"""Хранилище диалогов: контакты, каналы, сообщения, кэш ответов.

Срез этапа 4 из ADR-0004 и ADR-0017. СУБД учебная — SQLite вместо
PostgreSQL, — но схема боевая, и это важнее: один человек приходит из
Telegram, потом из WhatsApp, потом звонит, и это должен быть один человек
с одной историей. Привязка диалога напрямую к `telegram_user_id` означала
бы переписывание всех данных при первой же склейке каналов, поэтому
`contact` и много `channel_identity` к нему — с первого дня.

Что здесь есть:

* `contact` — человек; `channel_identity` — его адрес в конкретном канале;
* `dialog` — разговор в канале, с текущим маршрутом;
* `message` — реплика с версией промпта и знаний, из которых собран ответ;
* `pii_token` — отдельная таблица значений, вынутых из текста сообщений;
* `answer_cache` — ответы на обезличенные вопросы.

Персональные данные в тексте сообщений заменяются плейсхолдерами, а сами
значения лежат отдельной таблицей. В учебном контуре это разделение
структурное: в бою та же таблица шифруется на уровне столбцов, ключ живёт
в Vault, доступ идёт через сервисный API (ADR-0002). Смысл разделения в
том, что выгрузка диалогов для разбора качества не тянет за собой телефоны.

Запуск как утилита:
    python tools/store.py stats             что накопилось
    python tools/store.py purge             удалить просроченное
    python tools/store.py forget <contact>  забыть человека по требованию
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "var" / "avtograd.db"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# Сроки хранения из ADR-0002: тела диалогов с ПДн — 90 дней, обезличенный
# аудит — год. Здесь применяется первый: диалоги старше срока удаляются
# вместе со значениями ПДн.
DIALOG_TTL_DAYS = 90

SCHEMA = """
CREATE TABLE IF NOT EXISTS contact (
    id          INTEGER PRIMARY KEY,
    created_at  TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    note        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS channel_identity (
    id          INTEGER PRIMARY KEY,
    contact_id  INTEGER NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL,
    channel_user_id TEXT NOT NULL,
    handle      TEXT DEFAULT '',
    created_at  TEXT NOT NULL,
    UNIQUE (channel, channel_user_id)
);

CREATE TABLE IF NOT EXISTS dialog (
    id          INTEGER PRIMARY KEY,
    contact_id  INTEGER NOT NULL REFERENCES contact(id) ON DELETE CASCADE,
    channel     TEXT NOT NULL,
    route       TEXT,
    opened_at   TEXT NOT NULL,
    last_at     TEXT NOT NULL,
    closed_at   TEXT
);

CREATE TABLE IF NOT EXISTS message (
    id          INTEGER PRIMARY KEY,
    dialog_id   INTEGER NOT NULL REFERENCES dialog(id) ON DELETE CASCADE,
    direction   TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    text        TEXT NOT NULL,
    route       TEXT,
    mode        TEXT,
    prompt_version TEXT,
    kb_version  TEXT,
    records     TEXT DEFAULT '',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pii_token (
    id          INTEGER PRIMARY KEY,
    dialog_id   INTEGER NOT NULL REFERENCES dialog(id) ON DELETE CASCADE,
    placeholder TEXT NOT NULL,
    kind        TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE (dialog_id, placeholder)
);

CREATE TABLE IF NOT EXISTS answer_cache (
    key         TEXT PRIMARY KEY,
    role        TEXT NOT NULL,
    question    TEXT NOT NULL,
    text        TEXT NOT NULL,
    mode        TEXT NOT NULL,
    records     TEXT DEFAULT '',
    prompt_version TEXT NOT NULL,
    kb_version  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_message_dialog ON message(dialog_id, id);
CREATE INDEX IF NOT EXISTS idx_dialog_contact ON dialog(contact_id, last_at);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# --- Псевдонимизация -------------------------------------------------------

# Порядок важен: телефонный шаблон находит одиннадцать цифр и внутри VIN.
# Сначала распознаётся длинное и структурное, потом свободное.
PII_PATTERNS = [
    ("VIN", re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b", re.I)),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", re.I)),
    ("PLATE", re.compile(r"\b[авекмнорстухabekmhopctyx]\s?\d{3}\s?[авекмнорстухabekmhopctyx]{2}"
                         r"\s?\d{2,3}\b", re.I)),
    ("PHONE", re.compile(r"(?:\+7|\+?\d{1,3}|8)[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}")),
]


def pseudonymize(text: str, known: dict[str, str] | None = None) -> tuple[str, dict[str, str]]:
    """Заменяет ПДн плейсхолдерами. Возвращает текст и карту «плейсхолдер → значение».

    Одно и то же значение в пределах диалога получает один и тот же
    плейсхолдер: иначе один телефон, названный дважды, выглядел бы в
    выгрузке как два разных человека.
    """
    mapping = dict(known or {})
    back = {value: key for key, value in mapping.items()}
    counters: dict[str, int] = {}
    for placeholder in mapping:
        kind = placeholder.strip("{}").rsplit("_", 1)[0]
        counters[kind] = max(counters.get(kind, 0),
                             int(placeholder.strip("{}").rsplit("_", 1)[1]))

    def swap(kind: str, match: re.Match) -> str:
        value = match.group(0)
        if value in back:
            return back[value]
        counters[kind] = counters.get(kind, 0) + 1
        placeholder = "{{%s_%d}}" % (kind, counters[kind])
        mapping[placeholder] = value
        back[value] = placeholder
        return placeholder

    out = text
    for kind, pattern in PII_PATTERNS:
        out = pattern.sub(lambda m, k=kind: swap(k, m), out)
    return out, mapping


def restore(text: str, mapping: dict[str, str]) -> str:
    for placeholder, value in mapping.items():
        text = text.replace(placeholder, value)
    return text


# --- Контакты и диалоги ----------------------------------------------------

class Store:
    def __init__(self, path: Path | None = None):
        self.conn = connect(path)

    def close(self) -> None:
        self.conn.close()

    def identify(self, channel: str, channel_user_id: str | int, handle: str = "") -> int:
        """Контакт по адресу в канале. Новый адрес — новый контакт.

        Склейка контактов по нормализованному телефону — отдельная операция
        со своим аудитом (ADR-0004). Автоматически по имени или по похожести
        не склеиваем никогда: цена ошибки — чужая история обслуживания.
        """
        uid = str(channel_user_id)
        row = self.conn.execute(
            "SELECT contact_id FROM channel_identity WHERE channel = ? AND channel_user_id = ?",
            (channel, uid)).fetchone()
        stamp = now_iso()
        if row:
            self.conn.execute("UPDATE contact SET last_seen_at = ? WHERE id = ?",
                              (stamp, row["contact_id"]))
            if handle:
                self.conn.execute(
                    "UPDATE channel_identity SET handle = ? WHERE channel = ? AND channel_user_id = ?",
                    (handle, channel, uid))
            self.conn.commit()
            return int(row["contact_id"])
        cur = self.conn.execute(
            "INSERT INTO contact (created_at, last_seen_at) VALUES (?, ?)", (stamp, stamp))
        contact_id = int(cur.lastrowid)
        self.conn.execute(
            "INSERT INTO channel_identity (contact_id, channel, channel_user_id, handle, created_at)"
            " VALUES (?, ?, ?, ?, ?)", (contact_id, channel, uid, handle, stamp))
        self.conn.commit()
        return contact_id

    def open_dialog(self, contact_id: int, channel: str, route: str | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO dialog (contact_id, channel, route, opened_at, last_at)"
            " VALUES (?, ?, ?, ?, ?)", (contact_id, channel, route, now_iso(), now_iso()))
        self.conn.commit()
        return int(cur.lastrowid)

    def current_dialog(self, contact_id: int, channel: str, idle_hours: int = 12) -> int:
        """Текущий диалог или новый, если человек молчал дольше idle_hours.

        Разговор, возобновлённый через сутки, — это новый разговор: тянуть
        в него вчерашний контекст значит отвечать на позавчерашний вопрос.
        """
        row = self.conn.execute(
            "SELECT id, last_at FROM dialog WHERE contact_id = ? AND channel = ? AND closed_at IS NULL"
            " ORDER BY id DESC LIMIT 1", (contact_id, channel)).fetchone()
        if row:
            last = datetime.fromisoformat(row["last_at"])
            if datetime.now() - last < timedelta(hours=idle_hours):
                return int(row["id"])
            self.conn.execute("UPDATE dialog SET closed_at = ? WHERE id = ?",
                              (now_iso(), row["id"]))
            self.conn.commit()
        return self.open_dialog(contact_id, channel)

    def close_dialog(self, dialog_id: int) -> None:
        """Закрывает разговор. Следующее сообщение откроет новый — с чистого листа."""
        self.conn.execute("UPDATE dialog SET closed_at = ? WHERE id = ?", (now_iso(), dialog_id))
        self.conn.commit()

    def set_route(self, dialog_id: int, route: str | None) -> None:
        self.conn.execute("UPDATE dialog SET route = ?, last_at = ? WHERE id = ?",
                          (route, now_iso(), dialog_id))
        self.conn.commit()

    def route_of(self, dialog_id: int) -> str | None:
        row = self.conn.execute("SELECT route FROM dialog WHERE id = ?", (dialog_id,)).fetchone()
        return row["route"] if row else None

    def mapping(self, dialog_id: int) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT placeholder, value FROM pii_token WHERE dialog_id = ?", (dialog_id,)).fetchall()
        return {r["placeholder"]: r["value"] for r in rows}

    def add_message(self, dialog_id: int, direction: str, text: str, route: str | None = None,
                    mode: str = "", prompt_version: str = "", kb_version: str = "",
                    records: list[str] | None = None) -> None:
        clean, mapping = pseudonymize(text, self.mapping(dialog_id))
        stamp = now_iso()
        for placeholder, value in mapping.items():
            kind = placeholder.strip("{}").rsplit("_", 1)[0]
            self.conn.execute(
                "INSERT OR IGNORE INTO pii_token (dialog_id, placeholder, kind, value, created_at)"
                " VALUES (?, ?, ?, ?, ?)", (dialog_id, placeholder, kind, value, stamp))
        self.conn.execute(
            "INSERT INTO message (dialog_id, direction, text, route, mode, prompt_version,"
            " kb_version, records, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dialog_id, direction, clean, route, mode, prompt_version, kb_version,
             ",".join(records or []), stamp))
        self.conn.execute("UPDATE dialog SET last_at = ? WHERE id = ?", (stamp, dialog_id))
        self.conn.commit()

    def history(self, dialog_id: int, limit: int = 12, route: str | None = None) -> list[dict]:
        """История для модели — с восстановленными значениями.

        В базе текст лежит обезличенным; шлюз, закрывающий передачу ПДн в
        модель, — отдельная часть этапа 4, здесь его ещё нет. Разделение
        хранения сделано раньше шлюза сознательно: оно определяет схему,
        а шлюз — только путь вызова.
        """
        rows = self.conn.execute(
            "SELECT direction, text, route FROM message WHERE dialog_id = ? ORDER BY id DESC LIMIT ?",
            (dialog_id, limit)).fetchall()
        mapping = self.mapping(dialog_id)
        out = []
        for row in rows:  # от свежих к старым
            # Переключение маршрута обрывает контекст: сервисный ассистент не
            # должен видеть переписку про покупку и наоборот. Запись при этом
            # остаётся в базе — обрывается только то, что уходит в модель.
            if route and row["route"] and row["route"] != route:
                break
            out.append({"role": "user" if row["direction"] == "in" else "assistant",
                        "content": restore(row["text"], mapping)})
        return list(reversed(out))

    def contact_summary(self, contact_id: int) -> dict:
        """Что известно о вернувшемся человеке — без содержания прошлых разговоров."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS dialogs, MIN(opened_at) AS first_at, MAX(last_at) AS last_at"
            " FROM dialog WHERE contact_id = ?", (contact_id,)).fetchone()
        routes = self.conn.execute(
            "SELECT route, COUNT(*) AS n FROM dialog WHERE contact_id = ? AND route IS NOT NULL"
            " GROUP BY route ORDER BY n DESC", (contact_id,)).fetchall()
        return {"dialogs": int(row["dialogs"] or 0),
                "first_at": row["first_at"], "last_at": row["last_at"],
                "routes": [r["route"] for r in routes]}

    # --- Кэш ответов (ADR-0017) --------------------------------------------

    def cache_get(self, key: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM answer_cache WHERE key = ?", (key,)).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["expires_at"]) <= datetime.now():
            self.conn.execute("DELETE FROM answer_cache WHERE key = ?", (key,))
            self.conn.commit()
            return None
        self.conn.execute("UPDATE answer_cache SET hits = hits + 1 WHERE key = ?", (key,))
        self.conn.commit()
        return {"text": row["text"], "mode": row["mode"],
                "records": [r for r in (row["records"] or "").split(",") if r],
                "prompt_version": row["prompt_version"], "kb_version": row["kb_version"],
                "hits": int(row["hits"]) + 1}

    def cache_put(self, key: str, role: str, question: str, result: dict, ttl_seconds: int) -> None:
        stamp = datetime.now()
        self.conn.execute(
            "INSERT OR REPLACE INTO answer_cache (key, role, question, text, mode, records,"
            " prompt_version, kb_version, created_at, expires_at, hits)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (key, role, question, result["text"], result["mode"],
             ",".join(result.get("records") or []), result.get("prompt_version", ""),
             result.get("kb_version", ""), stamp.isoformat(timespec="seconds"),
             (stamp + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")))
        self.conn.commit()

    def cache_drop_stale(self, prompt_versions: dict[str, str], kb: str) -> int:
        """Выбрасывает записи чужих версий.

        Версия входит в ключ, поэтому старый кэш и так недостижим. Эта
        уборка нужна не для правильности, а чтобы база не росла мусором
        после каждой правки промпта.
        """
        removed = 0
        for role, version in prompt_versions.items():
            cur = self.conn.execute(
                "DELETE FROM answer_cache WHERE role = ? AND (prompt_version != ? OR kb_version != ?)",
                (role, version, kb))
            removed += cur.rowcount
        self.conn.commit()
        return removed

    # --- Сроки хранения и право на забвение --------------------------------

    def purge(self, days: int = DIALOG_TTL_DAYS) -> dict[str, int]:
        edge = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        dialogs = self.conn.execute(
            "SELECT id FROM dialog WHERE last_at < ?", (edge,)).fetchall()
        ids = [int(r["id"]) for r in dialogs]
        for dialog_id in ids:
            self.conn.execute("DELETE FROM message WHERE dialog_id = ?", (dialog_id,))
            self.conn.execute("DELETE FROM pii_token WHERE dialog_id = ?", (dialog_id,))
            self.conn.execute("DELETE FROM dialog WHERE id = ?", (dialog_id,))
        expired = self.conn.execute(
            "DELETE FROM answer_cache WHERE expires_at < ?", (now_iso(),)).rowcount
        self.conn.commit()
        return {"dialogs": len(ids), "cache": expired}

    def forget(self, contact_id: int) -> dict[str, int]:
        """Удаление по требованию человека: контакт, адреса, диалоги, значения ПДн."""
        dialogs = [int(r["id"]) for r in self.conn.execute(
            "SELECT id FROM dialog WHERE contact_id = ?", (contact_id,)).fetchall()]
        for dialog_id in dialogs:
            self.conn.execute("DELETE FROM message WHERE dialog_id = ?", (dialog_id,))
            self.conn.execute("DELETE FROM pii_token WHERE dialog_id = ?", (dialog_id,))
        self.conn.execute("DELETE FROM dialog WHERE contact_id = ?", (contact_id,))
        self.conn.execute("DELETE FROM channel_identity WHERE contact_id = ?", (contact_id,))
        self.conn.execute("DELETE FROM contact WHERE id = ?", (contact_id,))
        self.conn.commit()
        return {"dialogs": len(dialogs)}

    def stats(self) -> dict:
        def one(sql: str) -> int:
            return int(self.conn.execute(sql).fetchone()[0])
        return {
            "контактов": one("SELECT COUNT(*) FROM contact"),
            "адресов в каналах": one("SELECT COUNT(*) FROM channel_identity"),
            "диалогов": one("SELECT COUNT(*) FROM dialog"),
            "сообщений": one("SELECT COUNT(*) FROM message"),
            "значений ПДн": one("SELECT COUNT(*) FROM pii_token"),
            "записей кэша": one("SELECT COUNT(*) FROM answer_cache"),
            "попаданий в кэш": one("SELECT COALESCE(SUM(hits), 0) FROM answer_cache"),
        }


def cache_key(role: str, question: str, prompt_version: str, kb: str, marks: str) -> str:
    """Ключ кэша: вопрос, роль, версии и признаки контекста, меняющие ответ."""
    norm = re.sub(r"\s+", " ", question.lower().strip())
    norm = re.sub(r"[^\w\s]", "", norm)
    raw = "|".join((role, norm, prompt_version, kb, marks))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "stats"
    store = Store()
    if command == "stats":
        for key, value in store.stats().items():
            print(f"{key}: {value}")
    elif command == "purge":
        result = store.purge()
        print(f"Удалено диалогов старше {DIALOG_TTL_DAYS} дней: {result['dialogs']}; "
              f"просроченных записей кэша: {result['cache']}")
    elif command == "forget":
        if len(sys.argv) < 3:
            print("Укажите идентификатор контакта: python tools/store.py forget 7")
            return 1
        result = store.forget(int(sys.argv[2]))
        print(f"Контакт {sys.argv[2]} удалён вместе с диалогами: {result['dialogs']}")
    else:
        print("Команды: stats, purge, forget <contact_id>")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
