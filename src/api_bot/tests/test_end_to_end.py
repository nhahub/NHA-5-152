"""API, bot and notifier all read the same forecasts file: they must agree."""
import json
import subprocess
import sys

import pytest

from bot.bot import BotApp
from bot.notifier import run_daily_check
from bot.subscribers import SubscriberDB
from bot.testing import FakeTelegramClient
from conftest import ROOT, TODAY, scenario_rows, write_rows


def test_bot_forecast_matches_the_api(client, store):
    api = client.get("/forecast", params={"district": "helwan"}).json()["forecasts"]
    app = BotApp(store, SubscriberDB(":memory:", "en"), FakeTelegramClient())
    app.handle_update({"update_id": 1, "message": {"chat": {"id": 1, "type": "private"}, "from": {"language_code": "en"}, "text": "/forecast helwan"}})
    text = app.client.last()["text"]
    for f in api:
        assert f"{f['pred_pm25']:.0f} µg/m³" in text and f"risk {100 * f['risk_prob']:.0f}%" in text


def test_alerts_endpoint_and_notifier_select_the_same_rows(client, store):
    api = {(a["station"], a["horizon"]) for a in client.get("/alerts", params={"level": "warning"}).json()["alerts"]}
    db, fake = SubscriberDB(":memory:", "en"), FakeTelegramClient()
    for i, s in enumerate(("downtown", "helwan", "new_cairo", "october", "shobra"), start=1):
        db.add_subscription(i, s, "warning")
    run_daily_check(store, db, fake)
    notified = {s for i, s in enumerate(("downtown", "helwan", "new_cairo", "october", "shobra"), start=1) if fake.texts(i)}
    assert notified == {s for s, _ in api}


def test_sample_generator_output_is_valid_and_alert_levels_follow_config(tmp_path):
    out = tmp_path / "f.csv"
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_sample_forecasts.py"), "--scenario", "severe", "--date", "2026-11-22", "--out", str(out)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    from app.forecast_store import ForecastStore
    rows = ForecastStore(out, today_fn=lambda: TODAY).latest()
    cfg = json.loads((ROOT / "alert_config.json").read_text())
    assert len(rows) == 15
    for f in rows:
        t = cfg[str(f.horizon)]
        assert f.alert_level == ("high" if f.risk_prob >= t["high"] else "warning" if f.risk_prob >= t["warning"] else "none")
    assert sum(f.alert_level == "high" for f in rows) >= 10


@pytest.mark.parametrize("scenario,warn,high", [("calm", 0, 0)])
def test_calm_scenario_has_no_alerts(tmp_path, scenario, warn, high):
    from app.forecast_store import ForecastStore
    s = ForecastStore(write_rows(tmp_path / "f.csv", scenario_rows(scenario)), today_fn=lambda: TODAY)
    assert s.select(min_level="warning") == []
