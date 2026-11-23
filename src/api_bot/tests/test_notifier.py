import os
from datetime import date

import pytest

from app.forecast_store import ForecastStore
from bot import notifier
from bot.notifier import run_daily_check
from bot.subscribers import SubscriberDB
from bot.telegram_api import TelegramError
from bot.testing import FakeTelegramClient, blocked, server_error
from conftest import TODAY, scenario_rows, write_rows


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(notifier.time, "sleep", lambda s: None)


@pytest.fixture
def db():
    return SubscriberDB(":memory:", "en")


@pytest.fixture
def fake():
    return FakeTelegramClient()


def test_warning_subscriber_gets_warning_and_high(store, db, fake):
    db.add_subscription(1, "helwan", "warning")
    rep = run_daily_check(store, db, fake)
    assert rep.ok and rep.sent == 1 and len(fake.sent) == 1
    text = fake.sent[0]["text"]
    assert "Helwan" in text and "High risk" in text and "Warning" in text


def test_high_only_subscriber_gets_only_high(store, db, fake):
    db.add_subscription(1, "helwan", "high")
    run_daily_check(store, db, fake)
    text = fake.sent[0]["text"]
    assert "High risk" in text and "⚠️ Warning" not in text.split("\n", 2)[2]


def test_high_only_subscriber_with_no_high_gets_nothing(store, db, fake):
    db.add_subscription(1, "shobra", "high")            # shobra has warnings but no high
    rep = run_daily_check(store, db, fake)
    assert fake.sent == [] and rep.no_alert == 1 and rep.sent == 0


def test_district_without_alert_sends_nothing(store, db, fake):
    db.add_subscription(1, "october", "warning")
    rep = run_daily_check(store, db, fake)
    assert fake.sent == [] and rep.no_alert == 1


def test_all_districts_in_one_message_per_user(store, db, fake):
    for d in ("helwan", "shobra", "downtown", "october"):
        db.add_subscription(1, d, "warning")
    rep = run_daily_check(store, db, fake)
    assert rep.sent == 1 and len(fake.sent) == 1
    t = fake.sent[0]["text"]
    assert all(n in t for n in ("Helwan", "Shobra", "Downtown")) and "6th of October" not in t


def test_each_user_gets_own_message_in_own_language(store, db, fake):
    db.add_subscription(1, "helwan", "high"); db.set_lang(1, "en")
    db.add_subscription(2, "helwan", "high"); db.set_lang(2, "ar")
    run_daily_check(store, db, fake)
    assert "Air-quality alert" in fake.texts(1)[0] and "تنبيه جودة الهواء" in fake.texts(2)[0] and "حلوان" in fake.texts(2)[0]


def test_same_forecast_is_never_sent_twice(store, db, fake):
    db.add_subscription(1, "helwan", "warning")
    run_daily_check(store, db, fake)
    rep2 = run_daily_check(store, db, fake)
    assert len(fake.sent) == 1 and rep2.already_sent == 1 and rep2.sent == 0


def test_changed_forecast_same_day_sends_one_update(tmp_path, db, fake):
    p = write_rows(tmp_path / "f.csv", scenario_rows("mixed"))
    store = ForecastStore(p, 1, lambda: TODAY)
    db.add_subscription(1, "helwan", "warning")
    run_daily_check(store, db, fake)
    write_rows(p, scenario_rows("severe"))
    st = p.stat(); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    rep = run_daily_check(store, db, fake)
    assert rep.sent == 1 and rep.updated == 1 and "Updated forecast" in fake.sent[-1]["text"]
    assert run_daily_check(store, db, fake).already_sent == 1           # and not again


def test_new_day_sends_again(tmp_path, db, fake):
    p = write_rows(tmp_path / "f.csv", scenario_rows("mixed", date(2026, 11, 21)))
    store = ForecastStore(p, 1, lambda: date(2026, 11, 21))
    db.add_subscription(1, "helwan", "warning")
    run_daily_check(store, db, fake)
    write_rows(p, scenario_rows("mixed", date(2026, 11, 22)))
    st = p.stat(); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    store2 = ForecastStore(p, 1, lambda: TODAY)
    run_daily_check(store2, db, fake)
    assert len(fake.sent) == 2


def test_stale_forecast_sends_nothing(mixed_csv, db, fake):
    db.add_subscription(1, "helwan", "warning")
    rep = run_daily_check(ForecastStore(mixed_csv, 1, lambda: date(2026, 11, 26)), db, fake)
    assert rep.stale and not rep.ok and fake.sent == [] and "stale" in rep.summary()


def test_missing_or_invalid_file_sends_nothing(tmp_path, db, fake):
    db.add_subscription(1, "helwan", "warning")
    rep = run_daily_check(ForecastStore(tmp_path / "none.csv", 1, lambda: TODAY), db, fake)
    assert rep.error and fake.sent == [] and not rep.ok
    bad = tmp_path / "bad.csv"; bad.write_text("a,b\n1,2\n")
    assert run_daily_check(ForecastStore(bad, 1, lambda: TODAY), db, fake).error


def test_blocked_user_is_removed_and_others_still_served(store, db, fake):
    db.add_subscription(1, "helwan", "warning"); db.add_subscription(2, "helwan", "warning")
    fake.fail[1] = blocked()
    rep = run_daily_check(store, db, fake)
    assert rep.blocked == 1 and rep.sent == 1 and fake.texts(2)
    assert db.list_subscriptions(1) == [] and 1 not in db.active_subscribers()


def test_server_error_is_retried_next_run(store, db, fake):
    db.add_subscription(1, "helwan", "warning")
    fake.fail[1] = server_error()
    rep = run_daily_check(store, db, fake)
    assert rep.failed == 1 and rep.sent == 0 and db.sent_hash(1, TODAY) is None
    del fake.fail[1]
    assert run_daily_check(store, db, fake).sent == 1


def test_flood_control_waits_and_retries_once(store, db, fake):
    db.add_subscription(1, "helwan", "warning")
    fake.fail_once[1] = TelegramError("Too Many Requests", status=429, retry_after=3)
    rep = run_daily_check(store, db, fake)
    assert rep.sent == 1 and len(fake.sent) == 1


def test_dry_run_sends_and_records_nothing(store, db):
    db.add_subscription(1, "helwan", "warning")
    rep = run_daily_check(store, db, None, dry_run=True)
    assert rep.sent == 1 and len(rep.preview) == 1 and db.sent_hash(1, TODAY) is None


def test_client_required_unless_dry_run(store, db):
    with pytest.raises(ValueError):
        run_daily_check(store, db, None)


def test_no_subscribers_is_fine(store, db, fake):
    rep = run_daily_check(store, db, fake)
    assert rep.ok and rep.sent == 0 and fake.sent == []


def test_main_dry_run_and_stale_exit_codes(monkeypatch, tmp_path, mixed_csv, capsys):
    monkeypatch.setenv("FORECASTS_PATH", str(mixed_csv))
    monkeypatch.setenv("BOT_DB_PATH", str(tmp_path / "b.db"))
    monkeypatch.setenv("MAX_AGE_DAYS", "100000")                        # never stale
    db = SubscriberDB(tmp_path / "b.db", "en"); db.add_subscription(1, "helwan", "high"); db.close()
    assert notifier.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would send to chat 1" in out and "Helwan" in out
    old = write_rows(tmp_path / "old.csv", scenario_rows("mixed", date(2020, 1, 1)))      # far older than MAX_AGE_DAYS=1
    monkeypatch.setenv("FORECASTS_PATH", str(old))
    monkeypatch.setenv("MAX_AGE_DAYS", "1")
    assert notifier.main(["--dry-run"]) == 2
    assert "stale" in capsys.readouterr().out
