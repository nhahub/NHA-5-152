import csv
import json
import sys
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from app.config import Settings                      # noqa: E402
from app.forecast_store import ForecastStore         # noqa: E402
from app.main import create_app                      # noqa: E402
import make_sample_forecasts as gen                  # noqa: E402

REAL_SAMPLE = ROOT / "sample_data" / "forecasts_task1_2026-11-22.csv"
ALERT_CONFIG = ROOT / "alert_config.json"
TODAY = date(2026, 11, 22)
COLS = ["station", "issue_date", "target_date", "horizon", "pred_pm25", "risk_prob", "alert_level", "aqi_category"]


def write_rows(path: Path, rows: list[dict]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS)
        w.writeheader()
        w.writerows(rows)
    return path


def scenario_rows(scenario: str = "mixed", issue: date = TODAY) -> list[dict]:
    return gen.build_rows(issue, scenario, json.loads(ALERT_CONFIG.read_text()))


def settings_for(tmp_path: Path, forecasts: Path) -> Settings:
    return Settings(forecasts_path=forecasts, alert_config_path=ALERT_CONFIG, bot_db_path=tmp_path / "bot.db", max_age_days=1,
                    telegram_token=None, default_lang="ar", notify_hour_cairo=8, cors_origins=("*",))


@pytest.fixture
def mixed_csv(tmp_path):
    return write_rows(tmp_path / "forecasts.csv", scenario_rows("mixed"))


@pytest.fixture
def store(mixed_csv):
    return ForecastStore(mixed_csv, max_age_days=1, today_fn=lambda: TODAY)


@pytest.fixture
def client(tmp_path, mixed_csv, store):
    return TestClient(create_app(store, settings_for(tmp_path, mixed_csv)))
