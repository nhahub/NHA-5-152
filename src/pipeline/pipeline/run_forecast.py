import json, sys
from pathlib import Path
import joblib
import numpy as np, pandas as pd
import features as fe

MODELS = Path("models_v2")
OUT, LOG = Path("forecasts.csv"), Path("forecast_log.csv")
REG = {1: "xgboost", 2: "lightgbm", 3: "lightgbm"}
BINS = [-np.inf, 12, 35.4, 55.4, 150.4, 250.4, np.inf]
LABELS = ["Good", "Moderate", "Unhealthy for Sensitive Groups",
          "Unhealthy", "Very Unhealthy", "Hazardous"]


def alert(p, cfg):
    return "high" if p >= cfg["high"] else "warning" if p >= cfg["warning"] else "none"


def run(issue_date=None):
    t = pd.Timestamp(issue_date or pd.Timestamp.now(tz="UTC").tz_localize(None)).normalize()
    hourly = {s: fe.fetch_hourly(s) for s in fe.COORDS}
    X = fe.build_features(hourly, t)
    cfg = json.load(open(MODELS / "alert_config.json"))

    rows = []
    for h in (1, 2, 3):
        reg = joblib.load(MODELS / f"pm25_reg_t{h}_{REG[h]}.pkl")
        clf = joblib.load(MODELS / f"pm25_clf_t{h}_xgboost.pkl")
        pm = np.clip(reg.predict(X), 0, None)
        pr = clf.predict_proba(X)[:, 1]
        for s, a, b in zip(X.index, pm, pr):
            rows.append(dict(station=s, issue_date=t.date(),
                             target_date=(t + pd.Timedelta(days=h)).date(), horizon=h,
                             pred_pm25=round(float(a), 1), risk_prob=round(float(b), 3),
                             alert_level=alert(b, cfg[str(h)]),
                             aqi_category=str(pd.cut([a], BINS, labels=LABELS)[0])))
    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)

    if LOG.exists():                      # same day run twice -> replace, never duplicate
        old = pd.read_csv(LOG)
        old = old[old["issue_date"] != str(t.date())]
        out = pd.concat([old, out], ignore_index=True)
    out.to_csv(LOG, index=False)
    return pd.read_csv(OUT)


if __name__ == "__main__":
    print(run(sys.argv[1] if len(sys.argv) > 1 else None).to_string(index=False))