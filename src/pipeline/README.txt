FORECAST PIPELINE (Task 1)

What it does
Downloads the latest weather and air-quality data from Open-Meteo (free, no key),
builds the 76 model features for the 5 districts, runs the 6 models and writes:
  forecasts.csv      today's forecasts (overwritten each run)
  forecast_log.csv   every forecast ever made (appended, one set per issue_date)

How to run (once a day, any time after 02:00 Cairo time)
  pip install -r requirements.txt
  python pipeline/run_forecast.py
Run it from the task1_pipeline folder. Running twice on the same day replaces that day's rows.

Output columns
  station       downtown, helwan, new_cairo, october, shobra
  issue_date    the day the forecast was made (UTC day)
  target_date   the day being forecast (issue_date + horizon)
  horizon       1, 2 or 3 days ahead
  pred_pm25     predicted PM2.5 in ug/m3
  risk_prob     probability that the day is a health-risk day (PM2.5 above 55.4)
  alert_level   none / warning / high, from models_v2/alert_config.json
  aqi_category  EPA category computed from pred_pm25

Changing alert levels
Edit models_v2/alert_config.json. No retraining needed.

Known limits
- Live weather comes from the forecast service, training weather from the archive: small differences.
- 48h and 72h alerts have low recall (they miss many risky days), especially in spring and summer.
- Satellite features are not used (dropped: no accuracy loss, simpler to run).
- Ramadan dates for 2027 are approximate.