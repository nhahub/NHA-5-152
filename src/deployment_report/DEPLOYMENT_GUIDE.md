# Deployment & Operations Guide (Task 5)

**Owner:** Yousif Sherif  
**System:** Greater Cairo PM2.5 Air Quality Forecasting & Early Warning Platform

---

## 1. System Architecture & Data Flow

```text
[Open-Meteo Live Weather & AQ APIs]
                |
                v
   Task 1: Forecast Pipeline (run_forecast.py)
       |-- Writes forecasts.csv (15 rows: 5 districts x 3 horizons)
       |-- Appends forecast_log.csv (historical audit log)
                |
        +-------+-------+
        |               |
        v               v
Task 3: FastAPI + Bot   Task 4: Streamlit Dashboard
(Render Free Web)       (Streamlit Community Cloud)
  |-- /forecast           |-- Interactive Cairo AQI Map
  |-- /alerts             |-- 24h / 48h / 72h Horizon Switch
  |-- /districts          |-- District Health Guidance
  |-- /health             |-- Alert & Reliability Notes
  |-- Telegram Alerts
```

---

## 2. Step-by-Step Free Cloud Deployment

### Part A: Repository Structure Preparation
Ensure the unified repository contains the 4 task folders at the root level:
```text
project-root/
|-- .github/workflows/daily_forecast.yml
|-- task1_pipeline/
|-- task3_api_bot/
|-- task4_dashboard/
|-- task5_deploy/
```
Copy `task5_deploy/deploy/daily_forecast.yml` into `.github/workflows/daily_forecast.yml` so GitHub Actions updates `forecasts.csv` daily across all folders automatically.

### Part B: Deploy Task 3 API (Render Free Tier)
1. Sign in to **Render** (https://render.com) with GitHub and click **New + -> Web Service**.
2. Connect your project repository.
3. Configure the service settings:
   - **Name:** `cairo-pm25-api`
   - **Root Directory:** `task3_api_bot`
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type:** `Free`
   - **Health Check Path:** `/health`
4. Add Environment Variables under **Environment**:
   - `FORECASTS_PATH` = `data/forecasts.csv`
   - `ALERT_CONFIG_PATH` = `alert_config.json`
   - `MAX_AGE_DAYS` = `1`
   - `CORS_ORIGINS` = `*`
   - `TELEGRAM_BOT_TOKEN` = `<your-bot-token-from-BotFather>` (optional if running bot separately)
5. Click **Create Web Service** and wait for the build to complete.
6. Verify public endpoints in browser:
   - `https://<your-render-service>.onrender.com/health` -> should return `"status": "ok"`
   - `https://<your-render-service>.onrender.com/docs` -> interactive Swagger UI
   - `https://<your-render-service>.onrender.com/forecast?district=helwan&horizon=1`

### Part C: Deploy Task 4 Dashboard (Streamlit Community Cloud - Free)
1. Sign in to **Streamlit Community Cloud** (https://share.streamlit.io) with GitHub and click **New app**.
2. Select your repository and branch (`main`).
3. Configure app settings:
   - **Main file path:** `task4_dashboard/app.py`
   - **Python version:** `3.11`
4. Click **Deploy!**
5. Once live, verify the public dashboard URL (`https://<your-app>.streamlit.app`):
   - Confirm all 5 districts appear on the Cairo map (`Downtown`, `Helwan`, `Shobra`, `New Cairo`, `6th of October`).
   - Switch between `24h`, `48h`, and `72h` horizons and confirm metrics update cleanly.

### Part D: Telegram Bot Deployment Note
- Telegram polling allows **only one active bot instance per token** (running two copies simultaneously triggers Telegram HTTP `409 Conflict`).
- If Render free tier sleeps after 15 minutes of inactivity, run the Telegram bot either:
  1. As a single always-on background worker (`python -m bot.bot`), OR
  2. Locally on your laptop during the live demo (`python run.py` inside `task3_api_bot/`) with `TELEGRAM_BOT_TOKEN` set in `.env`.

---

## 3. Secrets & Configuration Reference

Never commit tokens or `.env` files to Git. Store all secrets in the hosting platform's environment settings:

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `TELEGRAM_BOT_TOKEN` | For Bot | None | Secret token issued by `@BotFather` on Telegram. |
| `FORECASTS_PATH` | Yes | `data/forecasts.csv` | Path to daily `forecasts.csv` read by Task 3 API/Bot. |
| `ALERT_CONFIG_PATH` | Yes | `alert_config.json` | Path to threshold config (`warning` and `high` cutoffs). |
| `BOT_DB_PATH` | For Bot | `data/bot.db` | SQLite database storing Telegram user subscriptions. |
| `MAX_AGE_DAYS` | No | `1` | Forecasts older than this many days are flagged `stale: true`. |
| `NOTIFY_HOUR_CAIRO` | No | `8` | Hour (Cairo time, 24h) after which daily Telegram alerts dispatch. |
| `BOT_DEFAULT_LANG` | No | `ar` | Default bot language (`ar` or `en`). |
| `CORS_ORIGINS` | No | `*` | Allowed frontend origins for CORS headers. |

**Token Rotation (If Compromised):**
1. Open `@BotFather` in Telegram -> send `/revoke` -> select your bot.
2. Copy the new token and update `TELEGRAM_BOT_TOKEN` in Render Environment Variables (or local `.env`).
3. Trigger a manual redeploy/restart.

---

## 4. How to Redeploy & Update

1. **Routine Code or Data Updates:**
   - Commit and push changes to `main` on GitHub (`git push origin main`).
   - Both Render and Streamlit Community Cloud automatically detect the push and redeploy within 1-2 minutes.
2. **Updating Alert Thresholds Without Retraining:**
   - Edit `alert_config.json` (in `task1_pipeline/models_v2/alert_config.json` and `task3_api_bot/alert_config.json`).
   - Re-run `python task5_deploy/run_chain.py` and push updated `forecasts.csv` and `alert_config.json`.
3. **Manual Redeploy on Render:**
   - Open Render Dashboard -> select `cairo-pm25-api` -> click **Manual Deploy -> Deploy latest commit**.
4. **Manual Reboot on Streamlit Cloud:**
   - Open Streamlit Cloud -> select app -> click **Manage app -> Reboot app**.

---

## 5. Accuracy Monitoring (`check_accuracy.py`)

Run daily or before the demo to compare logged predictions in `forecast_log.csv` against actual observed PM2.5 readings from Open-Meteo:
```bash
python task5_deploy/check_accuracy.py
```
Outputs:
- `task5_deploy/sample_output/accuracy_comparison.csv` (row-by-row predicted vs. observed PM2.5, bias, absolute error, and alert hit/miss status)
- `task5_deploy/sample_output/accuracy_summary.json` (MAE, RMSE, and Mean Bias broken down by horizon and district)

---

## 6. Demo Backup Plan (Local Laptop + Screen Recording)

Free hosting tiers may experience cold starts (30-50 seconds on Render) or venue Wi-Fi drops. Keep this backup ready before the presentation:

1. **Pre-Demo Local Verification (15 minutes prior):**
   - Run `python task5_deploy/run_chain.py` to ensure fresh `forecasts.csv` is present in all folders.
   - Start Task 3 API + Bot locally: `cd task3_api_bot && python run.py` (port `8000`).
   - Start Task 4 Dashboard locally in a second terminal: `cd task4_dashboard && streamlit run app.py` (port `8501`).
2. **Offline Fallback:**
   - If internet is completely unavailable at the venue, run `python task5_deploy/run_chain.py --skip-pipeline` so the API and Dashboard run cleanly off the cached `forecasts.csv`.
3. **Screen Recording Backup:**
   - Record a 2-minute continuous walkthrough video (MP4) showing:
     1. Running `python task5_deploy/run_chain.py` in terminal.
     2. Opening the public Render `/docs` and `/forecast?district=helwan&horizon=1` endpoints.
     3. Navigating the public Streamlit dashboard across 24h / 48h / 72h and clicking districts.
     4. Triggering and receiving a Telegram bot alert on mobile/desktop.
     5. Running `python task5_deploy/check_accuracy.py` to display recent error metrics.
