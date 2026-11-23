from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.forecast_store import ForecastStore
from bot import notifier
from bot.bot import CAIRO, DailyScheduler, run_polling
from bot.subscribers import SubscriberDB
from bot.telegram_api import TelegramError
from bot.testing import FakeTelegramClient
from conftest import TODAY, scenario_rows, write_rows

import threading


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)


def cairo(h, m=0, day=22):
    return datetime(2026, 11, day, h, m, tzinfo=CAIRO)


def make(store, now):
    db, fake = SubscriberDB(":memory:", "en"), FakeTelegramClient()
    db.add_subscription(1, "helwan", "high")
    clock = {"t": now}
    return DailyScheduler(store, db, fake, 8, now_fn=lambda: clock["t"]), db, fake, clock


def test_cairo_timezone_is_available_and_offset_is_sane():
    off = datetime(2026, 1, 15, 12, tzinfo=CAIRO).utcoffset()
    assert off == timedelta(hours=2)


def test_nothing_before_the_notify_hour(store):
    s, db, fake, clock = make(store, cairo(7, 59))
    assert s.tick() is None and fake.sent == []


def test_runs_once_per_day_after_the_hour(store):
    s, db, fake, clock = make(store, cairo(8, 0))
    assert s.tick().sent == 1
    clock["t"] = cairo(9, 0)
    assert s.tick() is None and len(fake.sent) == 1
    assert db.get_meta("last_notify_date") == "2026-11-22"


def test_stale_file_is_retried_every_30_minutes_until_fresh(tmp_path):
    p = write_rows(tmp_path / "f.csv", scenario_rows("mixed"))
    store = ForecastStore(p, 1, lambda: datetime(2026, 11, 25).date())      # 3 days old -> stale
    s, db, fake, clock = make(store, cairo(8, 0, 25))
    r = s.tick(); assert r.stale and fake.sent == [] and db.get_meta("last_notify_date") is None
    clock["t"] = cairo(8, 10, 25); assert s.tick() is None                  # too soon to retry
    store._today = lambda: TODAY                                            # the pipeline has now produced a fresh file
    clock["t"] = cairo(8, 31, 25); r = s.tick()
    assert r is not None and r.ok and len(fake.sent) == 1 and db.get_meta("last_notify_date") == "2026-11-25"


def test_next_day_runs_again(store):
    s, db, fake, clock = make(store, cairo(8, 5))
    s.tick()
    db.mark_sent  # noqa: B018
    clock["t"] = cairo(8, 5, 23)
    assert s.tick() is not None


class _Poller:
    """Fake client for the polling loop: serves scripted updates, then stops the loop."""
    def __init__(self, batches, stop):
        self.batches, self.stop, self.calls = list(batches), stop, []

    def get_updates(self, offset=None, timeout=30):
        self.calls.append(offset)
        if not self.batches:
            self.stop.set()
            return []
        b = self.batches.pop(0)
        if isinstance(b, Exception):
            raise b
        return b

    def send_message(self, *a, **k):
        return {}


def test_polling_loop_advances_offset_survives_errors_and_bad_updates(store):
    from bot.bot import BotApp
    stop = threading.Event()
    seen = []

    class App(BotApp):
        def handle_update(self, u):
            seen.append(u["update_id"])
            if u["update_id"] == 11:
                raise RuntimeError("boom")             # one bad update must not stop the bot

    client = _Poller([[{"update_id": 10}, {"update_id": 11}], TelegramError("network"), [{"update_id": 12}]], stop)
    app = App(store, SubscriberDB(":memory:", "en"), client)
    stop.wait = lambda t=None: False                    # no real waiting after the simulated network error
    run_polling(app, None, stop)
    assert seen == [10, 11, 12] and client.calls[:2] == [None, 12] and client.calls[2] == 12


def test_scheduler_exception_does_not_kill_the_loop(store):
    from bot.bot import BotApp
    stop = threading.Event()

    class Boom:
        def tick(self):
            raise RuntimeError("scheduler failed")

    app = BotApp(store, SubscriberDB(":memory:", "en"), _Poller([[]], stop))
    run_polling(app, Boom(), stop)                      # returns normally
