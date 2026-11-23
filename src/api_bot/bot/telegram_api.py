"""Thin Telegram Bot API client (plain HTTPS via `requests`; no framework, so it runs anywhere).

The token is only used to build the URL and is never printed or put into error messages.
"""
from __future__ import annotations

import requests

API = "https://api.telegram.org"


class TelegramError(Exception):
    def __init__(self, description: str, status: int | None = None, retry_after: int | None = None):
        super().__init__(description)
        self.description = description
        self.status = status
        self.retry_after = retry_after


class Forbidden(TelegramError):
    """The user blocked the bot / deleted the chat: stop sending to this chat."""


class TelegramClient:
    def __init__(self, token: str, session=None, base_url: str = API, timeout: int = 15):
        if not token:
            raise ValueError("a Telegram bot token is required (set TELEGRAM_BOT_TOKEN)")
        self._token = token
        self._s = session or requests.Session()
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def _call(self, method: str, payload: dict | None = None, timeout: int | None = None) -> dict | list | bool:
        try:
            r = self._s.post(f"{self._base}/bot{self._token}/{method}", json=payload or {}, timeout=timeout or self._timeout)
        except requests.RequestException as e:
            raise TelegramError(f"network error calling {method}: {type(e).__name__}") from None
        try:
            data = r.json()
        except ValueError:
            raise TelegramError(f"{method}: invalid response (HTTP {r.status_code})", status=r.status_code) from None
        if data.get("ok"):
            return data.get("result")
        desc = str(data.get("description", "unknown error"))
        retry = (data.get("parameters") or {}).get("retry_after")
        code = data.get("error_code", r.status_code)
        if code == 403 or "chat not found" in desc.lower() or "user is deactivated" in desc.lower():
            raise Forbidden(desc, status=code)
        raise TelegramError(desc, status=code, retry_after=retry)

    def get_me(self) -> dict:
        return self._call("getMe")

    def get_updates(self, offset: int | None = None, timeout: int = 30) -> list[dict]:
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return self._call("getUpdates", payload, timeout=timeout + 10)

    def send_message(self, chat_id: int | str, text: str, reply_markup: dict | None = None, parse_mode: str | None = "HTML") -> dict:
        payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._call("sendMessage", payload)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> bool:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        return self._call("answerCallbackQuery", payload)
