"""Records real responses of every endpoint (in-process, no server needed) into sample_output/api_examples.txt.

    python scripts/make_api_examples.py [forecasts.csv]      (default: the real Task 1 sample of 2026-11-22)
"""
from __future__ import annotations

import _path  # noqa: F401
import json
import sys
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.forecast_store import ForecastStore
from app.main import create_app

ROOT = _path.ROOT
CASES = [
    ("Service index", "/"),
    ("Health", "/health"),
    ("Districts", "/districts"),
    ("Forecast, one district, all horizons", "/forecast?district=shobra"),
    ("Forecast, one horizon", "/forecast?district=helwan&horizon=1"),
    ("Forecast, district given in Arabic", "/forecast?district=القاهرة الجديدة&horizon=3"),
    ("Alerts (warning and high)", "/alerts"),
    ("Alerts, high only", "/alerts?level=high"),
    ("Error: unknown district", "/forecast?district=maadi"),
    ("Error: horizon out of range", "/forecast?district=helwan&horizon=5"),
    ("Error: district missing", "/forecast"),
]


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "sample_data" / "forecasts_task1_2026-11-22.csv"
    s = get_settings()
    client = TestClient(create_app(ForecastStore(src, s.max_age_days, lambda: date(2026, 11, 22)), s))
    out = [f"Recorded from {src.name}; 'today' fixed at 2026-11-22 so that the forecast is not stale.\n"]
    for title, url in CASES:
        r = client.get(url)
        body = r.json()
        if url == "/health":
            body["server_time_utc"] = "2026-11-22T12:00:00+00:00"
        out.append(f"### {title}\nGET {url}\nHTTP {r.status_code}\n{json.dumps(body, indent=2, ensure_ascii=False)}\n")
    dst = ROOT / "sample_output" / "api_examples.txt"
    dst.write_text("\n".join(out), encoding="utf-8")
    print("wrote", dst)


if __name__ == "__main__":
    main()
