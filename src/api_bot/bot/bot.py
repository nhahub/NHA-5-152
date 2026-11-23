"""The Telegram bot: commands, inline buttons and the polling loop (with the daily alert check built in).

Run:  python -m bot.bot            (needs TELEGRAM_BOT_TOKEN; see docs/BOT_GUIDE.md)

BotApp.handle_update() holds all the behaviour and takes any client object with send_message(); the tests drive it with a fake client,
so the logic is verified without touching Telegram.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.forecast_store import ForecastError, ForecastStore
from app.stations import ORDER, display_name, resolve_station
from .messages import LANGS, fmt_date, forecast_line, level_label, t
from .notifier import run_daily_check
from .subscribers import SubscriberDB
from .telegram_api import Forbidden, TelegramClient, TelegramError

log = logging.getLogger("bot")
CAIRO = ZoneInfo("Africa/Cairo")

LEVEL_WORDS = {"warning": "warning", "warn": "warning", "تحذير": "warning", "high": "high", "خطر": "high", "مرتفع": "high", "عالي": "high"}
ALL_WORDS = {"all", "الكل", "كل", "كلها", "كل الاحياء", "كل الأحياء"}
LANG_WORDS = {"ar": "ar", "arabic": "ar", "عربي": "ar", "العربية": "ar", "en": "en", "english": "en", "انجليزي": "en", "إنجليزي": "en"}


class BotApp:
    def __init__(self, store: ForecastStore, db: SubscriberDB, client):
        self.store, self.db, self.client = store, db, client

    # ------------------------------------------------------------------------------------------------ plumbing
    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._on_callback(update["callback_query"])
        elif "message" in update:
            self._on_message(update["message"])

    def _send(self, chat_id: int, text: str, markup: dict | None = None) -> None:
        try:
            self.client.send_message(chat_id, text, reply_markup=markup)
        except Forbidden:
            self.db.deactivate_user(chat_id)
        except TelegramError as e:
            log.warning("send failed for chat %s: %s", chat_id, e.description)

    def _lang(self, chat_id: int) -> str:
        return self.db.get_lang(chat_id)

    def _items(self, lang: str) -> str:
        return "\n".join(f"• {display_name(s, lang)} - <code>{s}</code>" for s in ORDER)

    def _register(self, chat_id: int, sender: dict | None) -> None:
        """First contact: pick the language from the Telegram app language (ar -> Arabic, en -> English, else the default)."""
        if self.db.has_user(chat_id):
            self.db.ensure_user(chat_id)
            return
        self.db.ensure_user(chat_id)
        code = ((sender or {}).get("language_code") or "").lower()
        if code.startswith("ar"):
            self.db.set_lang(chat_id, "ar")
        elif code.startswith("en"):
            self.db.set_lang(chat_id, "en")

    # ------------------------------------------------------------------------------------------------ messages
    def _on_message(self, msg: dict) -> None:
        chat = msg.get("chat") or {}
        if chat.get("type") != "private":
            return                                              # alerts are personal: private chats only
        chat_id = chat["id"]
        text = (msg.get("text") or "").strip()
        self._register(chat_id, msg.get("from"))
        lang = self._lang(chat_id)
        if not text.startswith("/"):
            if text:
                self._send(chat_id, t(lang, "text_hint"))
            return
        head, _, rest = text.partition(" ")
        cmd, args = head[1:].split("@")[0].lower(), rest.strip()
        handler = {"start": self._cmd_start, "help": self._cmd_help, "districts": self._cmd_districts, "subscribe": self._cmd_subscribe,
                   "unsubscribe": self._cmd_unsubscribe, "stop": self._cmd_unsubscribe, "status": self._cmd_status,
                   "forecast": self._cmd_forecast, "lang": self._cmd_lang}.get(cmd)
        if handler is None:
            self._send(chat_id, t(lang, "unknown_command"))
        else:
            handler(chat_id, lang, args)

    def _cmd_start(self, chat_id, lang, args):
        self._send(chat_id, t(lang, "welcome"))

    def _cmd_help(self, chat_id, lang, args):
        self._send(chat_id, t(lang, "help"))

    def _cmd_districts(self, chat_id, lang, args):
        self._send(chat_id, t(lang, "districts", items=self._items(lang), example=ORDER[1]))

    def _cmd_lang(self, chat_id, lang, args):
        new = LANG_WORDS.get(args.lower())
        if new is None:
            self._send(chat_id, t(lang, "lang_usage"))
            return
        self.db.set_lang(chat_id, new)
        self._send(chat_id, t(new, "lang_set"))

    # -- subscribe -------------------------------------------------------------------------------------------
    def _district_keyboard(self, lang: str) -> dict:
        buttons = [{"text": display_name(s, lang), "callback_data": f"sd:{s}"} for s in ORDER]
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        rows.append([{"text": t(lang, "btn_all"), "callback_data": "sd:all"}])
        return {"inline_keyboard": rows}

    def _level_keyboard(self, lang: str, target: str) -> dict:
        return {"inline_keyboard": [[{"text": level_label(lang, "warning"), "callback_data": f"sl:{target}:warning"},
                                     {"text": level_label(lang, "high"), "callback_data": f"sl:{target}:high"}]]}

    def _target_name(self, lang: str, target: str) -> str:
        return t(lang, "btn_all") if target == "all" else display_name(target, lang)

    def _apply_subscription(self, chat_id: int, lang: str, target: str, level: str) -> None:
        if target == "all":
            for s in ORDER:
                self.db.add_subscription(chat_id, s, level)
            self._send(chat_id, t(lang, "subscribed_all", level=level_label(lang, level)))
            return
        outcome = self.db.add_subscription(chat_id, target, level)
        key = "updated" if outcome == "updated" else "subscribed"
        self._send(chat_id, t(lang, key, district=display_name(target, lang), level=level_label(lang, level)))

    def _cmd_subscribe(self, chat_id, lang, args):
        if not args:
            self._send(chat_id, t(lang, "choose_district"), self._district_keyboard(lang))
            return
        tokens = args.split()
        level = LEVEL_WORDS.get(tokens[-1].lower()) if len(tokens) > 1 or tokens[-1].lower() in LEVEL_WORDS else None
        if level:
            tokens = tokens[:-1]
        text = " ".join(tokens)
        if not text:                                            # only a level was typed
            self._send(chat_id, t(lang, "choose_district"), self._district_keyboard(lang))
            return
        target = "all" if text.lower() in ALL_WORDS else resolve_station(text)
        if target is None:
            if len(tokens) >= 2 and resolve_station(" ".join(tokens[:-1])) is not None and level is None:
                self._send(chat_id, t(lang, "bad_level"))      # e.g. "/subscribe helwan medium"
            else:
                self._send(chat_id, t(lang, "unknown_district", text=escape(text), items=self._items(lang)))
            return
        if level is None:
            level = "warning"
        self._apply_subscription(chat_id, lang, target, level)

    # -- unsubscribe / status / forecast ---------------------------------------------------------------------
    def _subs_text(self, chat_id: int, lang: str) -> str:
        return "\n".join(t(lang, "status_line", district=display_name(d, lang), level=level_label(lang, lv)) for d, lv in self.db.list_subscriptions(chat_id))

    def _cmd_unsubscribe(self, chat_id, lang, args):
        subs = self.db.list_subscriptions(chat_id)
        if not args:
            self._send(chat_id, t(lang, "unsub_usage", items=self._subs_text(chat_id, lang)) if subs else t(lang, "unsub_empty"))
            return
        if args.lower() in ALL_WORDS:
            removed = self.db.remove_subscription(chat_id)
            self._send(chat_id, t(lang, "unsub_all_done") if removed else t(lang, "unsub_empty"))
            return
        target = resolve_station(args)
        if target is None:
            self._send(chat_id, t(lang, "unknown_district", text=escape(args), items=self._items(lang)))
        elif self.db.remove_subscription(chat_id, target):
            self._send(chat_id, t(lang, "unsub_done", district=display_name(target, lang)))
        else:
            self._send(chat_id, t(lang, "unsub_none", district=display_name(target, lang)))

    def _cmd_status(self, chat_id, lang, args):
        subs = self.db.list_subscriptions(chat_id)
        if not subs:
            self._send(chat_id, t(lang, "status_empty"))
        else:
            self._send(chat_id, t(lang, "status_header") + "\n" + self._subs_text(chat_id, lang))

    def _cmd_forecast(self, chat_id, lang, args):
        sid = resolve_station(args) if args else None
        if sid is None:
            self._send(chat_id, t(lang, "forecast_usage", items=self._items(lang)))
            return
        try:
            rows = self.store.select(station=sid)
            age = self.store.age_days()
            stale = self.store.is_stale()
        except ForecastError:
            self._send(chat_id, t(lang, "no_data"))
            return
        if not rows:
            self._send(chat_id, t(lang, "no_data"))
            return
        lines = [t(lang, "forecast_header", district=display_name(sid, lang), date=fmt_date(lang, rows[0].issue_date))]
        lines += [forecast_line(lang, r) for r in rows]
        if stale:
            lines.append(t(lang, "stale", age=age))
        self._send(chat_id, "\n".join(lines))

    # ------------------------------------------------------------------------------------------------ buttons
    def _on_callback(self, cq: dict) -> None:
        cq_id = cq.get("id")
        chat_id = ((cq.get("message") or {}).get("chat") or {}).get("id") or (cq.get("from") or {}).get("id")
        if chat_id is None:
            return
        self._register(chat_id, cq.get("from"))
        lang = self._lang(chat_id)
        parts = str(cq.get("data", "")).split(":")
        ok = False
        if len(parts) == 2 and parts[0] == "sd" and (parts[1] == "all" or parts[1] in ORDER):
            ok = True
            self._send(chat_id, t(lang, "choose_level", district=self._target_name(lang, parts[1])), self._level_keyboard(lang, parts[1]))
        elif len(parts) == 3 and parts[0] == "sl" and (parts[1] == "all" or parts[1] in ORDER) and parts[2] in ("warning", "high"):
            ok = True
            self._apply_subscription(chat_id, lang, parts[1], parts[2])
        try:
            self.client.answer_callback_query(cq_id, t(lang, "ack_done" if ok else "ack_error"))
        except TelegramError:
            pass


class DailyScheduler:
    """Runs the daily alert check from inside the polling loop: after NOTIFY_HOUR_CAIRO, once per Cairo day, retrying every 30 min
    while the forecast file is stale (e.g. the pipeline has not run yet)."""

    def __init__(self, store, db, client, notify_hour: int, now_fn=None, retry_seconds: int = 1800):
        self.store, self.db, self.client, self.hour = store, db, client, notify_hour
        self._now = now_fn or (lambda: datetime.now(CAIRO))
        self._retry, self._last_attempt = retry_seconds, None

    def tick(self):
        now = self._now()
        today = now.date().isoformat()
        if now.hour < self.hour or self.db.get_meta("last_notify_date") == today:
            return None
        if self._last_attempt is not None and (now - self._last_attempt).total_seconds() < self._retry:
            return None
        self._last_attempt = now
        rep = run_daily_check(self.store, self.db, self.client)
        log.info("daily check: %s", rep.summary())
        if rep.ok:
            self.db.set_meta("last_notify_date", today)
        return rep


def run_polling(app: BotApp, scheduler: DailyScheduler | None = None, stop: threading.Event | None = None, poll_timeout: int = 30) -> None:
    stop = stop or threading.Event()
    offset, backoff = None, 1
    while not stop.is_set():
        if scheduler:
            try:
                scheduler.tick()
            except Exception:                                    # never let the scheduler kill the bot
                log.exception("scheduler error")
        try:
            updates = app.client.get_updates(offset, poll_timeout)
            backoff = 1
        except TelegramError as e:
            log.warning("getUpdates failed: %s (retry in %ss)", e.description, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, 60)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                app.handle_update(u)
            except Exception:
                log.exception("error while handling update %s", u.get("update_id"))


def build(settings: Settings | None = None) -> tuple[BotApp, DailyScheduler]:
    s = settings or get_settings()
    if not s.telegram_token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set. Create a bot with @BotFather and set the token (see docs/BOT_GUIDE.md).")
    store = ForecastStore(s.forecasts_path, s.max_age_days)
    db = SubscriberDB(s.bot_db_path, s.default_lang)
    client = TelegramClient(s.telegram_token)
    return BotApp(store, db, client), DailyScheduler(store, db, client, s.notify_hour_cairo)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app, scheduler = build()
    try:
        me = app.client.get_me()
    except TelegramError as e:
        print(f"Cannot reach Telegram or the token is wrong: {e.description}")
        return 1
    print(f"Bot @{me.get('username')} is running. Daily alerts are sent after {scheduler.hour}:00 Cairo time. Press Ctrl+C to stop.")
    try:
        run_polling(app, scheduler)
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
