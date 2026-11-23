"""SQLite store for Telegram users, their subscriptions and the notifications already sent (to avoid duplicates)."""
from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY, lang TEXT NOT NULL DEFAULT 'ar', active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS subscriptions (
    chat_id INTEGER NOT NULL, district TEXT NOT NULL, level TEXT NOT NULL CHECK (level IN ('warning','high')),
    created_at TEXT NOT NULL, PRIMARY KEY (chat_id, district));
CREATE TABLE IF NOT EXISTS notifications (
    chat_id INTEGER NOT NULL, issue_date TEXT NOT NULL, payload_hash TEXT NOT NULL, sent_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, issue_date));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SubscriberDB:
    def __init__(self, path: Path | str = ":memory:", default_lang: str = "ar"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.default_lang = default_lang
        with self._lock:
            self._db.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()):
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    # -- users -----------------------------------------------------------------------------------------------
    def ensure_user(self, chat_id: int) -> None:
        """Create the user if new; re-activate a user who came back after blocking the bot."""
        self._exec("INSERT INTO users (chat_id, lang, active, created_at) VALUES (?, ?, 1, ?) "
                   "ON CONFLICT(chat_id) DO UPDATE SET active = 1", (chat_id, self.default_lang, _now()))

    def has_user(self, chat_id: int) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM users WHERE chat_id = ?", (chat_id,)).fetchone() is not None

    def get_lang(self, chat_id: int) -> str:
        with self._lock:
            row = self._db.execute("SELECT lang FROM users WHERE chat_id = ?", (chat_id,)).fetchone()
        return row["lang"] if row else self.default_lang

    def set_lang(self, chat_id: int, lang: str) -> None:
        self.ensure_user(chat_id)
        self._exec("UPDATE users SET lang = ? WHERE chat_id = ?", (lang, chat_id))

    def deactivate_user(self, chat_id: int) -> None:
        """The user blocked the bot: keep the record but stop sending (subscriptions are removed)."""
        self._exec("UPDATE users SET active = 0 WHERE chat_id = ?", (chat_id,))
        self._exec("DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,))

    # -- subscriptions ---------------------------------------------------------------------------------------
    def add_subscription(self, chat_id: int, district: str, level: str) -> str:
        """Returns 'added' or 'updated' (the level of an existing subscription changed)."""
        self.ensure_user(chat_id)
        with self._lock:
            row = self._db.execute("SELECT level FROM subscriptions WHERE chat_id = ? AND district = ?", (chat_id, district)).fetchone()
            self._db.execute("INSERT INTO subscriptions (chat_id, district, level, created_at) VALUES (?, ?, ?, ?) "
                             "ON CONFLICT(chat_id, district) DO UPDATE SET level = excluded.level", (chat_id, district, level, _now()))
            self._db.commit()
        return "updated" if row else "added"

    def remove_subscription(self, chat_id: int, district: str | None = None) -> int:
        """district=None removes all. Returns how many rows were removed."""
        if district is None:
            return self._exec("DELETE FROM subscriptions WHERE chat_id = ?", (chat_id,)).rowcount
        return self._exec("DELETE FROM subscriptions WHERE chat_id = ? AND district = ?", (chat_id, district)).rowcount

    def list_subscriptions(self, chat_id: int) -> list[tuple[str, str]]:
        with self._lock:
            rows = self._db.execute("SELECT district, level FROM subscriptions WHERE chat_id = ? ORDER BY district", (chat_id,)).fetchall()
        return [(r["district"], r["level"]) for r in rows]

    def active_subscribers(self) -> dict[int, dict]:
        """chat_id -> {'lang': ..., 'subs': {district: level}} for active users with at least one subscription."""
        with self._lock:
            rows = self._db.execute("SELECT s.chat_id, s.district, s.level, u.lang FROM subscriptions s "
                                    "JOIN users u USING (chat_id) WHERE u.active = 1 ORDER BY s.chat_id, s.district").fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            out.setdefault(r["chat_id"], {"lang": r["lang"], "subs": {}})["subs"][r["district"]] = r["level"]
        return out

    def counts(self) -> dict:
        with self._lock:
            u = self._db.execute("SELECT COUNT(*) FROM users WHERE active = 1").fetchone()[0]
            s = self._db.execute("SELECT COUNT(*) FROM subscriptions").fetchone()[0]
        return {"active_users": u, "subscriptions": s}

    # -- notification log ------------------------------------------------------------------------------------
    def sent_hash(self, chat_id: int, issue_date: date) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT payload_hash FROM notifications WHERE chat_id = ? AND issue_date = ?",
                                   (chat_id, issue_date.isoformat())).fetchone()
        return row["payload_hash"] if row else None

    def mark_sent(self, chat_id: int, issue_date: date, payload_hash: str) -> None:
        self._exec("INSERT INTO notifications (chat_id, issue_date, payload_hash, sent_at) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT(chat_id, issue_date) DO UPDATE SET payload_hash = excluded.payload_hash, sent_at = excluded.sent_at",
                   (chat_id, issue_date.isoformat(), payload_hash, _now()))

    # -- small key/value store (scheduler state) -------------------------------------------------------------
    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self._exec("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

    def close(self) -> None:
        with self._lock:
            self._db.close()
