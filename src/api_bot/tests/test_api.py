from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.forecast_store import ForecastStore
from app.main import create_app
from conftest import settings_for, TODAY, scenario_rows, write_rows


def test_root_lists_endpoints(client):
    r = client.get("/")
    assert r.status_code == 200 and r.json()["docs"] == "/docs" and any("/forecast" in e for e in r.json()["endpoints"])


def test_districts(client):
    r = client.get("/districts")
    assert r.status_code == 200
    d = r.json()
    assert [x["id"] for x in d] == ["downtown", "helwan", "new_cairo", "october", "shobra"]
    assert all(x["name_en"] and x["name_ar"] for x in d)


def test_forecast_all_horizons(client):
    r = client.get("/forecast", params={"district": "helwan"})
    assert r.status_code == 200
    j = r.json()
    assert j["district"] == "helwan" and j["district_name"] == "Helwan" and j["stale"] is False and j["issue_date"] == "2026-11-22"
    assert [f["horizon"] for f in j["forecasts"]] == [1, 2, 3]
    f = j["forecasts"][0]
    assert set(f) == {"station", "station_name", "issue_date", "target_date", "horizon", "pred_pm25", "risk_prob", "alert_level", "aqi_category"}
    assert f["target_date"] == "2026-11-23" and f["alert_level"] == "high"


def test_forecast_single_horizon(client):
    j = client.get("/forecast", params={"district": "helwan", "horizon": 2}).json()
    assert len(j["forecasts"]) == 1 and j["forecasts"][0]["horizon"] == 2


@pytest.mark.parametrize("name", ["New Cairo", "new_cairo", "القاهرة الجديدة", "NEW CAIRO"])
def test_forecast_accepts_name_variants(client, name):
    assert client.get("/forecast", params={"district": name}).json()["district"] == "new_cairo"


def test_unknown_district_is_404_with_valid_list(client):
    r = client.get("/forecast", params={"district": "maadi"})
    assert r.status_code == 404 and "Valid districts" in r.json()["detail"] and "helwan" in r.json()["detail"]


def test_missing_district_is_422(client):
    assert client.get("/forecast").status_code == 422


@pytest.mark.parametrize("h", [0, 4, -1, "x"])
def test_bad_horizon_is_422(client, h):
    assert client.get("/forecast", params={"district": "helwan", "horizon": h}).status_code == 422


def test_alerts_default_returns_warning_and_high_sorted(client):
    j = client.get("/alerts").json()
    assert j["min_level"] == "warning" and j["count"] == 7 and j["by_level"] == {"warning": 6, "high": 1}
    levels = [a["alert_level"] for a in j["alerts"]]
    assert levels[0] == "high" and levels == sorted(levels, key=lambda x: x != "high")
    assert set(j["thresholds"]) == {"1", "2", "3"} and j["thresholds"]["1"]["high"] == 0.587


def test_alerts_high_only(client):
    j = client.get("/alerts", params={"level": "high"}).json()
    assert j["count"] == 1 and j["alerts"][0]["station"] == "helwan" and j["alerts"][0]["horizon"] == 1


def test_alerts_filters(client):
    j = client.get("/alerts", params={"district": "shobra", "horizon": 2}).json()
    assert j["count"] == 1 and j["alerts"][0]["station"] == "shobra"
    assert client.get("/alerts", params={"district": "october"}).json()["count"] == 0


def test_alerts_validation(client):
    assert client.get("/alerts", params={"level": "none"}).status_code == 422
    assert client.get("/alerts", params={"level": "critical"}).status_code == 422
    assert client.get("/alerts", params={"district": "maadi"}).status_code == 404
    assert client.get("/alerts", params={"horizon": 9}).status_code == 422


def test_health_ok(client):
    j = client.get("/health").json()
    assert j["status"] == "ok" and j["api"] == "up" and j["forecasts"]["state"] == "ok" and j["forecasts"]["rows"] == 15 and j["alert_config"] == "ok"


def test_stale_is_flagged_everywhere(tmp_path, mixed_csv):
    c = TestClient(create_app(ForecastStore(mixed_csv, 1, lambda: date(2026, 11, 26)), settings_for(tmp_path, mixed_csv)))
    assert c.get("/forecast", params={"district": "helwan"}).json()["stale"] is True
    assert c.get("/alerts").json()["stale"] is True
    h = c.get("/health").json()
    assert h["status"] == "degraded" and h["forecasts"]["state"] == "stale" and h["forecasts"]["age_days"] == 4


def test_missing_file_gives_503_and_degraded_health(tmp_path):
    p = tmp_path / "none.csv"
    c = TestClient(create_app(ForecastStore(p, 1, lambda: TODAY), settings_for(tmp_path, p)))
    assert c.get("/forecast", params={"district": "helwan"}).status_code == 503
    assert c.get("/alerts").status_code == 503
    assert c.get("/districts").status_code == 200                       # static info keeps working
    h = c.get("/health")
    assert h.status_code == 200 and h.json()["status"] == "degraded" and h.json()["forecasts"]["state"] == "missing"


def test_invalid_file_gives_503(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("station,issue_date\nhelwan,2026-11-22\n")
    c = TestClient(create_app(ForecastStore(p, 1, lambda: TODAY), settings_for(tmp_path, p)))
    r = c.get("/forecast", params={"district": "helwan"})
    assert r.status_code == 503 and "invalid" in r.json()["detail"]
    assert c.get("/health").json()["forecasts"]["state"] == "invalid"


def test_new_daily_file_is_served_without_restart(tmp_path):
    import os
    p = write_rows(tmp_path / "f.csv", scenario_rows("calm"))
    c = TestClient(create_app(ForecastStore(p, 1, lambda: TODAY), settings_for(tmp_path, p)))
    assert c.get("/alerts").json()["count"] == 0
    write_rows(p, scenario_rows("severe"))
    st = p.stat(); os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert c.get("/alerts").json()["count"] > 5


def test_missing_alert_config_does_not_break_alerts(tmp_path, mixed_csv, store):
    s = settings_for(tmp_path, mixed_csv)
    s = type(s)(**{**s.__dict__, "alert_config_path": tmp_path / "nope.json"})
    c = TestClient(create_app(store, s))
    j = c.get("/alerts").json()
    assert j["thresholds"] is None and j["count"] == 7
    assert c.get("/health").json()["alert_config"] == "missing"


def test_cors_header_present(client):
    r = client.get("/districts", headers={"Origin": "http://localhost:8501"})
    assert r.headers.get("access-control-allow-origin") in ("*", "http://localhost:8501")


def test_post_not_allowed(client):
    assert client.post("/forecast", params={"district": "helwan"}).status_code == 405


def test_openapi_documents_the_four_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/forecast", "/districts", "/health", "/alerts"} <= set(paths)
