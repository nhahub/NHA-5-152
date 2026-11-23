"""Writes a forecasts.csv in the agreed format with fake values, so the API, the bot and the tests can be tried without the
forecast pipeline. alert_level uses alert_config.json and aqi_category uses the same bins as the pipeline.

    python scripts/make_sample_forecasts.py --scenario mixed --out data/forecasts.csv        (dated today, UTC)
    scenarios: calm (no alerts) | mixed (warnings and one high) | severe (several high)
"""
from __future__ import annotations

import _path  # noqa: F401
import argparse
import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings

BINS = [(12.0, "Good"), (35.4, "Moderate"), (55.4, "Unhealthy for Sensitive Groups"), (150.4, "Unhealthy"), (250.4, "Very Unhealthy")]
HORIZONS = (1, 2, 3)

# station -> {horizon: (pred_pm25, risk_prob)}
SCENARIOS = {
    "calm": {
        "downtown": {1: (31.2, 0.12), 2: (33.5, 0.2), 3: (34.0, 0.18)}, "helwan": {1: (36.0, 0.15), 2: (38.1, 0.25), 3: (39.0, 0.21)},
        "new_cairo": {1: (22.4, 0.05), 2: (24.0, 0.08), 3: (25.5, 0.09)}, "october": {1: (20.9, 0.04), 2: (23.1, 0.07), 3: (24.2, 0.08)},
        "shobra": {1: (34.7, 0.13), 2: (36.2, 0.22), 3: (37.0, 0.2)}},
    "mixed": {
        "downtown": {1: (51.3, 0.383), 2: (49.0, 0.452), 3: (47.5, 0.31)}, "helwan": {1: (63.5, 0.74), 2: (57.2, 0.61), 3: (52.0, 0.46)},
        "new_cairo": {1: (35.4, 0.064), 2: (38.2, 0.19), 3: (40.1, 0.2)}, "october": {1: (38.0, 0.129), 2: (41.0, 0.17), 3: (42.5, 0.126)},
        "shobra": {1: (55.1, 0.403), 2: (58.9, 0.585), 3: (60.2, 0.5)}},
    "severe": {
        "downtown": {1: (92.0, 0.93), 2: (88.0, 0.86), 3: (80.0, 0.9)}, "helwan": {1: (131.0, 0.97), 2: (118.0, 0.95), 3: (96.0, 0.88)},
        "new_cairo": {1: (70.0, 0.8), 2: (66.0, 0.7), 3: (61.0, 0.6)}, "october": {1: (58.0, 0.62), 2: (62.0, 0.66), 3: (57.0, 0.5)},
        "shobra": {1: (101.0, 0.95), 2: (97.0, 0.9), 3: (90.0, 0.87)}},
}


def category(pm: float) -> str:
    for upper, name in BINS:
        if pm <= upper:
            return name
    return "Hazardous"


def alert_level(prob: float, thr: dict) -> str:
    return "high" if prob >= thr["high"] else "warning" if prob >= thr["warning"] else "none"


def build_rows(issue: date, scenario: str, thresholds: dict) -> list[dict]:
    rows = []
    for station, per_h in SCENARIOS[scenario].items():
        for h in HORIZONS:
            pm, prob = per_h[h]
            rows.append({"station": station, "issue_date": issue.isoformat(), "target_date": (issue + timedelta(days=h)).isoformat(), "horizon": h,
                         "pred_pm25": round(pm, 1), "risk_prob": round(prob, 3), "alert_level": alert_level(prob, thresholds[str(h)]), "aqi_category": category(pm)})
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["station", "issue_date", "target_date", "horizon", "pred_pm25", "risk_prob", "alert_level", "aqi_category"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=list(SCENARIOS), default="mixed")
    ap.add_argument("--date", default=None, help="issue_date, YYYY-MM-DD (default: today, UTC)")
    ap.add_argument("--out", default="data/forecasts.csv")
    a = ap.parse_args()
    s = get_settings()
    thr = json.loads(s.alert_config_path.read_text(encoding="utf-8"))
    issue = date.fromisoformat(a.date) if a.date else datetime.now(timezone.utc).date()
    rows = build_rows(issue, a.scenario, thr)
    write_csv(Path(a.out), rows)
    n = {lv: sum(r["alert_level"] == lv for r in rows) for lv in ("warning", "high")}
    print(f"wrote {a.out}: {len(rows)} rows, issue_date {issue}, scenario '{a.scenario}', alerts {n}")


if __name__ == "__main__":
    main()
