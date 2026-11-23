"""
splitting.py - Week 3, Task 1: data-splitting protocol for the PM2.5 panel dataset.

Everything Tasks 2-6 need to get *identical* data splits, targets and feature sets:

    prepare_dataset(source)        raw parquet/DataFrame -> clean modelling frame (NaN rows dropped,
                                   targets t+1/t+2/t+3 created, station_id encoded)
    PanelTimeSeriesSplit           rolling-origin CV that splits by calendar date (all 5 districts
                                   always on both sides of every fold), with a purge gap = horizon
    get_cv_splits(df, horizon)     convenience wrapper -> list of (train_idx, test_idx)
    get_fold(df, fold, horizon)    X/y for one fold (regression or health-risk classification)
    feature_columns(df, ...)       the one agreed-upon feature list
    class_balance(df, horizon)     positive rate + scale_pos_weight per fold (and per district)
    validate(df)                   leakage / integrity self-checks (run it after any change)

Quick start
-----------
    import splitting as sp
    df = sp.prepare_dataset("daily_features_v2.parquet")
    for k, (tr, te) in enumerate(sp.get_cv_splits(df, horizon=2)):
        fold = sp.get_fold(df, k, horizon=2, task="reg")
        model.fit(fold["X_train"], fold["y_train"]); ...

Conventions
-----------
* A row is (district, day t). Its features use information available at the END of day t.
* target_{h}d  = pm2_5_corrected on day t+h        (h = 1, 2, 3)
* target_risk_{h}d = is_health_risk on day t+h     (primary classification target)
* The input DataFrame passed around is the one returned by prepare_dataset(); fold indices are
  positional (use df.iloc[idx]) and, because prepare_dataset() resets the index, equal to df.loc labels.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------------------
# Constants (single source of truth - do not redefine in notebooks)
# --------------------------------------------------------------------------------------
DATE_COL = "day"
STATION_COL = "station_id"
PM_COL = "pm2_5_corrected"
HORIZONS = (1, 2, 3)

# Fixed alphabetical mapping so encodings never depend on data order.
STATION_MAP = {"downtown": 0, "helwan": 1, "new_cairo": 2, "october": 3, "shobra": 4}
SEASON_MAP = {"winter": 0, "spring": 1, "summer": 2, "autumn": 3}
DOW_MAP = {"Monday": 0, "Tuesday": 1, "Wednesday": 2, "Thursday": 3,
           "Friday": 4, "Saturday": 5, "Sunday": 6}
# aqi_category -> ordinal.  Only these 4 exist in the data (no "Good", no "Hazardous").
AQI_MAP = {"Moderate": 0, "Unhealthy for Sensitive Groups": 1, "Unhealthy": 2, "Very Unhealthy": 3}

# Columns describing the CURRENT day's outcome (deterministic functions of today's PM2.5).
CURRENT_DAY_OUTCOME_COLS = ["pm2_5_corrected", "aqi_category", "is_health_risk", "aqi_class"]

# Lag/rolling columns that must be strictly past-only (checked in validate()).
N_DROP_HEAD = 7  # longest lag in the data is 7 days


# --------------------------------------------------------------------------------------
# 1. Loading
# --------------------------------------------------------------------------------------
def _to_datetime(s: pd.Series) -> pd.Series:
    """Parse the `day` column whatever way the parquet reader hands it over."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s).dt.normalize()
    if pd.api.types.is_numeric_dtype(s):  # epoch integers; infer unit from magnitude
        m = float(np.abs(s.iloc[0]))
        unit = "ns" if m > 1e17 else "us" if m > 1e14 else "ms" if m > 1e11 else "s"
        return pd.to_datetime(s, unit=unit).dt.normalize()
    return pd.to_datetime(s).dt.normalize()


def load_raw(source) -> pd.DataFrame:
    """Read daily_features_v2 (path or DataFrame); sort by (district, day); sanity-check the panel."""
    df = pd.read_parquet(source) if isinstance(source, (str, bytes)) or hasattr(source, "__fspath__") \
        else source.copy()
    df[DATE_COL] = _to_datetime(df[DATE_COL])
    df = df.sort_values([STATION_COL, DATE_COL]).reset_index(drop=True)
    assert not df.duplicated([STATION_COL, DATE_COL]).any(), "duplicate (district, day) rows"
    gaps = df.groupby(STATION_COL)[DATE_COL].diff().dropna().dt.days
    assert (gaps == 1).all(), "district calendars must be complete daily series (no gaps)"
    assert df.groupby(STATION_COL)[DATE_COL].agg(["min", "max"]).nunique().max() == 1, \
        "all districts must cover the same date range"
    unknown = set(df[STATION_COL]) - set(STATION_MAP)
    assert not unknown, f"unmapped districts: {unknown}"
    return df


# --------------------------------------------------------------------------------------
# 2. NaN handling
# --------------------------------------------------------------------------------------
def drop_nan_head(df: pd.DataFrame, n_drop: int = N_DROP_HEAD):
    """Drop the first `n_drop` days of every district.

    All NaNs in the raw file are lag / rolling / dust_transport_proxy warm-up values in the first
    7 days (2023-09-01..07) of each district (lag_7d is the longest). Dropping them means no
    imputation is needed. Returns (clean_df, report_df).
    """
    before = df.groupby(STATION_COL).size().rename("rows_before")
    nan_before = df.isna().any(axis=1).groupby(df[STATION_COL]).sum().rename("rows_with_nan_before")
    rank = df.groupby(STATION_COL).cumcount()
    out = df[rank >= n_drop].reset_index(drop=True)
    if out.isna().any().any():
        bad = out.columns[out.isna().any()].tolist()
        raise AssertionError(f"NaNs remain after dropping head rows: {bad}")
    after = out.groupby(STATION_COL).size().rename("rows_after")
    rep = pd.concat([before, nan_before, after], axis=1)
    rep["rows_dropped"] = rep.rows_before - rep.rows_after
    rep["first_day"] = out.groupby(STATION_COL)[DATE_COL].min().dt.date
    rep["last_day"] = out.groupby(STATION_COL)[DATE_COL].max().dt.date
    return out, rep


# --------------------------------------------------------------------------------------
# 3. Encodings
# --------------------------------------------------------------------------------------
def encode_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add station_id_encoded (0-4), station one-hots (for LSTM), calendar and AQI encodings."""
    df = df.copy()
    df["station_id_encoded"] = df[STATION_COL].map(STATION_MAP).astype("int64")
    for name, code in STATION_MAP.items():                     # one-hot, fixed column order
        df[f"station_{name}"] = (df["station_id_encoded"] == code).astype("int64")
    df["day_of_week_enc"] = df["day_of_week"].map(DOW_MAP).astype("int64")   # Mon=0 .. Sun=6
    df["season_enc"] = df["season"].map(SEASON_MAP).astype("int64")
    df["aqi_class"] = df["aqi_category"].map(AQI_MAP).astype("int64")        # secondary target (current day)
    assert df[["day_of_week_enc", "season_enc", "aqi_class"]].notna().all().all(), "unmapped category"
    return df


# --------------------------------------------------------------------------------------
# 4. Targets
# --------------------------------------------------------------------------------------
def add_targets(df: pd.DataFrame, horizons=HORIZONS) -> pd.DataFrame:
    """Create forward targets per district (never across districts).

    target_{h}d       PM2.5 on day t+h             (regression)
    target_risk_{h}d  is_health_risk on day t+h    (binary classification, PRIMARY)
    target_aqi_{h}d   aqi_class on day t+h         (4-level ordinal, secondary)
    The last h rows of each district get NaN (their t+h is beyond the data).
    """
    df = df.sort_values([STATION_COL, DATE_COL]).copy()
    g = df.groupby(STATION_COL)
    for h in horizons:
        df[f"target_{h}d"] = g[PM_COL].shift(-h)
        df[f"target_risk_{h}d"] = g["is_health_risk"].shift(-h)
        df[f"target_aqi_{h}d"] = g["aqi_class"].shift(-h)
    return df


def prepare_dataset(source, horizons=HORIZONS, trim_tail: bool = True, return_report: bool = False):
    """raw -> modelling frame.

    Steps: load/sort -> drop first 7 days per district -> encode -> add targets -> trim the last
    max(horizons) days per district so that t+1, t+2 and t+3 share EXACTLY the same rows (this makes
    horizon-degradation comparisons apples-to-apples). Result: 1086 days x 5 districts = 5430 rows.
    """
    raw = load_raw(source)
    df, nan_report = drop_nan_head(raw)
    df = encode_columns(df)
    df = add_targets(df, horizons)
    if trim_tail:
        tcols = [c for c in df.columns if c.startswith("target_")]
        df = df.dropna(subset=tcols).reset_index(drop=True)
        nan_report["rows_final"] = df.groupby(STATION_COL).size()
        nan_report["rows_trimmed_tail"] = nan_report["rows_after"] - nan_report["rows_final"]
    for h in horizons:  # integer labels for classifiers
        df[f"target_risk_{h}d"] = df[f"target_risk_{h}d"].astype("int64")
        df[f"target_aqi_{h}d"] = df[f"target_aqi_{h}d"].astype("int64")
    return (df, nan_report) if return_report else df


# --------------------------------------------------------------------------------------
# 5. Feature list
# --------------------------------------------------------------------------------------
def feature_columns(df: pd.DataFrame, include_current_day_outcome: bool = False,
                    station_encoding: str = "label") -> list[str]:
    """The agreed feature list.

    station_encoding: 'label'  -> station_id_encoded (0-4)   for XGBoost / LightGBM / RF
                      'onehot' -> station_downtown..shobra   for LSTM / GRU
                      'none'   -> no district identifier
    include_current_day_outcome: if False (DEFAULT, as specified in the Week 3 brief) today's
        pm2_5_corrected / aqi_category / is_health_risk / aqi_class are excluded. Setting True adds the
        numeric ones back (pm2_5_corrected, is_health_risk, aqi_class): this is NOT leakage for a
        t+h forecast (today's PM2.5 is known at the end of day t) and is what the persistence baseline
        uses; see README "Decision to confirm".
    """
    drop = {STATION_COL, DATE_COL, "day_of_week", "season", "aqi_category", "station_id_encoded",
            *[c for c in df.columns if c.startswith("station_") and c != STATION_COL],
            *[c for c in df.columns if c.startswith("target_")], *CURRENT_DAY_OUTCOME_COLS}
    cols = [c for c in df.columns if c not in drop]
    if include_current_day_outcome:
        cols += ["pm2_5_corrected", "is_health_risk", "aqi_class"]
    if station_encoding == "label":
        cols += ["station_id_encoded"]
    elif station_encoding == "onehot":
        cols += [f"station_{n}" for n in STATION_MAP]
    elif station_encoding != "none":
        raise ValueError(station_encoding)
    return cols


# --------------------------------------------------------------------------------------
# 6. Cross-validation
# --------------------------------------------------------------------------------------
@dataclass
class PanelTimeSeriesSplit:
    """Rolling-origin (expanding-window) CV over a (district x day) panel.

    * Splits by calendar DATE globally -> every district is in train and test of every fold.
    * Test windows are contiguous, non-overlapping blocks of `test_days`, anchored to the END of the
      data (so the last fold tests on the most recent data and no data is wasted).
    * Train = every day from the start up to `horizon` days before the test window starts. That
      `horizon`-day purge gap matters: the label of a train row at day t lives on day t+h, so
      without the gap the last h training labels would be test-period values.
    * Never shuffled.

    Why 135 days and not 180? 5 folds x 180-day tests + a 400-day minimum train needs 1300 days;
    only 1086 usable days exist. 5 x 135 + 411 initial train days fits exactly (1086).
    """
    n_splits: int = 5
    test_days: int = 135
    min_train_days: int = 400
    horizon: int = 1
    date_col: str = DATE_COL

    def _windows(self, days: pd.DatetimeIndex):
        n = len(days)
        first_test = n - self.n_splits * self.test_days
        if first_test - self.horizon < self.min_train_days:
            raise ValueError(
                f"{n} days cannot give {self.n_splits} folds x {self.test_days}-day tests with >= "
                f"{self.min_train_days} train days (+{self.horizon}d purge). Reduce n_splits/test_days.")
        for k in range(self.n_splits):
            ts = first_test + k * self.test_days
            yield days[0], days[ts - self.horizon - 1], days[ts], days[ts + self.test_days - 1]

    def get_n_splits(self, X=None, y=None, groups=None) -> int:
        return self.n_splits

    def split(self, X: pd.DataFrame, y=None, groups=None):
        """Yield (train_idx, test_idx) as positional integer arrays (sklearn-compatible)."""
        d = pd.DatetimeIndex(X[self.date_col])
        days = pd.DatetimeIndex(np.sort(d.unique()))
        for tr0, tr1, te0, te1 in self._windows(days):
            yield np.flatnonzero((d >= tr0) & (d <= tr1)), np.flatnonzero((d >= te0) & (d <= te1))

    def describe(self, X: pd.DataFrame) -> pd.DataFrame:
        d = pd.DatetimeIndex(X[self.date_col]); days = pd.DatetimeIndex(np.sort(d.unique()))
        rows = []
        for k, (tr0, tr1, te0, te1) in enumerate(self._windows(days), 1):
            rows.append(dict(fold=k, train_start=tr0.date(), train_end=tr1.date(),
                             train_days=(tr1 - tr0).days + 1, gap_days=self.horizon,
                             test_start=te0.date(), test_end=te1.date(), test_days=(te1 - te0).days + 1))
        return pd.DataFrame(rows)


def get_cv_splits(df: pd.DataFrame, horizon: int = 1, n_splits: int = 5, test_days: int = 135,
                  min_train_days: int = 400):
    """THE function everyone imports -> list of (train_idx, test_idx) for `horizon` (1, 2 or 3)."""
    return list(PanelTimeSeriesSplit(n_splits, test_days, min_train_days, horizon).split(df))


def scale_pos_weight(y) -> float:
    """N_negative / N_positive for XGBoost `scale_pos_weight` (compute on the TRAIN fold only)."""
    y = np.asarray(y); pos = (y == 1).sum()
    return float((y == 0).sum() / pos) if pos else float("nan")


def get_fold(df: pd.DataFrame, fold: int, horizon: int = 1, task: str = "reg",
             include_current_day_outcome: bool = False, station_encoding: str = "label",
             **split_kwargs) -> dict:
    """Everything for one fold (fold = 0..4).

    task: 'reg' -> target_{h}d (PM2.5 in ug/m3) | 'clf' -> target_risk_{h}d (0/1) | 'aqi' -> target_aqi_{h}d
    Returns dict: X_train, y_train, X_test, y_test, train_idx, test_idx, features and, for 'clf',
    scale_pos_weight (use for XGBoost) / class_weight (use for LightGBM / sklearn), computed on train only.
    """
    target = {"reg": f"target_{horizon}d", "clf": f"target_risk_{horizon}d",
              "aqi": f"target_aqi_{horizon}d"}[task]
    tr, te = get_cv_splits(df, horizon, **split_kwargs)[fold]
    feats = feature_columns(df, include_current_day_outcome, station_encoding)
    out = dict(X_train=df.iloc[tr][feats], y_train=df.iloc[tr][target],
               X_test=df.iloc[te][feats], y_test=df.iloc[te][target],
               train_idx=tr, test_idx=te, features=feats, target=target)
    if task == "clf":
        spw = scale_pos_weight(out["y_train"])
        out["scale_pos_weight"] = spw
        out["class_weight"] = {0: 1.0, 1: spw}
    return out


def class_balance(df: pd.DataFrame, horizon: int = 1, **split_kwargs) -> pd.DataFrame:
    """Per fold: positive-class share of target_risk_{h}d in train/test (pooled and per district)
    and the train-fold scale_pos_weight."""
    t = f"target_risk_{horizon}d"; rows = []
    for k, (tr, te) in enumerate(get_cv_splits(df, horizon, **split_kwargs), 1):
        a, b = df.iloc[tr], df.iloc[te]
        r = dict(fold=k, n_train=len(a), n_test=len(b), pos_rate_train=a[t].mean(),
                 pos_rate_test=b[t].mean(), n_pos_train=int(a[t].sum()), n_pos_test=int(b[t].sum()),
                 scale_pos_weight_train=scale_pos_weight(a[t]))
        for s, g in b.groupby(STATION_COL):
            r[f"test_pos_{s}"] = g[t].mean()
        rows.append(r)
    return pd.DataFrame(rows)


def class_balance_by_district(df: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
    """Whole-dataset positive rate and N_neg/N_pos per district (range check vs. brief's 2.5-7x)."""
    t = f"target_risk_{horizon}d"
    g = df.groupby(STATION_COL)[t].agg(n="size", n_pos="sum", pos_rate="mean")
    g["neg_over_pos"] = (g.n - g.n_pos) / g.n_pos
    return g


# --------------------------------------------------------------------------------------
# 7. Integrity / leakage checks
# --------------------------------------------------------------------------------------
def validate(df: pd.DataFrame, verbose: bool = True) -> bool:
    """Raises AssertionError on the first problem; returns True otherwise."""
    log = print if verbose else (lambda *a, **k: None)
    feats_all = feature_columns(df, True, "label")
    assert not df[feats_all + [c for c in df.columns if c.startswith("target_")]].isna().any().any()
    log("OK  no NaN in features or targets")

    g = df.groupby(STATION_COL)
    for h in HORIZONS:  # targets are the future PM2.5 of the SAME district
        exp = g[PM_COL].shift(-h)
        m = exp.notna()
        assert np.allclose(df.loc[m, f"target_{h}d"], exp[m])
        assert (df.loc[m, f"target_risk_{h}d"] == g["is_health_risk"].shift(-h)[m]).all()
    log("OK  target_{1,2,3}d == PM2.5 at t+h within each district")

    # Lag / rolling features are strictly past-only (so valid for every horizon).
    # Rows in the first 7 days were dropped, so check against PM2.5 inside the retained frame
    # for lags that exist there (t-1 .. t-7 are within the frame after the first 7 rows).
    for L in (1, 2, 3, 7):
        c = f"pm2_5_corrected_lag_{L}d"
        exp = g[PM_COL].shift(L); m = exp.notna()
        assert np.allclose(df.loc[m, c], exp[m]), c
    for w in (3, 7):
        c = f"pm2_5_corrected_roll_avg_{w}d"
        exp = g[PM_COL].transform(lambda s: s.shift(1).rolling(w, min_periods=1).mean())
        m = g.cumcount() >= w
        assert np.allclose(df.loc[m, c], exp[m]), c
    log("OK  PM2.5 lags/rolling means use days <= t-1 only (no look-ahead; valid for t+1..t+3)")

    feats = feature_columns(df, False, "label")
    assert not set(feats) & set(CURRENT_DAY_OUTCOME_COLS)
    assert not any(c.startswith("target_") for c in feats)
    assert not {"is_health_risk", "aqi_class", "aqi_category", PM_COL} & set(feats)
    log(f"OK  default feature set: {len(feats)} columns, current-day outcome and all targets excluded")

    n_dist = df[STATION_COL].nunique()
    for h in HORIZONS:
        prev_end = None
        for k, (tr, te) in enumerate(get_cv_splits(df, h), 1):
            a, b = df.iloc[tr], df.iloc[te]
            assert a[STATION_COL].nunique() == n_dist and b[STATION_COL].nunique() == n_dist
            assert len(set(tr) & set(te)) == 0
            assert (b[DATE_COL].min() - a[DATE_COL].max()).days == h + 1, "purge gap wrong"
            # no TRAIN label may fall on/after the first test day
            assert (a[DATE_COL].max() + pd.Timedelta(days=h)) < b[DATE_COL].min()
            assert a[DATE_COL].nunique() >= 400
            assert len(a) == a[DATE_COL].nunique() * n_dist and len(b) == b[DATE_COL].nunique() * n_dist
            if prev_end is not None:
                assert (b[DATE_COL].min() - prev_end).days == 1, "test windows must be contiguous"
            prev_end = b[DATE_COL].max()
    te_sets = [[tuple(x) for x in (df.iloc[t][[STATION_COL, DATE_COL]].values)]
               for t in (s[1] for s in get_cv_splits(df, 1))]
    te_sets3 = [[tuple(x) for x in (df.iloc[t][[STATION_COL, DATE_COL]].values)]
                for t in (s[1] for s in get_cv_splits(df, 3))]
    assert te_sets == te_sets3, "test rows must be identical across horizons"
    log("OK  CV: all 5 districts in every train & test fold, expanding train, contiguous test blocks,")
    log("    purge gap = horizon (no label overlap), identical test rows for t+1/t+2/t+3")
    return True


# --------------------------------------------------------------------------------------
# CLI:  python splitting.py daily_features_v2.parquet [--out prepared_dataset.parquet]
# --------------------------------------------------------------------------------------
def _report(df, nan_report):
    pd.set_option("display.width", 200, "display.max_columns", 30)
    print("\n== NaN handling / rows per district ==\n", nan_report.to_string())
    print(f"\nfinal modelling frame: {df.shape[0]} rows x {df.shape[1]} cols "
          f"({df[DATE_COL].min().date()} .. {df[DATE_COL].max().date()})")
    print("\n== CV folds (horizon=1) ==\n", PanelTimeSeriesSplit().describe(df).to_string(index=False))
    print("\n== health-risk balance per fold (target_risk_1d) ==\n",
          class_balance(df, 1).round(3).to_string(index=False))
    print("\n== per district ==\n", class_balance_by_district(df, 1).round(3).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("source"); ap.add_argument("--out", default=None)
    a = ap.parse_args()
    d, rep = prepare_dataset(a.source, return_report=True)
    validate(d); _report(d, rep)
    if a.out:
        d.to_parquet(a.out, index=False) if a.out.endswith(".parquet") else d.to_csv(a.out, index=False)
        print("\nsaved", a.out)
