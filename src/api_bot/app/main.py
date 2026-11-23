"""FastAPI service: serves the daily forecasts.csv (it computes nothing itself).

Run:  uvicorn app.main:app --port 8000        (interactive docs at /docs)
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import Settings, get_settings
from .forecast_store import Forecast, ForecastError, ForecastStore, LEVEL_RANK
from .schemas import AlertsResponse, District, ForecastItem, ForecastResponse, Health, Root
from .stations import ORDER, STATIONS, display_name, resolve_station

DESCRIPTION = """
Daily PM2.5 forecasts for 5 Cairo districts (1, 2 and 3 days ahead) and the health-risk alert level.

* The data comes from `forecasts.csv`, written once a day by the forecast pipeline. The API re-reads the file when it changes.
* `alert_level` is **none / warning / high**. Alerts are guidance: at 48-72 hours they miss many high-pollution days.
* Check `stale` in every response: if it is true the forecast is older than expected and must not be shown as current.
"""


def read_thresholds(path: Path) -> tuple[Optional[dict], str]:
    """Thresholds from alert_config.json -> (dict or None, 'ok' | 'missing' | 'invalid')."""
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        return {str(h): {"warning": float(v["warning"]), "high": float(v["high"])} for h, v in cfg.items()}, "ok"
    except FileNotFoundError:
        return None, "missing"
    except (ValueError, KeyError, TypeError, AttributeError):
        return None, "invalid"


def _item(f: Forecast) -> ForecastItem:
    return ForecastItem(**f.to_dict(), station_name=display_name(f.station, "en"))


def create_app(store: ForecastStore | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = store or ForecastStore(settings.forecasts_path, settings.max_age_days)
    app = FastAPI(title="Cairo PM2.5 Forecast API", version=__version__, description=DESCRIPTION)
    app.state.store, app.state.settings = store, settings
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET"], allow_headers=["*"])

    def rows_or_503(request: Request, **kw) -> tuple[ForecastStore, list[Forecast]]:
        st: ForecastStore = request.app.state.store
        try:
            return st, st.select(**kw)
        except ForecastError as e:
            raise HTTPException(status_code=503, detail=f"Forecasts are not available ({e.kind}): {e.message}")

    @app.get("/", response_model=Root, summary="API index")
    def root() -> Root:
        return Root(name="Cairo PM2.5 Forecast API", version=__version__, docs="/docs",
                    endpoints=["/forecast?district=helwan&horizon=1", "/districts", "/alerts?level=warning", "/health"])

    @app.get("/health", response_model=Health, summary="Service and data health")
    def health(request: Request) -> Health:
        st: ForecastStore = request.app.state.store
        fs = st.status()
        _, cfg_state = read_thresholds(request.app.state.settings.alert_config_path)
        return Health(status="ok" if fs["state"] == "ok" and cfg_state != "invalid" else "degraded", version=__version__,
                      server_time_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"), forecasts=fs, alert_config=cfg_state)

    @app.get("/districts", response_model=list[District], summary="The districts covered")
    def districts() -> list[District]:
        return [District(id=s, name_en=STATIONS[s]["en"], name_ar=STATIONS[s]["ar"]) for s in ORDER]

    @app.get("/forecast", response_model=ForecastResponse, summary="Forecast for one district")
    def forecast(request: Request,
                 district: str = Query(description="District id or name: downtown, helwan, new_cairo, october, shobra (English or Arabic names accepted)"),
                 horizon: Optional[int] = Query(default=None, ge=1, le=3, description="1, 2 or 3 days ahead; omit for all three")) -> ForecastResponse:
        sid = resolve_station(district)
        if sid is None:
            raise HTTPException(status_code=404, detail=f"Unknown district '{district}'. Valid districts: {', '.join(ORDER)}")
        st, rows = rows_or_503(request, station=sid, horizon=horizon)
        if not rows:
            raise HTTPException(status_code=404, detail=f"No forecast available for district '{sid}'")
        age = st.age_days()
        return ForecastResponse(district=sid, district_name=display_name(sid, "en"), issue_date=rows[0].issue_date.isoformat(),
                                age_days=age, stale=age > st.max_age_days, forecasts=[_item(r) for r in rows])

    @app.get("/alerts", response_model=AlertsResponse, summary="Current warnings and high-risk alerts")
    def alerts(request: Request,
               level: Literal["warning", "high"] = Query(default="warning", description="Minimum level: 'warning' returns warning + high, 'high' only high"),
               district: Optional[str] = Query(default=None, description="Only this district"),
               horizon: Optional[int] = Query(default=None, ge=1, le=3)) -> AlertsResponse:
        sid = None
        if district is not None:
            sid = resolve_station(district)
            if sid is None:
                raise HTTPException(status_code=404, detail=f"Unknown district '{district}'. Valid districts: {', '.join(ORDER)}")
        st, rows = rows_or_503(request, station=sid, horizon=horizon, min_level=level)
        rows = sorted(rows, key=lambda r: (-LEVEL_RANK[r.alert_level], r.horizon, ORDER.index(r.station) if r.station in ORDER else 99))
        age = st.age_days()
        thresholds, _ = read_thresholds(request.app.state.settings.alert_config_path)
        counts = {"warning": sum(r.alert_level == "warning" for r in rows), "high": sum(r.alert_level == "high" for r in rows)}
        return AlertsResponse(issue_date=st.issue_date.isoformat(), age_days=age, stale=age > st.max_age_days, min_level=level,
                              count=len(rows), by_level=counts, thresholds=thresholds, alerts=[_item(r) for r in rows])

    return app


app = create_app()
