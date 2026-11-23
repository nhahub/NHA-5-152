"""
Task 3 — Spatial Interpolation & Upwind/Downwind Feature
Input : Merged_cleaned_daily_data.csv  (5 districts x 1096 days)
Output: Merged_cleaned_daily_data_task3.csv  (+ new feature columns)
        idw_scatter.png, task3_results.json
"""
import json, itertools
import numpy as np
import pandas as pd
from scipy import stats

IN_CSV   = "Merged_cleaned_daily_data.csv"
OUT_CSV  = "Merged_cleaned_daily_data_task3.csv"
OUT_PNG  = "idw_scatter.png"
OUT_JSON = "task3_results.json"

# ------------------------------------------------------------------ config
# Reference ("actual") series for the IDW check. The CSV has NO raw OpenAQ column,
# so Downtown's bias-corrected PM2.5 is used as the ground truth.
REF_COL, REF_STATION = "pm2_5_corrected", "downtown"
IDW_POWERS = [1, 2, 3]          # p=2 is the headline choice
SECTOR_HALF_ANGLE = 45.0        # degrees, ±45° => 90° downwind cone

# District coordinates (lat, lon) as used in the team's Open-Meteo requests (not stored in the CSV).
COORDS = {
    "downtown":  (30.0444, 31.2357),
    "helwan":    (29.8490, 31.3342),
    "shobra":    (30.1286, 31.2422),
    "new_cairo": (30.0074, 31.4913),
    "october":   (29.9723, 30.9458),
}
SOURCES = {"helwan": "is_downwind_helwan", "shobra": "is_downwind_shobra"}

# ------------------------------------------------------------------ helpers
def haversine_km(a, b):
    R = 6371.0088
    la1, lo1, la2, lo2 = map(np.radians, (*a, *b))
    h = np.sin((la2-la1)/2)**2 + np.cos(la1)*np.cos(la2)*np.sin((lo2-lo1)/2)**2
    return 2*R*np.arcsin(np.sqrt(h))

def bearing_deg(a, b):
    """Initial compass bearing a -> b, degrees clockwise from North [0,360)."""
    la1, lo1, la2, lo2 = map(np.radians, (*a, *b))
    x = np.sin(lo2-lo1)*np.cos(la2)
    y = np.cos(la1)*np.sin(la2) - np.sin(la1)*np.cos(la2)*np.cos(lo2-lo1)
    return (np.degrees(np.arctan2(x, y)) + 360) % 360

def idw_weights(target, others, p):
    d = np.array([haversine_km(COORDS[target], COORDS[o]) for o in others])
    w = 1.0 / d**p
    return w / w.sum()

def metrics(y, yhat):
    m = np.isfinite(y) & np.isfinite(yhat)
    y, yhat = y[m], yhat[m]
    return dict(n=int(m.sum()),
                MAE=float(np.mean(np.abs(y-yhat))),
                RMSE=float(np.sqrt(np.mean((y-yhat)**2))),
                bias=float(np.mean(yhat-y)),
                r=float(stats.pearsonr(y, yhat)[0]) if np.std(yhat) > 0 else float('nan'))

# ------------------------------------------------------------------ load
df = pd.read_csv(IN_CSV)
df["day"] = pd.to_datetime(df["day"])
assert not df.duplicated(["station_id", "day"]).any()
results = {}

# sanity: lag columns really are per-station shifts
chk = df.sort_values(["station_id", "day"]).groupby("station_id")["pm10"].shift(1)
ok = np.isclose(chk.reindex(df.index).dropna(), df.loc[chk.reindex(df.index).dropna().index, "pm10_lag_1d"]).all()
results["pm10_lag_1d_consistent_with_shift"] = bool(ok)

# ================================================================== 1. IDW
wide = df.pivot(index="day", columns="station_id", values=REF_COL).sort_index()
actual = wide[REF_STATION].values
others = [s for s in COORDS if s != REF_STATION]

dist = {o: float(haversine_km(COORDS[REF_STATION], COORDS[o])) for o in others}
results["distances_km_from_downtown"] = dist

idw = {}
for p in IDW_POWERS:
    w = idw_weights(REF_STATION, others, p)
    est = wide[others].values @ w
    idw[p] = dict(weights=dict(zip(others, map(float, w))), **metrics(actual, est))
    if p == 2:
        est_p2 = est
results["idw"] = idw

# baselines
results["baseline_mean_of_4"] = metrics(actual, wide[others].mean(axis=1).values)
results["baseline_nearest_only"] = {o: metrics(actual, wide[o].values) for o in others}
results["baseline_persistence_lag1"] = metrics(actual[1:], actual[:-1])
results["baseline_climatology_mean"] = metrics(actual, np.full_like(actual, actual.mean()))
results["downtown_std"] = float(np.nanstd(actual, ddof=1))

# leave-one-out for every district (is proximity informative everywhere?)
loo = {}
for tgt in COORDS:
    oth = [s for s in COORDS if s != tgt]
    w = idw_weights(tgt, oth, 2)
    loo[tgt] = dict(**metrics(wide[tgt].values, wide[oth].values @ w),
                    std=float(wide[tgt].std()))
results["leave_one_out_idw_p2"] = loo

# seasonal / high-pollution breakdown for Downtown
tmp = pd.DataFrame({"actual": actual, "est": est_p2}, index=wide.index)
tmp["season"] = tmp.index.month.map(lambda m: {12:"winter",1:"winter",2:"winter",3:"spring",4:"spring",5:"spring",
                                               6:"summer",7:"summer",8:"summer"}.get(m,"autumn"))
results["idw_p2_by_season"] = {s: metrics(g.actual.values, g.est.values) for s, g in tmp.groupby("season")}
hi = tmp.actual >= tmp.actual.quantile(0.9)
results["idw_p2_top10pct_days"] = metrics(tmp.actual[hi].values, tmp.est[hi].values)

# plot
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
fig, ax = plt.subplots(figsize=(5.2, 5.2))
ax.scatter(actual, est_p2, s=6, alpha=.35)
lim = [0, max(np.nanmax(actual), np.nanmax(est_p2))*1.03]
ax.plot(lim, lim, "k--", lw=1); ax.set_xlim(lim); ax.set_ylim(lim)
ax.set_xlabel("Downtown actual PM2.5 (pm2_5_corrected)"); ax.set_ylabel("IDW estimate from 4 districts (p=2)")
ax.set_title(f"IDW check  MAE={idw[2]['MAE']:.2f}  r={idw[2]['r']:.3f}")
fig.tight_layout(); fig.savefig(OUT_PNG, dpi=150); plt.close(fig)

# ================================================================== 2. Up/downwind
# Convention check: meteorological wind_dir = direction wind blows FROM, clockwise from N.
# sin = east component, cos = north component of that "from" bearing.
mean_from = (np.degrees(np.arctan2(df.wind_dir_sin.mean(), df.wind_dir_cos.mean())) + 360) % 360
results["mean_wind_from_deg_all"] = float(mean_from)

# per-day wind of each district, indexed by day
ws = df.pivot(index="day", columns="station_id", values="wind_dir_sin")
wc = df.pivot(index="day", columns="station_id", values="wind_dir_cos")
cos_thr = np.cos(np.radians(SECTOR_HALF_ANGLE))

def downwind_flag(src, dst, wind_from="source"):
    """1 if dst lies inside ±45° of the direction the wind blows TOWARD, as seen from src."""
    if src == dst:
        return pd.Series(0, index=ws.index)
    b = np.radians(bearing_deg(COORDS[src], COORDS[dst]))
    wsrc = src if wind_from == "source" else dst
    # unit vector wind blows TOWARD (east, north) = -(sin, cos) of the FROM direction
    tx, ty = -ws[wsrc], -wc[wsrc]
    dot = tx*np.sin(b) + ty*np.cos(b)           # = cos(angle between wind-to and src->dst bearing)
    return (dot >= cos_thr - 1e-12).astype(int)

bear = {s: {d: float(bearing_deg(COORDS[s], COORDS[d])) for d in COORDS if d != s} for s in SOURCES}
results["bearings_deg"] = bear

flags = {}
agree = {}
for src, col in SOURCES.items():
    fl = pd.DataFrame({d: downwind_flag(src, d, "source") for d in COORDS})
    fl_alt = pd.DataFrame({d: downwind_flag(src, d, "receptor") for d in COORDS})
    flags[col] = fl
    agree[col] = {d: float((fl[d] == fl_alt[d]).mean()) for d in COORDS if d != src}

results["downwind_rate_pct"] = {c: (f.mean()*100).round(2).to_dict() for c, f in flags.items()}
results["agreement_source_vs_receptor_wind"] = agree

for col, fl in flags.items():
    long = fl.stack().rename(col).reset_index()
    long.columns = ["day", "station_id", col]
    df = df.merge(long, on=["day", "station_id"], how="left")
    df[col] = df[col].astype(int)

# signal check: does the flag separate pollution levels? (descriptive, not causal)
sig = {}
for src, col in SOURCES.items():
    for d in COORDS:
        if d == src: continue
        g = df[df.station_id == d]
        a, b = g[g[col] == 1], g[g[col] == 0]
        if len(a) < 10: continue
        row = {"n_downwind": int(len(a))}
        for v in ["pm2_5_corrected", "pm10", "sulphur_dioxide", "nitrogen_dioxide"]:
            row[v] = dict(mean_down=float(a[v].mean()), mean_other=float(b[v].mean()),
                          diff=float(a[v].mean()-b[v].mean()),
                          p_mwu=float(stats.mannwhitneyu(a[v], b[v]).pvalue))
        sig[f"{col}->{d}"] = row
results["downwind_signal_check"] = sig


# confounding check: how much of each flag is just "which season / Khamsin"?
conf = {}
for col in SOURCES.values():
    for d in COORDS:
        g = df[df.station_id == d]
        if g[col].sum() < 10: continue
        a = g[g[col] == 1]
        conf[f"{col}->{d}"] = dict(share_in_khamsin_season=float(a.is_Khamsin_season.mean()),
                                   base_share_khamsin=float(g.is_Khamsin_season.mean()),
                                   share_spring=float(a.season.eq("spring").mean()),
                                   share_winter=float(a.season.eq("winter").mean()),
                                   share_summer=float(a.season.eq("summer").mean()),
                                   share_autumn=float(a.season.eq("autumn").mean()))
results["downwind_season_confounding"] = conf

# ================================================================== 3. Interactions
df = df.copy()  # defragment after merges
df["dust_transport_proxy"] = df["wind_speed_10m_mean"] * df["pm10_lag_1d"]
dtr = df["temperature_2m_max"] - df["temperature_2m_min"]
df["inversion_proxy"] = df["relative_humidity_2m_max"] * dtr                      # EXACT task formula
df["inversion_proxy_v2"] = df["relative_humidity_2m_max"] / dtr.clip(lower=1.0)   # physically-signed variant

results["dtr_min"] = float(dtr.min())
feat_corr = {}
for c in ["dust_transport_proxy", "inversion_proxy", "inversion_proxy_v2",
          "is_downwind_helwan", "is_downwind_shobra"]:
    row = {}
    for tgt in ["pm2_5_corrected", "pm10"]:
        m = df[[c, tgt]].dropna()
        row[tgt] = float(stats.spearmanr(m[c], m[tgt])[0])
    feat_corr[c] = row
# comparison against raw ingredients
for c in ["wind_speed_10m_mean", "pm10_lag_1d", "relative_humidity_2m_max"]:
    feat_corr[c] = {t: float(stats.spearmanr(df[[c, t]].dropna()[c], df[[c, t]].dropna()[t])[0])
                    for t in ["pm2_5_corrected", "pm10"]}
feat_corr["dtr"] = {t: float(stats.spearmanr(dtr, df[t])[0]) for t in ["pm2_5_corrected", "pm10"]}
results["spearman_with_targets"] = feat_corr
results["nan_counts_new_cols"] = df[["is_downwind_helwan", "is_downwind_shobra",
                                     "dust_transport_proxy", "inversion_proxy", "inversion_proxy_v2"]].isna().sum().to_dict()

# ------------------------------------------------------------------ save
df["day"] = df["day"].dt.strftime("%Y-%m-%d")
df.to_csv(OUT_CSV, index=False)
json.dump(results, open(OUT_JSON, "w"), indent=2)
print(json.dumps(results, indent=1))
print(df.shape)
