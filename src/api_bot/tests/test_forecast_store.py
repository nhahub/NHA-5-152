import json
import os
from datetime import date, timedelta

import pytest

from app.forecast_store import ForecastError, ForecastStore, LEVEL_RANK
from conftest import ALERT_CONFIG, COLS, REAL_SAMPLE, TODAY, scenario_rows, write_rows


def test_real_task1_sample_loads_and_is_consistent():
    s = ForecastStore(REAL_SAMPLE, today_fn=lambda: TODAY)
    rows = s.latest()
    assert len(rows) == 15 and s.issue_date == TODAY
    assert s.stations() == ["downtown", "helwan", "new_cairo", "october", "shobra"]
    assert sorted({r.horizon for r in rows}) == [1, 2, 3]
    assert all(r.target_date == r.issue_date + timedelta(days=r.horizon) for r in rows)


def test_real_sample_alert_levels_follow_alert_config():
    """The alert_level written by the pipeline must equal the rule applied to risk_prob with alert_config.json."""
    cfg = json.loads(ALERT_CONFIG.read_text())
    for r in ForecastStore(REAL_SAMPLE, today_fn=lambda: TODAY).latest():
        t = cfg[str(r.horizon)]
        expected = "high" if r.risk_prob >= t["high"] else "warning" if r.risk_prob >= t["warning"] else "none"
        assert r.alert_level == expected, (r.station, r.horizon)


def test_latest_returns_only_newest_issue_date(tmp_path):
    rows = scenario_rows("calm", date(2026, 11, 21)) + scenario_rows("mixed", date(2026, 11, 22))
    s = ForecastStore(write_rows(tmp_path / "log.csv", rows), today_fn=lambda: TODAY)
    assert len(s.latest()) == 15 and s.issue_date == date(2026, 11, 22)


def test_rows_sorted_by_district_then_horizon(store):
    keys = [(r.station, r.horizon) for r in store.latest()]
    assert keys == sorted(keys, key=lambda k: (["downtown", "helwan", "new_cairo", "october", "shobra"].index(k[0]), k[1]))


@pytest.mark.parametrize("today,stale", [(date(2026, 11, 22), False), (date(2026, 11, 23), False), (date(2026, 11, 24), True), (date(2026, 12, 30), True)])
def test_staleness(mixed_csv, today, stale):
    s = ForecastStore(mixed_csv, max_age_days=1, today_fn=lambda: today)
    assert s.is_stale() is stale and s.age_days() == (today - TODAY).days


def test_max_age_is_configurable(mixed_csv):
    s = ForecastStore(mixed_csv, max_age_days=5, today_fn=lambda: date(2026, 11, 26))
    assert not s.is_stale()


def test_select_filters(store):
    assert {r.station for r in store.select(station="helwan")} == {"helwan"}
    assert [r.horizon for r in store.select(station="helwan")] == [1, 2, 3]
    assert {r.horizon for r in store.select(horizon=2)} == {2}
    warn = store.select(min_level="warning")
    assert warn and all(LEVEL_RANK[r.alert_level] >= 1 for r in warn)
    high = store.select(min_level="high")
    assert [(r.station, r.horizon) for r in high] == [("helwan", 1)]
    assert store.select(station="nowhere") == []


def _row(**over):
    r = dict(station="helwan", issue_date="2026-11-22", target_date="2026-11-23", horizon=1, pred_pm25=40.0, risk_prob=0.3, alert_level="none", aqi_category="Moderate")
    r.update(over)
    return r


@pytest.mark.parametrize("bad,fragment", [
    (_row(horizon=4), "horizon"), (_row(horizon=0), "horizon"),
    (_row(alert_level="red"), "alert_level"), (_row(risk_prob=1.2), "risk_prob"), (_row(risk_prob=-0.1), "risk_prob"),
    (_row(pred_pm25=-3), "negative"), (_row(target_date="2026-11-25"), "target_date"),
    (_row(issue_date="22/11/2026"), "bad value"), (_row(pred_pm25="abc"), "bad value"), (_row(station=""), "empty station"),
])
def test_invalid_rows_are_rejected(tmp_path, bad, fragment):
    s = ForecastStore(write_rows(tmp_path / "f.csv", [bad]), today_fn=lambda: TODAY)
    with pytest.raises(ForecastError) as e:
        s.latest()
    assert e.value.kind == "invalid" and fragment in e.value.message


def test_duplicate_rows_rejected(tmp_path):
    s = ForecastStore(write_rows(tmp_path / "f.csv", [_row(), _row()]), today_fn=lambda: TODAY)
    with pytest.raises(ForecastError) as e:
        s.latest()
    assert "duplicate" in e.value.message


def test_missing_column_empty_and_missing_file(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("station,issue_date\nhelwan,2026-11-22\n")
    with pytest.raises(ForecastError) as e:
        ForecastStore(p).latest()
    assert e.value.kind == "invalid" and "missing column" in e.value.message
    p.write_text(",".join(COLS) + "\n")
    with pytest.raises(ForecastError) as e:
        ForecastStore(p).latest()
    assert "no forecast rows" in e.value.message
    with pytest.raises(ForecastError) as e:
        ForecastStore(tmp_path / "absent.csv").latest()
    assert e.value.kind == "missing"


def test_utf8_bom_and_blank_lines_are_accepted(tmp_path):
    p = write_rows(tmp_path / "f.csv", scenario_rows("mixed"))
    p.write_bytes(b"\xef\xbb\xbf" + p.read_bytes() + b"\r\n\r\n")
    assert len(ForecastStore(p, today_fn=lambda: TODAY).latest()) == 15


def test_file_change_is_picked_up_without_restart(tmp_path):
    p = write_rows(tmp_path / "f.csv", scenario_rows("calm"))
    s = ForecastStore(p, today_fn=lambda: TODAY)
    assert s.select(min_level="warning") == []
    write_rows(p, scenario_rows("severe"))
    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))      # make the change visible even on coarse clocks
    assert len(s.select(min_level="high")) >= 5


def test_file_disappearing_is_reported(tmp_path):
    p = write_rows(tmp_path / "f.csv", scenario_rows("calm"))
    s = ForecastStore(p, today_fn=lambda: TODAY)
    s.latest()
    p.unlink()
    with pytest.raises(ForecastError) as e:
        s.latest()
    assert e.value.kind == "missing"


def test_status_states(tmp_path, mixed_csv):
    ok = ForecastStore(mixed_csv, today_fn=lambda: TODAY).status()
    assert ok["state"] == "ok" and ok["rows"] == 15 and ok["issue_date"] == "2026-11-22"
    assert ForecastStore(mixed_csv, today_fn=lambda: date(2026, 11, 30)).status()["state"] == "stale"
    assert ForecastStore(tmp_path / "nope.csv").status()["state"] == "missing"
    bad = tmp_path / "bad.csv"
    bad.write_text("x,y\n1,2\n")
    assert ForecastStore(bad).status()["state"] == "invalid"
