"""The daily check: reads the forecasts file and messages the subscribers whose level is reached.

Rules
* A "warning" subscriber gets warning + high alerts, a "high" subscriber only high alerts.
* One message per user per forecast day (all their districts and horizons together).
* The same forecast is never sent twice. If the pipeline re-runs the same day and the alerts change, an
  "updated forecast" message is sent once.
* A stale forecast file (older than MAX_AGE_DAYS) is never sent as news.
* If Telegram refuses a message the user is handled (blocked -> removed); other errors are retried on the next run.

Run once (cron / GitHub Actions / by hand):   python -m bot.notifier            (add --dry-run to see what would be sent)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date

from app.config import get_settings
from app.forecast_store import LEVEL_RANK, Forecast, ForecastError, ForecastStore
from .messages import build_alert_message
from .subscribers import SubscriberDB
from .telegram_api import Forbidden, TelegramClient, TelegramError

log = logging.getLogger("notifier")


@dataclass
class NotifyReport:
    issue_date: date | None = None
    stale: bool = False
    error: str | None = None
    sent: int = 0
    updated: int = 0
    no_alert: int = 0
    already_sent: int = 0
    failed: int = 0
    blocked: int = 0
    preview: list[tuple[int, str]] = field(default_factory=list)      # (chat_id, text) in dry-run mode

    @property
    def ok(self) -> bool:
        return self.error is None and not self.stale

    def summary(self) -> str:
        if self.error:
            return f"NOT SENT - forecasts unusable: {self.error}"
        if self.stale:
            return f"NOT SENT - forecast of {self.issue_date} is stale"
        return (f"forecast {self.issue_date}: sent {self.sent} (updates {self.updated}), nobody to alert {self.no_alert}, "
                f"already sent {self.already_sent}, failed {self.failed}, blocked {self.blocked}")


def _hash(blocks: dict[str, list[Forecast]]) -> str:
    payload = {s: [(r.horizon, r.alert_level, r.aqi_category, round(r.pred_pm25, 1), round(r.risk_prob, 3)) for r in rows] for s, rows in sorted(blocks.items())}
    return hashlib.sha1(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def run_daily_check(store: ForecastStore, db: SubscriberDB, client: TelegramClient | None, dry_run: bool = False, pause: float = 0.05) -> NotifyReport:
    rep = NotifyReport()
    try:
        rows = store.latest()
    except ForecastError as e:
        rep.error = f"{e.kind}: {e.message}"
        log.error("forecasts unusable: %s", rep.error)
        return rep
    rep.issue_date = rows[0].issue_date
    if store.is_stale():
        rep.stale = True
        log.warning("forecast of %s is %s day(s) old (max %s): nothing sent", rep.issue_date, store.age_days(), store.max_age_days)
        return rep
    if client is None and not dry_run:
        raise ValueError("a TelegramClient is required unless dry_run=True")

    by_station: dict[str, list[Forecast]] = {}
    for r in rows:
        by_station.setdefault(r.station, []).append(r)

    for chat_id, info in db.active_subscribers().items():
        blocks = {}
        for district, level in info["subs"].items():
            hit = [r for r in by_station.get(district, []) if LEVEL_RANK[r.alert_level] >= LEVEL_RANK[level]]
            if hit:
                blocks[district] = hit
        if not blocks:
            rep.no_alert += 1
            continue
        digest = _hash(blocks)
        previous = db.sent_hash(chat_id, rep.issue_date)
        if previous == digest:
            rep.already_sent += 1
            continue
        text = build_alert_message(info["lang"], rep.issue_date, blocks, updated=previous is not None)
        if dry_run:
            rep.preview.append((chat_id, text))
            rep.sent += 1
            continue
        try:
            client.send_message(chat_id, text)
        except Forbidden:
            db.deactivate_user(chat_id)
            rep.blocked += 1
            continue
        except TelegramError as e:
            if e.retry_after:                                   # Telegram flood control: wait once, then try again
                time.sleep(min(e.retry_after, 30))
                try:
                    client.send_message(chat_id, text)
                except TelegramError:
                    rep.failed += 1
                    continue
            else:
                log.warning("could not send to %s: %s", chat_id, e.description)
                rep.failed += 1
                continue
        db.mark_sent(chat_id, rep.issue_date, digest)
        rep.sent += 1
        rep.updated += previous is not None
        time.sleep(pause)                                       # stay far below Telegram's 30 messages/second limit
    log.info(rep.summary())
    return rep


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Send the daily air-quality alerts to Telegram subscribers.")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent, send nothing")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = get_settings()
    store = ForecastStore(s.forecasts_path, s.max_age_days)
    db = SubscriberDB(s.bot_db_path, s.default_lang)
    client = None if args.dry_run else TelegramClient(s.telegram_token or "")
    rep = run_daily_check(store, db, client, dry_run=args.dry_run)
    for chat_id, text in rep.preview:
        print(f"--- would send to chat {chat_id} ---\n{text}\n")
    print(rep.summary())
    return 0 if rep.ok else 2


if __name__ == "__main__":
    sys.exit(main())
