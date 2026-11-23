"""A fake Telegram client for tests and the offline demo: records every message instead of sending it."""
from __future__ import annotations

from .telegram_api import Forbidden, TelegramError


class FakeTelegramClient:
    def __init__(self):
        self.sent: list[dict] = []           # {"chat_id", "text", "reply_markup"}
        self.answers: list[tuple] = []       # (callback_query_id, text)
        self.fail: dict[int, Exception] = {}  # chat_id -> exception to raise on send (blocked user, server error ...)
        self.fail_once: dict[int, Exception] = {}

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML"):
        if chat_id in self.fail_once:
            raise self.fail_once.pop(chat_id)
        if chat_id in self.fail:
            raise self.fail[chat_id]
        self.sent.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})
        return {"message_id": len(self.sent)}

    def answer_callback_query(self, callback_query_id, text=None):
        self.answers.append((callback_query_id, text))
        return True

    # helpers for assertions
    def texts(self, chat_id=None) -> list[str]:
        return [m["text"] for m in self.sent if chat_id is None or m["chat_id"] == chat_id]

    def last(self, chat_id=None) -> dict:
        return [m for m in self.sent if chat_id is None or m["chat_id"] == chat_id][-1]


def blocked() -> Forbidden:
    return Forbidden("Forbidden: bot was blocked by the user", status=403)


def server_error() -> TelegramError:
    return TelegramError("Internal Server Error", status=500)
