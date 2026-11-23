"""Reads forecasts.csv (written daily by the forecast pipeline) and validates it.

* The file is re-read whenever it changes on disk (modification time + size), so a new daily file is picked up
  without restarting the API or the bot.
* Only the rows of the most recent issue_date are served (the pipeline also writes a history file with many days;
  pointing the store at it works too).
* A file that is missing or invalid raises ForecastError instead of serving wrong data.
"""
from __future__ import annotations

import csv
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from .stations import ORDER

LEVEL_RANK = {"none": 0, "warning": 1, "high": 2}
HORIZONS = (1, 2, 3)
REQUIRED_COLUMNS = ("station", "issue_date", "target_date", "horizon", "pred_pm25", "risk_prob", "alert_level", "aqi_category")
CATEGORY_RANK = {"Good": 0, "Moderate": 1, "Unhealthy for Sensitive Groups": 2, "Unhealthy": 3, "Very Unhealthy": 4, "Hazardous": 5}


class ForecastError(Exception):
    """The forecasts file cannot be used. kind is 'missing' or 'invalid'."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass(frozen=True)
class Forecast:
    station: str
    issue_date: date
    target_date: date
    horizon: int
    pred_pm25: float
    risk_prob: float
    alert_level: str
    aqi_category: str

    def to_dict(self) -> dict:
        return {"station": self.station, "issue_date": self.issue_date.isoformat(), "target_date": self.target_date.isoformat(),
                "horizon": self.horizon, "pred_pm25": self.pred_pm25, "risk_prob": self.risk_prob,
                "alert_level": self.alert_level, "aqi_category": self.aqi_category}


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def parse_forecasts(path: Path) -> list[Forecast]:
    """Read and validate a forecasts file. Raises ForecastError."""
    path = Path(path)
    if not path.is_file():
        raise ForecastError("missing", f"forecasts file not found: {path.name}")
    try:
        with path.open(newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            header = [h.strip() for h in (reader.fieldnames or [])]
            missing = [c for c in REQUIRED_COLUMNS if c not in header]
            if missing:
                raise ForecastError("invalid", f"missing column(s): {', '.join(missing)}")
            out: list[Forecast] = []
            seen: set[tuple] = set()
            for n, raw in enumerate(reader, start=2):
                row = {(k or "").strip(): (v or "").strip() for k, v in raw.items()}
                if not any(row.values()):
                    continue                                   # blank line
                try:
                    f = Forecast(
                        station=row["station"],
                        issue_date=date.fromisoformat(row["issue_date"]),
                        target_date=date.fromisoformat(row["target_date"]),
                        horizon=int(row["horizon"]),
                        pred_pm25=float(row["pred_pm25"]),
                        risk_prob=float(row["risk_prob"]),
                        alert_level=row["alert_level"].lower(),
                        aqi_category=row["aqi_category"],
                    )
                except (ValueError, KeyError) as e:
                    raise ForecastError("invalid", f"line {n}: bad value ({e})") from e
                if not f.station:
                    raise ForecastError("invalid", f"line {n}: empty station")
                if f.horizon not in HORIZONS:
                    raise ForecastError("invalid", f"line {n}: horizon must be 1, 2 or 3 (got {f.horizon})")
                if f.alert_level not in LEVEL_RANK:
                    raise ForecastError("invalid", f"line {n}: alert_level must be none, warning or high (got '{f.alert_level}')")
                if not 0.0 <= f.risk_prob <= 1.0:
                    raise ForecastError("invalid", f"line {n}: risk_prob must be between 0 and 1 (got {f.risk_prob})")
                if f.pred_pm25 < 0:
                    raise ForecastError("invalid", f"line {n}: pred_pm25 is negative")
                if f.target_date != f.issue_date + timedelta(days=f.horizon):
                    raise ForecastError("invalid", f"line {n}: target_date is not issue_date + horizon")
                key = (f.station, f.issue_date, f.horizon)
                if key in seen:
                    raise ForecastError("invalid", f"line {n}: duplicate row for {f.station}, {f.issue_date}, horizon {f.horizon}")
                seen.add(key)
                out.append(f)
    except ForecastError:
        raise
    except (OSError, UnicodeDecodeError, csv.Error) as e:
        raise ForecastError("invalid", f"cannot read file: {e}") from e
    if not out:
        raise ForecastError("invalid", "the file has no forecast rows")
    return out


class ForecastStore:
    def __init__(self, path: Path | str, max_age_days: int = 1, today_fn: Optional[Callable[[], date]] = None):
        self.path = Path(path)
        self.max_age_days = max_age_days
        self._today = today_fn or _utc_today
        self._lock = threading.Lock()
        self._cache_key: tuple | None = None
        self._cache: list[Forecast] = []

    # -- loading ---------------------------------------------------------------------------------------------
    def _load(self) -> list[Forecast]:
        with self._lock:
            try:
                st = self.path.stat()
            except OSError:
                self._cache_key, self._cache = None, []
                raise ForecastError("missing", f"forecasts file not found: {self.path.name}") from None
            key = (st.st_mtime_ns, st.st_size)
            if key != self._cache_key:
                self._cache = parse_forecasts(self.path)
                self._cache_key = key
            return self._cache

    def latest(self) -> list[Forecast]:
        """Rows of the most recent issue_date, sorted by district then horizon."""
        rows = self._load()
        newest = max(r.issue_date for r in rows)
        pos = {s: i for i, s in enumerate(ORDER)}
        return sorted((r for r in rows if r.issue_date == newest), key=lambda r: (pos.get(r.station, 99), r.station, r.horizon))

    # -- freshness -------------------------------------------------------------------------------------------
    @property
    def issue_date(self) -> date:
        return self.latest()[0].issue_date

    def age_days(self) -> int:
        return (self._today() - self.issue_date).days

    def is_stale(self) -> bool:
        return self.age_days() > self.max_age_days

    # -- queries ---------------------------------------------------------------------------------------------
    def stations(self) -> list[str]:
        have = {r.station for r in self.latest()}
        return [s for s in ORDER if s in have] + sorted(have - set(ORDER))

    def select(self, station: str | None = None, horizon: int | None = None, min_level: str = "none") -> list[Forecast]:
        floor = LEVEL_RANK[min_level]
        return [r for r in self.latest()
                if (station is None or r.station == station) and (horizon is None or r.horizon == horizon)
                and LEVEL_RANK[r.alert_level] >= floor]

    def status(self) -> dict:
        """State for /health: ok | stale | missing | invalid."""
        try:
            rows = self.latest()
        except ForecastError as e:
            return {"state": e.kind, "detail": e.message, "issue_date": None, "age_days": None, "rows": 0, "max_age_days": self.max_age_days}
        age = (self._today() - rows[0].issue_date).days
        return {"state": "stale" if age > self.max_age_days else "ok", "detail": None, "issue_date": rows[0].issue_date.isoformat(),
                "age_days": age, "rows": len(rows), "max_age_days": self.max_age_days}
