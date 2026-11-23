"""One command to run everything:   python run.py

* the API on http://127.0.0.1:8000  (docs at /docs)
* the Telegram bot in the same process, if TELEGRAM_BOT_TOKEN is set (otherwise only the API runs)

Environment: see .env.example. Use --port / --host to change the address.
"""
from __future__ import annotations

import argparse
import logging
import threading

import uvicorn

from app.config import get_settings


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the forecast API and the Telegram bot.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = get_settings()
    if s.telegram_token:
        from bot.bot import build, run_polling
        app, scheduler = build(s)
        threading.Thread(target=run_polling, args=(app, scheduler), daemon=True, name="telegram-bot").start()
        print(f"Telegram bot started (daily alerts after {s.notify_hour_cairo}:00 Cairo time).")
    else:
        print("TELEGRAM_BOT_TOKEN is not set: running the API only (see docs/BOT_GUIDE.md to enable the bot).")
    uvicorn.run("app.main:app", host=a.host, port=a.port)


if __name__ == "__main__":
    main()
