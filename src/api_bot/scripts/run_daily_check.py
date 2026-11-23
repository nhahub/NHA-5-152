"""Run the daily alert check once (for cron / GitHub Actions):  python scripts/run_daily_check.py [--dry-run]"""
import _path  # noqa: F401
import sys

from bot.notifier import main

if __name__ == "__main__":
    sys.exit(main())
