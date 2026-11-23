# Telegram bot guide

## 1. Create the bot and get the token (once)

1. Open Telegram, search **@BotFather**, send `/newbot`.
2. Choose a display name (e.g. *Cairo Air Alerts*) and a username that ends with `bot` (e.g. `CairoAirAlertsBot`).
3. BotFather replies with a **token** like `123456789:AAH...`. It is a password: anyone with it controls the bot.
4. Optional polish in BotFather: `/setdescription`, `/setabouttext`, `/setuserpic`, and `/setcommands` with:
   ```
   subscribe - choose district and alert level
   unsubscribe - stop alerts
   status - my subscriptions
   forecast - forecast for a district
   districts - list of districts
   lang - language / اللغة
   help - all commands
   ```

## 2. Give the token to the program (never put it in code or Git)

| System | Command |
|---|---|
| Windows cmd | `set TELEGRAM_BOT_TOKEN=123456789:AAH...` |
| Windows PowerShell | `$env:TELEGRAM_BOT_TOKEN="123456789:AAH..."` |
| Linux / macOS | `export TELEGRAM_BOT_TOKEN=123456789:AAH...` |
| any | copy `.env.example` to `.env` and fill `TELEGRAM_BOT_TOKEN=` (`.env` is in `.gitignore`) |

If the token ever leaks (screenshot, commit, chat): BotFather → `/revoke`, then use the new one.

## 3. Run

```
pip install -r requirements.txt
python run.py            # API + bot together
python -m bot.bot        # bot only
```
The bot prints `Bot @YourBotName is running...` after it has verified the token with Telegram. Stop with `Ctrl+C`.
Run **one** copy only: two copies polling with the same token fight each other (Telegram error 409).

### First run checklist (the one thing that cannot be tested without a token)

1. `python run.py`, open your bot in Telegram, send `/start` → welcome message.
2. `/subscribe` → district buttons → level buttons → "Subscribed".
3. `/forecast helwan` → forecast lines; compare with `http://127.0.0.1:8000/forecast?district=helwan`.
4. Force an alert to see a real message: `python scripts/make_sample_forecasts.py --scenario severe --out data/forecasts.csv`, then (from another terminal, with the token set) `python scripts/run_daily_check.py` → your alert arrives. Run it again → nothing is sent (no duplicates).
5. `/lang en` and `/lang ar` → texts switch. `/unsubscribe all` → removed.

## 4. Commands

| Command | What it does |
|---|---|
| `/start`, `/help` | welcome and command list |
| `/subscribe` | buttons: district (or *all districts*), then level |
| `/subscribe helwan high` | direct form. Level `warning` (default) or `high`; also `تحذير` / `خطر`. District: id, English or Arabic name, or `all` / `الكل` |
| `/unsubscribe helwan` | stop one district; `/unsubscribe all` (or `/stop all`) stops everything |
| `/status` | your subscriptions |
| `/forecast helwan` | the 3 horizons of a district, with a note if the data is stale |
| `/districts` | list with the ids to type |
| `/lang ar` · `/lang en` | language. New users get the language of their Telegram app (Arabic/English), otherwise `BOT_DEFAULT_LANG` |

The bot only answers in private chats (alerts are personal); group messages are ignored.

## 5. Alert rules

| Rule | Behaviour |
|---|---|
| Levels | `warning` subscribers get warning **and** high alerts; `high` subscribers only high |
| When | once a day after `NOTIFY_HOUR_CAIRO` (default 08:00 Cairo time), from inside the running bot |
| What | one message per user per forecast day, with every district and horizon that reached their level, a health-advice line for the worst category, and the reliability note |
| No alert | nothing is sent (no "all clear" messages) |
| Duplicates | the same forecast is never sent twice; if the pipeline re-runs the same day and the alerts change, one message marked *Updated forecast* is sent |
| Old data | if the forecast file is stale/missing/invalid nothing is sent; the check is retried every 30 minutes until a fresh file appears |
| Blocked bot | the user's subscriptions are removed automatically |
| Failures | a failed send is not recorded as sent, so the next run retries it |

Example (English, `mixed` scenario of the offline demo):

```
⚠️ Air-quality alert (forecast issued 22 Nov)

📍 Helwan
• Tomorrow (23 Nov): 🔴 High risk - PM2.5 ≈ 64 µg/m³ (Unhealthy), risk 74%

📍 Shobra
• Tomorrow (23 Nov): ⚠️ Warning - PM2.5 ≈ 55 µg/m³ (Unhealthy for Sensitive Groups), risk 40%
• In 2 days (24 Nov): ⚠️ Warning - PM2.5 ≈ 59 µg/m³ (Unhealthy), risk 58%
• In 3 days (25 Nov): ⚠️ Warning - PM2.5 ≈ 60 µg/m³ (Unhealthy), risk 50%

💡 Everyone should reduce long or heavy outdoor activity; sensitive groups should stay indoors if possible.
ℹ️ Forecasts are estimates. At 48-72 hours the alerts miss many high-pollution days.
Change: /status · Stop: /unsubscribe all
```
Full transcripts in both languages: `sample_output/bot_demo_transcript_en.txt` and `_ar.txt` (regenerate with `python scripts/demo_bot_offline.py`).

## 6. Ways to run the daily check

1. **Built in (recommended):** the running bot does it by itself (section 5). Nothing else to configure.
2. **Cron / Task Scheduler** on the same machine as the bot: `python scripts/run_daily_check.py` (exit code 0 = ok, 2 = forecast stale/invalid). Use `--dry-run` to preview without sending. Both ways share `data/bot.db`, so a message is not sent twice if both run.
3. GitHub Actions is **not** suitable for sending, because the subscribers database lives on the bot's machine.

## 7. Hosting notes

* The bot uses **long polling** (it keeps asking Telegram for new messages), so it needs a process that stays alive. Free web hosts that sleep after inactivity will pause it; use an always-on worker, a small VPS, or run it on a laptop for the demo.
* `data/bot.db` (SQLite) holds the subscribers' Telegram chat ids: keep it on a persistent disk and out of Git. Losing it only means users must `/subscribe` again.
* The forecasts file must be readable by the bot's machine (same `FORECASTS_PATH` as the API).

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `TELEGRAM_BOT_TOKEN is not set` | set the variable in the same terminal you run Python from |
| `Cannot reach Telegram or the token is wrong` | check the token (no spaces/quotes), the internet connection, or revoke and re-issue it |
| Bot silent, log shows `409 Conflict` | another copy of the bot is running with the same token: stop it |
| Subscribers get nothing | `/health` of the API: is `forecasts.state` `stale`/`invalid`? Run `python scripts/run_daily_check.py --dry-run` to see why |
| Alert not repeated | by design (already sent today). Delete the subscription row or use another chat to retest, or use `make_sample_forecasts.py --date` with a different day |
| Time of the daily send looks wrong | it uses Cairo time (needs the `tzdata` package on Windows, included in requirements.txt) |
