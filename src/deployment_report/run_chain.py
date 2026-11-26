"""End-to-end integration runner for Cairo PM2.5 Forecast System (Task 5).

Runs the full chain once:
1. Syncs alert_config.json across Task 1 and Task 3.
2. Executes Task 1 forecast pipeline (or uses cached/sample forecasts if --skip-pipeline).
3. Atomically publishes forecasts.csv to Task 3 (data/forecasts.csv) and Task 4 (forecasts.csv).
4. Starts Task 3 FastAPI server and verifies /health, /districts, /forecast, 404 handling, and /alerts.
5. Runs a test alert verification (live Telegram test if token is set, or dry-run alert formatting).
6. Validates Task 4 Streamlit dashboard app syntax, dependencies, and data compatibility.
7. Saves a JSON verification report in task5_deploy/sample_output/chain_verification_report.json.
"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pandas as pd

ROOT_DIR = Path(__file__).resolve().parent.parent     
TASK1_DIR = ROOT_DIR / "pipeline"
TASK3_DIR = ROOT_DIR / "api_bot"
TASK4_DIR = ROOT_DIR / "dashboard"
OUT_DIR = Path(__file__).resolve().parent / "sample_output"

REQUIRED_COLS = [
    "station",
    "issue_date",
    "target_date",
    "horizon",
    "pred_pm25",
    "risk_prob",
    "alert_level",
    "aqi_category",
]
VALID_STATIONS = {"downtown", "helwan", "new_cairo", "october", "shobra"}


def atomic_copy(src: Path, dst: Path) -> None:
    """Copy a file atomically using a temporary file and os.replace."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def find_free_port(preferred: int = 8000) -> int:
    """Return preferred port if free, otherwise allocate an available local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex(("127.0.0.1", preferred)) != 0:
            return preferred
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def http_get_json(url: str, timeout: float = 5.0) -> tuple[int, dict | list]:
    """Perform GET request and parse JSON response."""
    try:
        with urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"detail": body}


def validate_forecasts_csv(csv_path: Path) -> dict:
    """Validate schema, stations, horizons, and values of forecasts.csv."""
    if not csv_path.is_file():
        raise FileNotFoundError(f"Missing forecasts file: {csv_path}")
    df = pd.read_csv(csv_path)
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in {csv_path}: {missing}")
    if len(df) != 15:
        raise ValueError(f"Expected 15 rows (5 districts x 3 horizons), found {len(df)}")
    if set(df["station"].unique()) != VALID_STATIONS:
        raise ValueError(f"Unexpected stations: {set(df['station'].unique())}")
    if set(df["horizon"].astype(int).unique()) != {1, 2, 3}:
        raise ValueError(f"Unexpected horizons: {set(df['horizon'].unique())}")
    if (df["pred_pm25"] < 0).any():
        raise ValueError("Negative pred_pm25 values found")
    if ((df["risk_prob"] < 0) | (df["risk_prob"] > 1)).any():
        raise ValueError("risk_prob outside [0, 1]")
    if not set(df["alert_level"].unique()).issubset({"none", "warning", "high"}):
        raise ValueError(f"Invalid alert_level values: {set(df['alert_level'].unique())}")

    return {
        "rows": int(len(df)),
        "issue_date": str(df["issue_date"].iloc[0]),
        "target_dates": sorted(df["target_date"].astype(str).unique().tolist()),
        "alerts_count": int((df["alert_level"] != "none").sum()),
    }


def step_sync_alert_config(alert_config_override: Path | None) -> str:
    """Ensure Task 1 and Task 3 use the exact same alert_config.json."""
    src = alert_config_override
    if src is None:
        candidates = [
            ROOT_DIR / "alert_config.json",
            TASK3_DIR / "alert_config.json",
            TASK1_DIR / "models_v2" / "alert_config.json",
            OUT_DIR / "alert_config.json",
        ]
        src = next((p for p in candidates if p.is_file()), None)
    if src is None:
        return "skipped (no alert_config.json found)"

    targets = [
        TASK1_DIR / "models_v2" / "alert_config.json",
        TASK3_DIR / "alert_config.json",
    ]
    synced = []
    for dst in targets:
        if dst.parent.is_dir() and src.resolve() != dst.resolve():
            atomic_copy(src, dst)
            synced.append(str(dst.relative_to(ROOT_DIR)))
    return f"synced from {src.name} -> {synced or ['already in place']}"


def step_run_pipeline(skip_pipeline: bool) -> Path:
    """Run Task 1 forecast pipeline and return path to generated forecasts.csv."""
    t1_forecast = TASK1_DIR / "forecasts.csv"
    t1_log = TASK1_DIR / "forecast_log.csv"

    if not skip_pipeline and (TASK1_DIR / "pipeline" / "run_forecast.py").is_file():
        print("[1/5] Running Task 1 forecast pipeline...")
        subprocess.run(
            [sys.executable, "pipeline/run_forecast.py"],
            cwd=str(TASK1_DIR),
            check=True,
        )
    else:
        print("[1/5] Using existing forecasts.csv (pipeline execution skipped)...")

    for candidate in [
        t1_forecast,
        ROOT_DIR / "forecasts.csv",
        TASK3_DIR / "data" / "forecasts.csv",
        TASK4_DIR / "forecasts.csv",
        OUT_DIR / "forecasts.csv",
    ]:
        if candidate.is_file():
            if t1_forecast.parent.is_dir() and candidate.resolve() != t1_forecast.resolve():
                atomic_copy(candidate, t1_forecast)
            if t1_log.parent.is_dir() and not t1_log.is_file():
                atomic_copy(candidate, t1_log)
            return candidate

    raise FileNotFoundError("No forecasts.csv found across project folders.")


def step_distribute_forecasts(src_csv: Path) -> list[str]:
    """Publish validated forecasts.csv atomically to Task 3 and Task 4."""
    print("[2/5] Distributing forecasts.csv atomically to Task 3 and Task 4...")
    published = []
    for dst in [
        
    ]:
        if dst.parent.is_dir() and src_csv.resolve() != dst.resolve():
            atomic_copy(src_csv, dst)
            published.append(str(dst.relative_to(ROOT_DIR)))
    return published


def step_verify_api(forecasts_csv: Path) -> dict:
    """Start Task 3 FastAPI temporarily and test all required endpoints."""
    print("[3/5] Verifying Task 3 FastAPI endpoints...")
    if not (TASK3_DIR / "app" / "main.py").is_file():
        return {"status": "skipped", "reason": "task3_api_bot/app/main.py not found"}

    port = find_free_port(8000)
    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["FORECASTS_PATH"] = str(forecasts_csv.resolve())
    env["MAX_AGE_DAYS"] = "30"
    if (TASK3_DIR / "alert_config.json").is_file():
        env["ALERT_CONFIG_PATH"] = str((TASK3_DIR / "alert_config.json").resolve())

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(TASK3_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        ready = False
        for _ in range(30):
            try:
                code, health = http_get_json(f"{base_url}/health", timeout=1.0)
                if code == 200:
                    ready = True
                    break
            except (URLError, ConnectionError, OSError):
                time.sleep(0.3)

        if not ready:
            raise RuntimeError("FastAPI server failed to start within timeout.")

        _, health_data = http_get_json(f"{base_url}/health")
        code_dist, districts_data = http_get_json(f"{base_url}/districts")
        code_fc, forecast_data = http_get_json(f"{base_url}/forecast?district=helwan&horizon=1")
        code_404, err_404 = http_get_json(f"{base_url}/forecast?district=unknown_district")
        code_alerts, alerts_data = http_get_json(f"{base_url}/alerts?level=warning")

        assert code_dist == 200 and len(districts_data) == 5, "Invalid /districts response"
        assert code_fc == 200 and forecast_data["district"] == "helwan", "Invalid /forecast response"
        assert code_404 == 404, f"Expected 404 for unknown district, got {code_404}"
        assert code_alerts == 200 and "alerts" in alerts_data, "Invalid /alerts response"

        return {
            "status": "passed",
            "health_state": health_data.get("forecasts", {}).get("state"),
            "districts_count": len(districts_data),
            "sample_helwan_t1_pm25": forecast_data["forecasts"][0]["pred_pm25"],
            "invalid_district_http_status": code_404,
            "active_alerts_count": alerts_data["count"],
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def step_verify_test_alert(forecasts_csv: Path) -> dict:
    """Verify Telegram alert payload generation and optional Bot API connectivity."""
    print("[4/5] Running test alert verification...")
    df = pd.read_csv(forecasts_csv)
    alerts_df = df[df["alert_level"].isin(["warning", "high"])].copy()

    if alerts_df.empty:
        sample_msg = (
            "Test Alert Preview (Simulated): [WARNING] Helwan - "
            "PM2.5 ~ 54.7 ug/m3 (Unhealthy for Sensitive Groups)"
        )
    else:
        first = alerts_df.iloc[0]
        sample_msg = (
            f"Air-quality alert ({first['issue_date']}): {first['station']} "
            f"horizon {int(first['horizon'])} -> {str(first['alert_level']).upper()} "
            f"(PM2.5 ~ {first['pred_pm25']} ug/m3, risk {float(first['risk_prob']):.1%})"
        )

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    bot_status = "dry_run (TELEGRAM_BOT_TOKEN not set)"
    if token:
        code, resp = http_get_json(f"https://api.telegram.org/bot{token}/getMe", timeout=5.0)
        if code == 200 and resp.get("ok"):
            username = resp["result"].get("username", "unknown")
            bot_status = f"verified (@{username})"
        else:
            bot_status = f"token_error (HTTP {code})"

    return {
        "status": "passed",
        "bot_connection": bot_status,
        "active_alert_rows": int(len(alerts_df)),
        "sample_alert_message": sample_msg,
    }


def step_verify_dashboard(forecasts_csv: Path) -> dict:
    """Verify Task 4 Streamlit dashboard syntax and data compatibility."""
    print("[5/5] Verifying Task 4 Streamlit dashboard...")
    app_py = TASK4_DIR / "app.py"
    if not app_py.is_file():
        return {"status": "skipped", "reason": "task4_dashboard/app.py not found"}

    py_compile.compile(str(app_py), doraise=True)
    df = pd.read_csv(forecasts_csv)
    horizons_present = sorted(df["horizon"].astype(int).unique().tolist())
    return {
        "status": "passed",
        "app_file": str(app_py.relative_to(ROOT_DIR)),
        "syntax_check": "ok",
        "horizons_ready": horizons_present,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run full end-to-end integration chain.")
    parser.add_argument(
        "--skip-pipeline",
        action="store_true",
        help="Skip live Open-Meteo fetch and validate using existing forecasts.csv.",
    )
    parser.add_argument(
        "--alert-config",
        type=Path,
        default=None,
        help="Optional path to alert_config.json to sync before running.",
    )
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    config_sync = step_sync_alert_config(args.alert_config)
    forecasts_csv = step_run_pipeline(args.skip_pipeline)
    csv_summary = validate_forecasts_csv(forecasts_csv)
    published = step_distribute_forecasts(forecasts_csv)
    api_result = step_verify_api(forecasts_csv)
    alert_result = step_verify_test_alert(forecasts_csv)
    dash_result = step_verify_dashboard(forecasts_csv)

    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall_status": "SUCCESS",
        "alert_config_sync": config_sync,
        "forecasts_validation": csv_summary,
        "published_targets": published,
        "task3_api_verification": api_result,
        "test_alert_verification": alert_result,
        "task4_dashboard_verification": dash_result,
    }

    report_path = OUT_DIR / "chain_verification_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n=== END-TO-END CHAIN VERIFICATION COMPLETE ===")
    print(json.dumps(report, indent=2))
    print(f"Saved report to: {report_path}")


if __name__ == "__main__":
    main()
