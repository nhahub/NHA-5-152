from datetime import date

from bot.subscribers import SubscriberDB


def test_add_update_remove_and_list():
    db = SubscriberDB(":memory:", "ar")
    assert db.add_subscription(1, "helwan", "warning") == "added"
    assert db.add_subscription(1, "helwan", "high") == "updated"
    assert db.add_subscription(1, "shobra", "warning") == "added"
    assert db.list_subscriptions(1) == [("helwan", "high"), ("shobra", "warning")]
    assert db.remove_subscription(1, "helwan") == 1 and db.remove_subscription(1, "helwan") == 0
    assert db.remove_subscription(1) == 1 and db.list_subscriptions(1) == []


def test_language_default_and_change():
    db = SubscriberDB(":memory:", "en")
    assert db.get_lang(99) == "en" and not db.has_user(99)
    db.set_lang(99, "ar")
    assert db.get_lang(99) == "ar" and db.has_user(99)


def test_active_subscribers_grouping_and_deactivation():
    db = SubscriberDB(":memory:", "ar")
    db.add_subscription(1, "helwan", "high"); db.add_subscription(1, "shobra", "warning"); db.add_subscription(2, "helwan", "warning")
    act = db.active_subscribers()
    assert act[1]["subs"] == {"helwan": "high", "shobra": "warning"} and act[2]["subs"] == {"helwan": "warning"}
    db.deactivate_user(1)
    assert 1 not in db.active_subscribers() and db.list_subscriptions(1) == []
    assert db.counts() == {"active_users": 1, "subscriptions": 1}


def test_user_who_returns_is_reactivated():
    db = SubscriberDB(":memory:", "ar")
    db.add_subscription(5, "helwan", "high"); db.deactivate_user(5)
    db.ensure_user(5); db.add_subscription(5, "helwan", "high")
    assert 5 in db.active_subscribers()


def test_notification_log_and_meta():
    db = SubscriberDB(":memory:", "ar")
    d = date(2026, 11, 22)
    assert db.sent_hash(1, d) is None
    db.mark_sent(1, d, "abc"); assert db.sent_hash(1, d) == "abc"
    db.mark_sent(1, d, "xyz"); assert db.sent_hash(1, d) == "xyz"
    assert db.sent_hash(1, date(2026, 11, 23)) is None
    assert db.get_meta("k") is None
    db.set_meta("k", "v1"); db.set_meta("k", "v2"); assert db.get_meta("k") == "v2"


def test_data_survives_reopening_the_file(tmp_path):
    p = tmp_path / "sub" / "bot.db"
    db = SubscriberDB(p, "ar")
    db.add_subscription(7, "october", "high"); db.set_lang(7, "en"); db.close()
    db2 = SubscriberDB(p, "ar")
    assert db2.list_subscriptions(7) == [("october", "high")] and db2.get_lang(7) == "en"


def test_level_check_constraint(tmp_path):
    import sqlite3
    import pytest
    db = SubscriberDB(":memory:", "ar")
    with pytest.raises(sqlite3.IntegrityError):
        db.add_subscription(1, "helwan", "critical")
