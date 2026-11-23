"""Settings, read from environment variables (and an optional .env file next to this folder).

Nothing secret lives in the code: the Telegram token comes from TELEGRAM_BOT_TOKEN.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: Path | None = None) -> None:
    """Tiny .env reader (KEY=VALUE per line). Real environment variables win over the file."""
    path = path or BASE_DIR / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _path(name: str, default: str) -> Path:
    p = Path(os.environ.get(name, default))
    return p if p.is_absolute() else BASE_DIR / p


@dataclass(frozen=True)
class Settings:
    forecasts_path: Path          # forecasts.csv written by the forecast pipeline (Task 1)
    alert_config_path: Path       # alert_config.json used by the pipeline (thresholds, for display only)
    bot_db_path: Path             # SQLite file with Telegram subscribers
    max_age_days: int             # forecasts older than this many days are "stale"
    telegram_token: str | None
    default_lang: str             # "ar" or "en"
    notify_hour_cairo: int        # the bot sends the daily alerts after this hour (Cairo time)
    cors_origins: tuple[str, ...]


def get_settings() -> Settings:
    """Read the settings now (so tests can change the environment)."""
    load_dotenv()
    lang = os.environ.get("BOT_DEFAULT_LANG", "ar").lower()
    return Settings(
        forecasts_path=_path("FORECASTS_PATH", "data/forecasts.csv"),
        alert_config_path=_path("ALERT_CONFIG_PATH", "alert_config.json"),
        bot_db_path=_path("BOT_DB_PATH", "data/bot.db"),
        max_age_days=int(os.environ.get("MAX_AGE_DAYS", "1")),
        telegram_token=os.environ.get("TELEGRAM_BOT_TOKEN") or None,
        default_lang=lang if lang in ("ar", "en") else "ar",
        notify_hour_cairo=int(os.environ.get("NOTIFY_HOUR_CAIRO", "8")),
        cors_origins=tuple(o.strip() for o in os.environ.get("CORS_ORIGINS", "*").split(",") if o.strip()),
    )
