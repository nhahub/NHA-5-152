import pytest
import requests

from bot.telegram_api import Forbidden, TelegramClient, TelegramError

TOKEN = "123456:SECRET-TOKEN"


class Resp:
    def __init__(self, data, status=200, bad_json=False):
        self._d, self.status_code, self._bad = data, status, bad_json

    def json(self):
        if self._bad:
            raise ValueError("not json")
        return self._d


class Session:
    def __init__(self, resp=None, exc=None):
        self.resp, self.exc, self.calls = resp, exc, []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json, timeout))
        if self.exc:
            raise self.exc
        return self.resp


def client(resp=None, exc=None):
    s = Session(resp, exc)
    return TelegramClient(TOKEN, session=s), s


def test_token_is_required():
    with pytest.raises(ValueError):
        TelegramClient("")


def test_send_message_payload_and_url():
    c, s = client(Resp({"ok": True, "result": {"message_id": 5}}))
    assert c.send_message(42, "hi <b>x</b>", reply_markup={"inline_keyboard": []}) == {"message_id": 5}
    url, payload, _ = s.calls[0]
    assert url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert payload["chat_id"] == 42 and payload["parse_mode"] == "HTML" and payload["reply_markup"] == {"inline_keyboard": []}


def test_get_updates_uses_long_polling_timeout_and_offset():
    c, s = client(Resp({"ok": True, "result": [{"update_id": 1}]}))
    assert c.get_updates(offset=7, timeout=30) == [{"update_id": 1}]
    _, payload, http_timeout = s.calls[0]
    assert payload["offset"] == 7 and payload["timeout"] == 30 and http_timeout > 30 and "callback_query" in payload["allowed_updates"]


def test_blocked_user_raises_forbidden():
    c, _ = client(Resp({"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}, 403))
    with pytest.raises(Forbidden):
        c.send_message(1, "x")


def test_chat_not_found_is_forbidden_too():
    c, _ = client(Resp({"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}, 400))
    with pytest.raises(Forbidden):
        c.send_message(1, "x")


def test_flood_control_exposes_retry_after():
    c, _ = client(Resp({"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 7}}, 429))
    with pytest.raises(TelegramError) as e:
        c.send_message(1, "x")
    assert e.value.retry_after == 7 and not isinstance(e.value, Forbidden)


def test_other_api_errors():
    c, _ = client(Resp({"ok": False, "error_code": 400, "description": "Bad Request: can't parse entities"}, 400))
    with pytest.raises(TelegramError) as e:
        c.send_message(1, "<x")
    assert "parse entities" in e.value.description and e.value.status == 400


def test_network_errors_become_telegram_errors_without_leaking_the_token():
    c, _ = client(exc=requests.ConnectionError(f"HTTPSConnectionPool: /bot{TOKEN}/sendMessage failed"))
    with pytest.raises(TelegramError) as e:
        c.send_message(1, "x")
    assert TOKEN not in str(e.value) and "SECRET" not in str(e.value)


def test_non_json_response():
    c, _ = client(Resp(None, 502, bad_json=True))
    with pytest.raises(TelegramError) as e:
        c.send_message(1, "x")
    assert e.value.status == 502 and TOKEN not in str(e.value)


def test_answer_callback_query():
    c, s = client(Resp({"ok": True, "result": True}))
    assert c.answer_callback_query("abc", "Done") is True
    assert s.calls[0][1] == {"callback_query_id": "abc", "text": "Done"}
