# API reference

Base URL (local): `http://127.0.0.1:8000`. Interactive version (try it in the browser): `/docs`. Machine-readable: `/openapi.json`.
All endpoints are `GET`, return JSON, need no authentication, and send CORS headers (`CORS_ORIGINS`, default `*`).

## Concepts

| Term | Meaning |
|---|---|
| `issue_date` | day the forecast was made (UTC) |
| `target_date` | day being forecast = `issue_date` + `horizon` |
| `horizon` | 1, 2 or 3 days ahead |
| `pred_pm25` | predicted PM2.5 in µg/m³ |
| `risk_prob` | probability that the day is a health-risk day (PM2.5 above 55.4 µg/m³) |
| `alert_level` | `none`, `warning` or `high`. Rule applied by the pipeline: `risk_prob >= high` → high; `>= warning` → warning; else none. Thresholds per horizon are in `alert_config.json` (returned by `/alerts`) |
| `aqi_category` | Good, Moderate, Unhealthy for Sensitive Groups, Unhealthy, Very Unhealthy, Hazardous (from `pred_pm25`) |
| `stale` | `true` when the forecast file is older than `MAX_AGE_DAYS` (default 1). A stale forecast must not be shown as current |
| districts | `downtown`, `helwan`, `new_cairo`, `october`, `shobra` |

District parameters accept ids and names in English or Arabic, ignoring case, spaces and underscores: `helwan`, `New Cairo`, `6th of October`, `حلوان`, `القاهرة الجديدة`.

## `GET /forecast`

| Parameter | Required | Description |
|---|---|---|
| `district` | yes | district id or name |
| `horizon` | no | 1, 2 or 3; omit to get all three |

```
GET /forecast?district=helwan&horizon=1
{
  "district": "helwan", "district_name": "Helwan", "issue_date": "2026-11-22", "age_days": 0, "stale": false,
  "forecasts": [ { "station": "helwan", "station_name": "Helwan", "issue_date": "2026-11-22", "target_date": "2026-11-23", "horizon": 1,
                   "pred_pm25": 47.3, "risk_prob": 0.185, "alert_level": "none", "aqi_category": "Unhealthy for Sensitive Groups" } ]
}
```
Errors: `404` unknown district (the message lists the valid ones), `422` missing `district` or horizon outside 1-3, `503` forecast file missing or invalid.

## `GET /alerts`

| Parameter | Default | Description |
|---|---|---|
| `level` | `warning` | minimum level: `warning` returns warning + high, `high` only high |
| `district` | all | only this district |
| `horizon` | all | only this horizon |

Returns `issue_date`, `age_days`, `stale`, `min_level`, `count`, `by_level` (`{"warning": n, "high": n}`), `thresholds` (per horizon, or `null` if `alert_config.json` is missing) and `alerts` (same fields as a forecast item), sorted high first, then by horizon, then by district. An empty list means no alert today.
Errors: `404` unknown district, `422` bad `level`/`horizon`, `503` file problem.

## `GET /districts`

`[{"id": "helwan", "name_en": "Helwan", "name_ar": "حلوان"}, ...]` for the 5 districts. Works even if the forecast file is missing.

## `GET /health`

```
{ "status": "ok", "api": "up", "version": "1.0.0", "server_time_utc": "...",
  "forecasts": { "state": "ok", "issue_date": "2026-11-22", "age_days": 0, "rows": 15, "max_age_days": 1, "detail": null },
  "alert_config": "ok" }
```
`status` is `ok` or `degraded`. `forecasts.state` is `ok`, `stale`, `missing` or `invalid` (with `detail` explaining). Always HTTP 200 while the API process is alive, so a host's health check does not restart the service because the daily file is late.

## How the forecast file is read

* Read from `FORECASTS_PATH` and re-read automatically whenever the file changes (modification time + size): no restart is needed after the daily update.
* Only the rows of the newest `issue_date` are served (the history file `forecast_log.csv` can be used as the source too).
* The file is validated: required columns, horizon 1-3, `alert_level` in none/warning/high, `risk_prob` in [0, 1], non-negative PM2.5, `target_date = issue_date + horizon`, no duplicate (district, issue_date, horizon). Any violation → `503` with the reason, never silently wrong data.

## Run and deploy

* Local: `python run.py` or `uvicorn app.main:app --port 8000`.
* Hosted (free platforms): start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`; set `FORECASTS_PATH` to the file the pipeline refreshes daily (on hosts with ephemeral disks the file must be re-published after each pipeline run) and `CORS_ORIGINS` to the dashboard's address if you want to restrict it.
