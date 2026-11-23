"""Shows the bot working WITHOUT Telegram: a scripted conversation plus the daily alert run, printed as a transcript.
Good for a demo when there is no internet, and for checking the wording.

    python scripts/demo_bot_offline.py [--lang en|ar] [--scenario mixed|severe|calm]
"""
from __future__ import annotations

import _path  # noqa: F401
import argparse
import json
import re
import tempfile
from datetime import date
from pathlib import Path

from app.config import get_settings
from app.forecast_store import ForecastStore
from bot.bot import BotApp
from bot.notifier import run_daily_check
from bot.subscribers import SubscriberDB
from bot.testing import FakeTelegramClient
from make_sample_forecasts import build_rows, write_csv

DEMO_DAY = date(2026, 11, 22)


def plain(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def show_reply(m: dict) -> None:
    for line in plain(m["text"]).splitlines():
        print(f"   BOT | {line}")
    if m.get("reply_markup"):
        for row in m["reply_markup"]["inline_keyboard"]:
            print("   BOT | [ " + " ] [ ".join(b["text"] for b in row) + " ]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lang", default="en", choices=["en", "ar"])
    ap.add_argument("--scenario", default="mixed", choices=["mixed", "severe", "calm"])
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp())
    thr = json.loads(get_settings().alert_config_path.read_text(encoding="utf-8"))
    write_csv(tmp / "forecasts.csv", build_rows(DEMO_DAY, a.scenario, thr))
    store = ForecastStore(tmp / "forecasts.csv", 1, lambda: DEMO_DAY)
    db, fake = SubscriberDB(tmp / "bot.db", "en"), FakeTelegramClient()
    app = BotApp(store, db, fake)
    code = {"en": "en", "ar": "ar"}[a.lang]
    chat = 5001

    def user(text=None, button=None):
        n = len(fake.sent)
        if button:
            print(f"\nUSER taps button: {button}")
            app.handle_update({"update_id": 1, "callback_query": {"id": "q", "from": {"id": chat, "language_code": code},
                                                                    "message": {"chat": {"id": chat, "type": "private"}}, "data": button}})
        else:
            print(f"\nUSER: {text}")
            app.handle_update({"update_id": 1, "message": {"chat": {"id": chat, "type": "private"}, "from": {"id": chat, "language_code": code}, "text": text}})
        for m in fake.sent[n:]:
            show_reply(m)

    print(f"=== Offline bot demo | forecast file of {DEMO_DAY} | scenario: {a.scenario} | language: {a.lang} ===")
    user("/start")
    user("/subscribe")
    user(button="sd:helwan")
    user(button="sl:helwan:high")
    user("/subscribe new cairo warning")
    user("/subscribe shobra warning")
    user("/status")
    user("/forecast helwan")
    user("/subscribe maadi")
    user("/subscribe helwan medium")
    user("/unsubscribe new cairo")

    print("\n\n=== Daily check (what the scheduler does after 08:00 Cairo time) ===")
    n = len(fake.sent)
    rep = run_daily_check(store, db, fake, pause=0)
    for m in fake.sent[n:]:
        print(f"\n-> message to chat {m['chat_id']}:")
        show_reply(m)
    print("\nReport:", rep.summary())
    n = len(fake.sent)
    rep2 = run_daily_check(store, db, fake, pause=0)
    print("Second run the same day:", rep2.summary(), "(no duplicate sent)" if len(fake.sent) == n else "")


if __name__ == "__main__":
    main()
