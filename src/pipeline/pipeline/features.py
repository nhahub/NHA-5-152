import time, requests,json
import numpy as np, pandas as pd
import splitting as sp

COORDS = {"downtown": (30.0444, 31.2357), "helwan": (29.8490, 31.3342),
          "shobra": (30.1286, 31.2422), "new_cairo": (30.0074, 31.4913),
          "october": (29.9723, 30.9458)}
WEATHER = ["temperature_2m", "relative_humidity_2m", "surface_pressure",
           "precipitation", "wind_speed_10m", "wind_direction_10m"]
AQ = ["pm10", "pm2_5", "uv_index", "carbon_monoxide", "nitrogen_dioxide",
      "sulphur_dioxide", "ozone"]
POLL = ["pm10", "pm2_5_corrected", "carbon_monoxide", "nitrogen_dioxide",
        "sulphur_dioxide", "ozone"]
PM25_FACTOR = 2.18
RAMADAN = [("2023-03-23", "2023-04-20"), ("2024-03-11", "2024-04-09"),
           ("2025-03-01", "2025-03-29"), ("2026-02-18", "2026-03-19"),
           ("2027-02-08", "2027-03-09")]          # 2027 approximate
SOURCES = {"helwan": "is_downwind_helwan", "shobra": "is_downwind_shobra"}
URL_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
URL_FORECAST = "https://api.open-meteo.com/v1/forecast"
URL_AQ = "https://air-quality-api.open-meteo.com/v1/air-quality"


def _get(url, params):
    for k in range(4):
        try:
            r = requests.get(url, params=params, timeout=60)
            r.raise_for_status()
            return r.json()
        except Exception:
            if k == 3:
                raise
            time.sleep(2 * (k + 1))


def fetch_hourly(station, start=None, end=None):
    """start/end given -> past dates (used for testing). None -> live (last 10 days + today)."""
    lat, lon = COORDS[station]
    base = dict(latitude=lat, longitude=lon, timezone="GMT")
    if start:
        span, w_url = dict(start_date=start, end_date=end), URL_ARCHIVE
    else:
        span, w_url = dict(past_days=10, forecast_days=2), URL_FORECAST
    w = _get(w_url, dict(base, hourly=",".join(WEATHER), **span))
    a = _get(URL_AQ, dict(base, hourly=",".join(AQ), **span))
    out = pd.DataFrame(w["hourly"]).merge(pd.DataFrame(a["hourly"]), on="time", how="outer")
    out["time"] = pd.to_datetime(out["time"])
    return out


def _daily(h):
    h = h.copy()
    h["day"] = h["time"].dt.floor("D")
    rad = np.radians(h["wind_direction_10m"])
    h["s"], h["c"] = np.sin(rad), np.cos(rad)
    g = h.groupby("day")
    d = pd.DataFrame(index=g.size().index)
    for v in ["temperature_2m", "relative_humidity_2m", "surface_pressure",
              "precipitation", "wind_speed_10m"]:
        for stat in ["mean", "max", "min", "std"]:
            d[f"{v}_{stat}"] = g[v].agg(stat)
    ang = np.degrees(np.arctan2(g["s"].mean(), g["c"].mean())) % 360    # vector mean
    d["wind_dir_sin"], d["wind_dir_cos"] = np.sin(np.radians(ang)), np.cos(np.radians(ang))
    d["uv_index"] = g["uv_index"].mean()
    for p in ["pm10", "carbon_monoxide", "nitrogen_dioxide", "sulphur_dioxide", "ozone"]:
        d[p] = g[p].mean()
    d["pm2_5_corrected"] = g["pm2_5"].mean() * PM25_FACTOR
    for c in POLL + ["uv_index", "relative_humidity_2m_mean", "relative_humidity_2m_max",
                     "relative_humidity_2m_min", "wind_speed_10m_mean", "wind_speed_10m_max",
                     "wind_speed_10m_min"]:
        d[c] = d[c].clip(lower=0)
    d = d.asfreq("D")
    for p in POLL:
        for L in (1, 2, 3, 7):
            d[f"{p}_lag_{L}d"] = d[p].shift(L)
        for w in (3, 7):
            d[f"{p}_roll_avg_{w}d"] = d[p].shift(1).rolling(w, min_periods=1).mean()
    return d


def _bearing(a, b):
    la1, lo1, la2, lo2 = map(np.radians, (*a, *b))
    x = np.sin(lo2 - lo1) * np.cos(la2)
    y = np.cos(la1) * np.sin(la2) - np.sin(la1) * np.cos(la2) * np.cos(lo2 - lo1)
    return (np.degrees(np.arctan2(x, y)) + 360) % 360


def build_features(hourly, issue_date, features_json="models_v2/features_76.json",
                   static_csv="models_v2/station_static.csv"):
    """hourly: {station: hourly DataFrame}. Returns one row per district, columns = the 76 features."""
    t = pd.Timestamp(issue_date).normalize()
    daily = {s: _daily(h) for s, h in hourly.items()}
    static = pd.read_csv(static_csv, index_col=0)
    month = t.month
    season = ("winter" if month in (12, 1, 2) else "spring" if month in (3, 4, 5)
              else "summer" if month in (6, 7, 8) else "autumn")
    rows = {}
    for s, d in daily.items():
        r = d.loc[t].to_dict()
        for src, col in SOURCES.items():
            if s == src:
                r[col] = 0
            else:
                b = np.radians(_bearing(COORDS[src], COORDS[s]))
                tx, ty = -daily[src].loc[t, "wind_dir_sin"], -daily[src].loc[t, "wind_dir_cos"]
                r[col] = int(tx * np.sin(b) + ty * np.cos(b) >= np.cos(np.radians(45)) - 1e-12)
        dtr = r["temperature_2m_max"] - r["temperature_2m_min"]
        r["dust_transport_proxy"] = r["wind_speed_10m_mean"] * r["pm10_lag_1d"]
        r["inversion_proxy"] = r["relative_humidity_2m_max"] * dtr
        r["inversion_proxy_v2"] = r["relative_humidity_2m_max"] / max(dtr, 1.0)
        r["is_weekend"] = int(t.dayofweek in (4, 5))
        r["month"] = month
        r["is_Khamsin_season"] = int(month in (3, 4, 5))
        r["is_Ramadan"] = int(any(pd.Timestamp(a) <= t <= pd.Timestamp(b) for a, b in RAMADAN))
        r["day_of_week_enc"] = sp.DOW_MAP[t.day_name()]
        r["season_enc"] = sp.SEASON_MAP[season]
        r["station_id_encoded"] = sp.STATION_MAP[s]
        r.update(static.loc[s].to_dict())
        rows[s] = r
    feats = json.load(open(features_json))
    X = pd.DataFrame(rows).T[feats].astype(float)
    assert not X.isna().any().any(), X.columns[X.isna().any()].tolist()
    return X