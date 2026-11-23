"""Response models (they also produce the interactive docs at /docs)."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

AlertLevel = Literal["none", "warning", "high"]


class ForecastItem(BaseModel):
    station: str = Field(examples=["helwan"])
    station_name: str = Field(examples=["Helwan"])
    issue_date: str = Field(description="Day the forecast was made (UTC), ISO date", examples=["2026-11-22"])
    target_date: str = Field(description="Day being forecast (issue_date + horizon)", examples=["2026-11-23"])
    horizon: int = Field(ge=1, le=3, description="Days ahead (1, 2 or 3)")
    pred_pm25: float = Field(description="Predicted PM2.5 in ug/m3")
    risk_prob: float = Field(ge=0, le=1, description="Probability that the day is a health-risk day (PM2.5 above 55.4)")
    alert_level: AlertLevel
    aqi_category: str = Field(examples=["Unhealthy for Sensitive Groups"])


class ForecastResponse(BaseModel):
    district: str
    district_name: str
    issue_date: str
    age_days: int
    stale: bool = Field(description="True when the forecast file is older than MAX_AGE_DAYS; do not present it as current")
    forecasts: list[ForecastItem]


class AlertsResponse(BaseModel):
    issue_date: str
    age_days: int
    stale: bool
    min_level: Literal["warning", "high"]
    count: int
    by_level: dict[str, int]
    thresholds: Optional[dict[str, dict[str, float]]] = Field(
        default=None, description="Probability thresholds per horizon used by the pipeline: risk_prob >= high -> high, >= warning -> warning")
    alerts: list[ForecastItem]


class District(BaseModel):
    id: str
    name_en: str
    name_ar: str


class ForecastsStatus(BaseModel):
    state: Literal["ok", "stale", "missing", "invalid"]
    detail: Optional[str] = None
    issue_date: Optional[str] = None
    age_days: Optional[int] = None
    rows: int = 0
    max_age_days: int


class Health(BaseModel):
    status: Literal["ok", "degraded"] = Field(description="'degraded' when the forecasts file is stale, missing or invalid (the API itself is up)")
    api: str = "up"
    version: str
    server_time_utc: str
    forecasts: ForecastsStatus
    alert_config: Literal["ok", "missing", "invalid"]


class Root(BaseModel):
    name: str
    version: str
    docs: str
    endpoints: list[str]
