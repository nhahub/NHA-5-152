"""Accuracy check script for Cairo PM2.5 Forecast System (Task 5).

Compares past forecasts in forecast_log.csv against observed PM2.5 values
fetched from the Open-Meteo Air Quality API using the exact training calibration
factor (PM25_FACTOR = 2.18 -> pm2_5_corrected).
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT_DIR = Path(__file__).resolve().parent.parent
OUT_DIR = Path(__file__).resolve().parent / "sample_output"

COORDS = {
    "downtown": (30.0444, 31.2357),
    "helwan": (29.8490, 31.3342),
    "shobra": (30.1286, 31.2422),
    "new_cairo": (30.0074, 31.4913),
    "october": (29.9723, 30.9458),
}
URL_AQ = "https://air-quality-api.open-meteo.com/v1/air-quality"
PM25_FACTOR = 2.18
HEALTH_RISK_THRESHOLD = 55.4


def fetch_observed_pm25(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch hourly PM2.5 from Open-Meteo and aggregate to calibrated daily mean."""
    records = []
    for station, (lat, lon) in COORDS.items():
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": "pm2_5",
            "start_date": start_date,
            "end_date": end_date,
            "timezone": "GMT",
        }
        resp = None
        for attempt in range(4):
            try:
                r = requests.get(URL_AQ, params=params, timeout=30)
                r.raise_for_status()
                resp = r.json()
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(1.5 * (attempt + 1))

        hourly = pd.DataFrame(resp["hourly"])
        hourly["time"] = pd.to_datetime(hourly["time"])
        hourly["target_date"] = hourly["time"].dt.strftime("%Y-%m-%d")
        hourly = hourly.dropna(subset=["pm2_5"])
        if hourly.empty:
            continue

        daily = hourly.groupby("target_date", as_index=False).agg(
            raw_pm25=("pm2_5", "mean"),
            hours_count=("pm2_5", "count"),
        )
        daily = daily[daily["hours_count"] >= 18].copy()
        daily["station"] = station
        daily["actual_pm25"] = (daily["raw_pm25"] * PM25_FACTOR).round(1)
        records.append(daily[["station", "target_date", "actual_pm25", "hours_count"]])

    if not records:
        return pd.DataFrame(columns=["station", "target_date", "actual_pm25", "hours_count"])
    return pd.concat(records, ignore_index=True)


def locate_forecast_log(explicit_path: Path | None) -> Path:
    """Locate forecast_log.csv across standard project directories."""
    if explicit_path is not None:
        return explicit_path
    candidates = [
        ROOT_DIR / "task1_pipeline" / "forecast_log.csv",
        ROOT_DIR / "forecast_log.csv",
        OUT_DIR / "forecast_log.csv",
    ]
    found = next((p for p in candidates if p.is_file()), None)
    if found is None:
        raise FileNotFoundError("Could not find forecast_log.csv in task1_pipeline/ or root.")
    return found


def evaluate_accuracy(
    log_path: Path,
    target_date: str | None = None,
    days_back: int = 7,
    offline_actuals: Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Compare logged forecasts against observed values and compute error metrics."""
    log_df = pd.read_csv(log_path)
    log_df["target_date"] = log_df["target_date"].astype(str)
    log_df["issue_date"] = log_df["issue_date"].astype(str)
    log_df["horizon"] = log_df["horizon"].astype(int)

    today_utc = datetime.now(timezone.utc).date()
    yesterday_str = (today_utc - timedelta(days=1)).isoformat()

    if target_date:
        eval_df = log_df[log_df["target_date"] == target_date].copy()
    else:
        cutoff_str = (today_utc - timedelta(days=days_back)).isoformat()
        eval_df = log_df[
            (log_df["target_date"] <= yesterday_str) & (log_df["target_date"] >= cutoff_str)
        ].copy()

    if eval_df.empty:
        raise ValueError(
            f"No completed past target_dates found in {log_path} (latest eligible date: {yesterday_str})."
        )

    min_date = eval_df["target_date"].min()
    max_date = eval_df["target_date"].max()

    if offline_actuals is not None and offline_actuals.is_file():
        obs_df = pd.read_csv(offline_actuals)
    else:
        obs_df = fetch_observed_pm25(min_date, max_date)

    merged = eval_df.merge(obs_df, on=["station", "target_date"], how="inner")
    if merged.empty:
        raise RuntimeError(f"No observed PM2.5 data available for dates {min_date} to {max_date}.")

    merged["error"] = (merged["pred_pm25"] - merged["actual_pm25"]).round(2)
    merged["abs_error"] = merged["error"].abs().round(2)
    merged["squared_error"] = (merged["error"] ** 2).round(2)
    merged["actual_is_risk"] = merged["actual_pm25"] > HEALTH_RISK_THRESHOLD
    merged["alert_triggered"] = merged["alert_level"].isin(["warning", "high"])
    merged["risk_outcome"] = np.where(
        merged["actual_is_risk"] & merged["alert_triggered"],
        "TP (Caught Risk)",
        np.where(
            merged["actual_is_risk"] & ~merged["alert_triggered"],
            "FN (Missed Risk)",
            np.where(
                ~merged["actual_is_risk"] & merged["alert_triggered"],
                "FP (False Alarm)",
                "TN (Correct Clear)",
            ),
        ),
    )

    by_horizon = {}
    for h, grp in merged.groupby("horizon"):
        by_horizon[f"horizon_{int(h)}"] = {
            "samples": int(len(grp)),
            "mae_ug_m3": round(float(grp["abs_error"].mean()), 2),
            "rmse_ug_m3": round(float(np.sqrt(grp["squared_error"].mean())), 2),
            "mean_bias_ug_m3": round(float(grp["error"].mean()), 2),
        }

    by_district = {}
    for st_name, grp in merged.groupby("station"):
        by_district[str(st_name)] = {
            "samples": int(len(grp)),
            "mae_ug_m3": round(float(grp["abs_error"].mean()), 2),
            "rmse_ug_m3": round(float(np.sqrt(grp["squared_error"].mean())), 2),
            "mean_bias_ug_m3": round(float(grp["error"].mean()), 2),
        }

    summary = {
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_log": str(log_path),
        "date_range": {"start": min_date, "end": max_date},
        "total_evaluated_rows": int(len(merged)),
        "overall_mae_ug_m3": round(float(merged["abs_error"].mean()), 2),
        "overall_rmse_ug_m3": round(float(np.sqrt(merged["squared_error"].mean())), 2),
        "overall_bias_ug_m3": round(float(merged["error"].mean()), 2),
        "by_horizon": by_horizon,
        "by_district": by_district,
    }
    return merged, summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare logged forecasts against actual PM2.5.")
    parser.add_argument("--log", type=Path, default=None, help="Path to forecast_log.csv")
    parser.add_argument("--date", type=str, default=None, help="Specific target_date (YYYY-MM-DD)")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days (default: 7)")
    parser.add_argument(
        "--offline-actuals",
        type=Path,
        default=None,
        help="Optional CSV of observed PM2.5 (station,target_date,actual_pm25) for offline runs.",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    log_path = locate_forecast_log(args.log)
    merged, summary = evaluate_accuracy(
        log_path=log_path,
        target_date=args.date,
        days_back=args.days,
        offline_actuals=args.offline_actuals,
    )

    out_csv = OUT_DIR / "accuracy_comparison.csv"
    out_json = OUT_DIR / "accuracy_summary.json"
    merged.to_csv(out_csv, index=False)
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    cols = [
        "station",
        "issue_date",
        "target_date",
        "horizon",
        "pred_pm25",
        "actual_pm25",
        "error",
        "abs_error",
        "alert_level",
        "risk_outcome",
    ]
    print("=== FORECAST VS ACTUAL PM2.5 COMPARISON ===")
    print(merged[cols].to_string(index=False))
    print("\n=== RECENT ERROR SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print(f"\nSaved detailed table to {out_csv} and summary to {out_json}")


if __name__ == "__main__":
    main()
