from datetime import date

import pytest

from app.forecast_store import ForecastStore
from bot.bot import BotApp
from bot.subscribers import SubscriberDB
from bot.testing import FakeTelegramClient, blocked
from conftest import TODAY

CHAT = 1001


def msg(text, chat_id=CHAT, code="en", chat_type="private"):
    return {"update_id": 1, "message": {"chat": {"id": chat_id, "type": chat_type}, "from": {"id": chat_id, "language_code": code}, "text": text}}


def cb(data, chat_id=CHAT):
    return {"update_id": 2, "callback_query": {"id": "cq1", "from": {"id": chat_id, "language_code": "en"}, "message": {"chat": {"id": chat_id, "type": "private"}}, "data": data}}


@pytest.fixture
def env(store):
    db, fake = SubscriberDB(":memory:", "ar"), FakeTelegramClient()
    return BotApp(store, db, fake), db, fake


def say(env, text, **kw):
    app, db, fake = env
    n = len(fake.sent)
    app.handle_update(msg(text, **kw))
    return fake.sent[n:]


def test_start_help_districts(env):
    app, db, fake = env
    assert "Cairo air-quality alerts" in say(env, "/start")[0]["text"]
    assert "/subscribe" in say(env, "/help")[0]["text"]
    d = say(env, "/districts")[0]["text"]
    assert all(x in d for x in ("Downtown", "Helwan", "New Cairo", "6th of October", "Shobra"))


def test_language_follows_telegram_app_language_on_first_contact(env):
    app, db, fake = env
    app.handle_update(msg("/start", chat_id=1, code="ar-EG"))
    app.handle_update(msg("/start", chat_id=2, code="en-GB"))
    app.handle_update(msg("/start", chat_id=3, code=""))
    assert [db.get_lang(i) for i in (1, 2, 3)] == ["ar", "en", "ar"]
    assert "تنبيهات جودة الهواء" in fake.texts(1)[0] and "Cairo air-quality alerts" in fake.texts(2)[0]


def test_subscribe_direct(env):
    app, db, fake = env
    out = say(env, "/subscribe helwan high")
    assert db.list_subscriptions(CHAT) == [("helwan", "high")] and "Helwan" in out[0]["text"] and "High risk" in out[0]["text"]


def test_subscribe_defaults_to_warning(env):
    say(env, "/subscribe shobra")
    assert env[1].list_subscriptions(CHAT) == [("shobra", "warning")]


@pytest.mark.parametrize("text,district,level", [
    ("/subscribe new cairo warning", "new_cairo", "warning"), ("/subscribe New Cairo", "new_cairo", "warning"),
    ("/subscribe 6th of October high", "october", "high"), ("/subscribe شبرا تحذير", "shobra", "warning"),
    ("/subscribe حلوان خطر", "helwan", "high"), ("/subscribe القاهرة الجديدة high", "new_cairo", "high"),
    ("/subscribe@CairoAirBot helwan high", "helwan", "high"), ("/SUBSCRIBE Helwan HIGH", "helwan", "high"),
])
def test_subscribe_name_and_level_variants(env, text, district, level):
    say(env, text)
    assert env[1].list_subscriptions(CHAT) == [(district, level)]


def test_subscribe_all(env):
    say(env, "/subscribe all high")
    assert [d for d, _ in env[1].list_subscriptions(CHAT)] == ["downtown", "helwan", "new_cairo", "october", "shobra"]
    assert all(l == "high" for _, l in env[1].list_subscriptions(CHAT))


def test_changing_level_says_updated(env):
    say(env, "/subscribe helwan warning")
    out = say(env, "/subscribe helwan high")
    assert "Updated" in out[0]["text"] and env[1].list_subscriptions(CHAT) == [("helwan", "high")]


def test_subscribe_unknown_district_and_bad_level(env):
    out = say(env, "/subscribe maadi")
    assert "do not know" in out[0]["text"] and "helwan" in out[0]["text"] and env[1].list_subscriptions(CHAT) == []
    out = say(env, "/subscribe helwan medium")
    assert "must be" in out[0]["text"] and env[1].list_subscriptions(CHAT) == []


def test_user_text_is_html_escaped(env):
    out = say(env, "/subscribe <b>x</b>")
    assert "<b>x</b>" not in out[0]["text"] and "&lt;b&gt;x&lt;/b&gt;" in out[0]["text"]


def test_subscribe_without_arguments_shows_district_buttons(env):
    out = say(env, "/subscribe")
    kb = out[0]["reply_markup"]["inline_keyboard"]
    data = [b["callback_data"] for row in kb for b in row]
    assert data == ["sd:downtown", "sd:helwan", "sd:new_cairo", "sd:october", "sd:shobra", "sd:all"]
    assert all(len(d.encode()) <= 64 for d in data)


def test_button_flow_district_then_level(env):
    app, db, fake = env
    app.handle_update(cb("sd:helwan"))
    kb = fake.last()["reply_markup"]["inline_keyboard"][0]
    assert [b["callback_data"] for b in kb] == ["sl:helwan:warning", "sl:helwan:high"] and "Helwan" in fake.last()["text"]
    app.handle_update(cb("sl:helwan:high"))
    assert db.list_subscriptions(CHAT) == [("helwan", "high")]
    assert fake.answers[-1][0] == "cq1" and fake.answers[-1][1] == "Done"


def test_button_flow_all_districts(env):
    app, db, fake = env
    app.handle_update(cb("sd:all")); app.handle_update(cb("sl:all:warning"))
    assert len(db.list_subscriptions(CHAT)) == 5


@pytest.mark.parametrize("data", ["", "x", "sd:maadi", "sl:helwan:critical", "sl:helwan", "sd:helwan:extra", "sl:maadi:high", None])
def test_invalid_button_data_is_ignored_safely(env, data):
    app, db, fake = env
    app.handle_update(cb(data))
    assert db.list_subscriptions(CHAT) == [] and fake.sent == [] and fake.answers[-1][1] == "Something went wrong"


def test_unsubscribe_variants(env):
    say(env, "/subscribe all warning")
    assert "Removed" in say(env, "/unsubscribe helwan")[0]["text"] and "helwan" not in [d for d, _ in env[1].list_subscriptions(CHAT)]
    assert "not subscribed" in say(env, "/unsubscribe helwan")[0]["text"]
    assert "do not know" in say(env, "/unsubscribe maadi")[0]["text"]
    usage = say(env, "/unsubscribe")[0]["text"]
    assert "/unsubscribe all" in usage and "Shobra" in usage
    assert "All your subscriptions were removed" in say(env, "/unsubscribe all")[0]["text"]
    assert env[1].list_subscriptions(CHAT) == []
    assert "no subscriptions" in say(env, "/unsubscribe all")[0]["text"]
    assert "no subscriptions" in say(env, "/unsubscribe")[0]["text"]


def test_stop_is_an_alias_of_unsubscribe(env):
    say(env, "/subscribe helwan high")
    say(env, "/stop all")
    assert env[1].list_subscriptions(CHAT) == []


def test_status(env):
    assert "no subscriptions" in say(env, "/status")[0]["text"]
    say(env, "/subscribe helwan high"); say(env, "/subscribe shobra warning")
    t = say(env, "/status")[0]["text"]
    assert "Helwan" in t and "High risk" in t and "Shobra" in t and "Warning" in t


def test_forecast_for_a_district(env):
    t = say(env, "/forecast helwan")[0]["text"]
    lines = t.split("\n")
    assert "Helwan" in lines[0] and "Tomorrow" in lines[1] and "High risk" in lines[1] and "In 2 days" in lines[2] and "In 3 days" in lines[3]
    assert "New Cairo" in say(env, "/forecast New Cairo")[0]["text"]
    assert "Helwan" in say(env, "/forecast حلوان", code="en")[0]["text"]


def test_forecast_without_or_with_wrong_district_shows_usage(env):
    assert "Which district" in say(env, "/forecast")[0]["text"]
    assert "Which district" in say(env, "/forecast maadi")[0]["text"]


def test_forecast_warns_when_stale(mixed_csv):
    store = ForecastStore(mixed_csv, 1, lambda: date(2026, 11, 26))
    env_ = (BotApp(store, SubscriberDB(":memory:", "en"), FakeTelegramClient()),) 
    app = env_[0]
    app.handle_update(msg("/forecast helwan"))
    assert "day(s) old" in app.client.last()["text"]


def test_forecast_when_data_missing(tmp_path):
    app = BotApp(ForecastStore(tmp_path / "none.csv", 1, lambda: TODAY), SubscriberDB(":memory:", "en"), FakeTelegramClient())
    app.handle_update(msg("/forecast helwan"))
    assert "not available" in app.client.last()["text"]


def test_language_switch(env):
    app, db, fake = env
    say(env, "/lang en")
    assert db.get_lang(CHAT) == "en"
    out = say(env, "/lang ar")
    assert db.get_lang(CHAT) == "ar" and "العربية" in out[0]["text"]
    assert "استخدم /lang" in say(env, "/lang fr", code="en")[0]["text"]          # the user's stored language is Arabic now
    say(env, "/subscribe helwan high")
    assert "تم الاشتراك" in fake.last()["text"]


def test_unknown_command_and_plain_text(env):
    assert "do not know that command" in say(env, "/nonsense", code="en")[0]["text"]
    assert "/subscribe" in say(env, "hello", code="en")[0]["text"]
    assert say(env, "") == []


def test_group_chats_are_ignored(env):
    assert say(env, "/start", chat_type="group") == [] and say(env, "/subscribe helwan", chat_type="supergroup") == []
    assert env[1].list_subscriptions(CHAT) == []


def test_unrelated_updates_are_ignored(env):
    env[0].handle_update({"update_id": 3, "edited_message": {"text": "x"}})
    env[0].handle_update({"update_id": 4})
    assert env[2].sent == []


def test_user_who_blocked_the_bot_is_removed(env):
    app, db, fake = env
    db.add_subscription(CHAT, "helwan", "high")
    fake.fail[CHAT] = blocked()
    app.handle_update(msg("/status"))
    assert db.list_subscriptions(CHAT) == []


def test_send_errors_do_not_crash_the_handler(env):
    from bot.testing import server_error
    app, db, fake = env
    fake.fail[CHAT] = server_error()
    app.handle_update(msg("/help"))                    # must not raise
