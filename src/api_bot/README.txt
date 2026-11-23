TASK 3 - FORECAST API + TELEGRAM BOT
====================================
Serves the daily forecasts written by the forecast pipeline (Task 1) and sends alerts on Telegram.
It computes nothing itself: everything comes from forecasts.csv (columns agreed on Day 1) and alert levels come ready-made
in its alert_level column (none / warning / high).


ONE COMMAND
-----------
    pip install -r requirements.txt
    python run.py

  -> API on http://127.0.0.1:8000   (interactive docs: http://127.0.0.1:8000/docs)
  -> the Telegram bot starts in the same process if TELEGRAM_BOT_TOKEN is set (otherwise only the API runs).

It runs out of the box on data/forecasts.csv, a copy of the real Task 1 output of 2026-11-22. A forecast older than one day is
reported as "stale" (and the bot sends nothing), so for a live run point it at the pipeline's output:

    set FORECASTS_PATH=C:\path\to\forecasts.csv        (Windows cmd)   |   export FORECASTS_PATH=/path/to/forecasts.csv   (Linux/macOS)

No pipeline at hand? Make a fake file dated today:   python scripts/make_sample_forecasts.py --scenario mixed --out data/forecasts.csv


WHAT IT DOES
------------
API (FastAPI)
  GET /forecast?district=helwan&horizon=1   forecast of one district (horizon optional: 1, 2 or 3)
  GET /districts                            the 5 districts (id + English and Arabic names)
  GET /alerts?level=warning|high            current alerts (warning = warning + high, high = only high) with the thresholds
  GET /health                               service and data health (ok / degraded; forecast file ok / stale / missing / invalid)
  Every forecast/alert response carries "stale": true when the file is older than MAX_AGE_DAYS (default 1 day).
  District names are forgiving: helwan, Helwan, new cairo, 6th of October, حلوان, القاهرة الجديدة ... all work.
  Errors: unknown district 404 (with the valid list), bad horizon/level 422, forecast file missing/invalid 503.

Telegram bot (Arabic and English; the language follows the user's Telegram app language, /lang switches it)
  /subscribe             choose district and level with buttons        /subscribe helwan high   (direct)
  /unsubscribe helwan    or /unsubscribe all                           /status     your subscriptions
  /forecast helwan       forecast now                                  /districts  /help  /lang ar|en
  Levels:  warning = earlier and more alerts (more false alarms);  high = only when a risk day is very likely.
  Daily check: after 08:00 Cairo time (NOTIFY_HOUR_CAIRO) the bot messages every subscriber whose level is reached.
    - one message per user per forecast day (all their districts and horizons together)
    - the same forecast is never sent twice; if the pipeline re-runs the same day and the alerts change, one "updated" message is sent
    - a stale/missing/invalid forecast file is never sent as news (it retries every 30 minutes)
    - users who blocked the bot are removed automatically
    - every alert ends with an honest note: at 48-72 hours the alerts miss many high-pollution days


SET UP THE BOT (5 minutes)  -  details in docs/BOT_GUIDE.md
-----------------------------------------------------------
  1. In Telegram open @BotFather -> /newbot -> choose a name and a username ending in "bot" -> copy the token.
  2. Put it in the environment, NEVER in the code or Git:   set TELEGRAM_BOT_TOKEN=123456:ABC...   (or copy .env.example to .env)
  3. python run.py   (or only the bot:  python -m bot.bot)


CONFIGURATION (environment variables or .env, see .env.example)
---------------------------------------------------------------
  TELEGRAM_BOT_TOKEN   bot token (secret)                                  FORECASTS_PATH     default data/forecasts.csv
  ALERT_CONFIG_PATH    default alert_config.json (only shown by /alerts)    BOT_DB_PATH        default data/bot.db (subscribers)
  MAX_AGE_DAYS         default 1                                            NOTIFY_HOUR_CAIRO  default 8
  BOT_DEFAULT_LANG     ar or en, used when Telegram does not say           CORS_ORIGINS       default * (for the dashboard)


WHAT THIS TASK NEEDS FROM THE OTHER TASKS
-----------------------------------------
  Task 1 (pipeline):  forecasts.csv with exactly these columns, rewritten every day:
        station, issue_date, target_date, horizon, pred_pm25, risk_prob, alert_level, aqi_category
      target_date = issue_date + horizon; alert_level in none/warning/high; risk_prob between 0 and 1. A file that breaks these rules is
      rejected (API answers 503, bot sends nothing) instead of serving wrong numbers.
      Please write the file ATOMICALLY (write forecasts.tmp, then os.replace) so a reader never sees a half-written file.
  Task 2 (alert levels): alert_config.json. The copy in this folder is the one the pipeline uses (models_v2): 
      warning 0.362 / 0.557 / 0.445, high 0.587 / 0.802 / 0.863 for horizons 1 / 2 / 3. The API only displays it.
  Task 4 (dashboard): can call /forecast and /alerts (CORS is open). Show the "stale" flag.
  Task 5 (deployment): API start command  uvicorn app.main:app --host 0.0.0.0 --port $PORT   (needs FORECASTS_PATH to point to the daily file).
      The bot is a separate always-on process (long polling): python -m bot.bot.


TESTS
-----
    pip install -r requirements-dev.txt
    python -m pytest            -> 178 tests (API, forecast-file validation, subscriptions, alert rules, bot commands, Telegram client, scheduler)
  The tests never contact Telegram or the internet: the bot is driven with a fake client. Real sending needs your token.
  No Telegram? See the whole bot in action offline:   python scripts/demo_bot_offline.py [--lang ar]   (output saved in sample_output/)


FOLDER MAP
----------
  run.py                  starts API (+ bot)                      alert_config.json   thresholds used by the pipeline (display only)
  app/                    API: main.py (endpoints), forecast_store.py (read + validate forecasts.csv), stations.py (names), config.py
  bot/                    bot.py (commands, buttons, polling, scheduler), notifier.py (daily check), subscribers.py (SQLite),
                          messages.py (Arabic/English text), telegram_api.py (HTTP client), testing.py (fake client)
  scripts/                make_sample_forecasts.py, demo_bot_offline.py, run_daily_check.py (cron-friendly), make_api_examples.py
  tests/                  pytest suite
  data/forecasts.csv      real sample from Task 1 (2026-11-22)      sample_data/   Task 1 sample + forecast log
  sample_output/          api_examples.txt, bot_demo_transcript_en.txt / _ar.txt
  docs/                   API_REFERENCE.md, BOT_GUIDE.md


KNOWN LIMITS (honest list)
--------------------------
  - The bot could not be tested against the real Telegram servers from where it was built: sending, buttons and the polling loop are
    covered by tests with a fake client; the first run with a real token is the final check (see docs/BOT_GUIDE.md, "First run checklist").
  - The bot uses long polling, so it must stay running to answer commands; free hosts that sleep will pause it. Fallback for the demo: run it on a laptop.
  - Subscriber data (Telegram chat ids) is stored in data/bot.db: do not commit it.
  - Alerts are only as good as the model behind them: at 48-72 hours they miss many high-pollution days (Task 1/2 documentation).
  - Health advice texts follow general EPA-style guidance and are not medical advice.
