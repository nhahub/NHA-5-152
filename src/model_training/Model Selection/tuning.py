"""
tuning.py - Week 4, Task 1: the shared tuning foundation for Tasks 2-6.

Everything that must be IDENTICAL across people lives here, so results are comparable:

    protocol constants         hold-out date, purge gap, CV settings, seed, search budget, feature-set names
    split_tuning_holdout(df)   Week 3 prepared frame -> (tuning, holdout, purged)
    load_tuning_data()         what Tasks 2-5 load. The hold-out is NOT reachable from here.
    load_holdout(confirm=...)  Task 6 only: hash-checked, refuses to load without the confirmation string
    get_cv_splits(df, h)       splitting.get_cv_splits with the Week 4 CV settings baked in
    validate_protocol(...)     leakage / integrity checks for the tuning + hold-out split
    load_feature_set(name)     "full" or "pruned" feature list (feature_sets.json)

    cv_evaluate(...)           rolling-origin CV of ONE parameter set -> CVResult (per-fold metrics)
    make_objective(...)        Optuna objective (returns the mean CV score) - the function Tasks 2-4 reuse
    run_study(...)             one-call wrapper: Optuna study + MLflow logging, resumable
    DEFAULT_SPACES             the agreed search space per model (LightGBM, XGBoost)

    setup_mlflow / run_name / standard_tags / log_cv_result      the MLflow logging convention
    cv_shap_importance / decide_pruning                          SHAP-based feature pruning

Quick start (Task 2 example)
----------------------------
    import tuning as tn
    df    = tn.load_tuning_data()
    feats = tn.load_feature_set("pruned", df)
    study = tn.run_study("lightgbm", horizon=1, df=df, features=feats, feature_set="pruned",
                         task="reg", owner="abdullah_alaa")
    print(study.best_value, study.best_params)

Design notes
------------
* Only splitting.py's PanelTimeSeriesSplit is used for folds (nothing re-implemented).
* No early stopping anywhere: the validation fold is time-ordered future data, so stopping on it would
  leak. n_estimators is a tuned hyper-parameter instead.
* Fold std is population std (ddof=0), the same convention as the Week 3 notebooks.
* mlflow / optuna / lightgbm / xgboost are imported lazily, so cv_evaluate works without mlflow/optuna.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

import splitting as sp

# ======================================================================================
# 1. PROTOCOL CONSTANTS  (single source of truth - do not redefine in notebooks)
# ======================================================================================
PROTOCOL_VERSION = "w4-v1"

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ART_DIR = ROOT / "artifacts"
TUNING_PATH = DATA_DIR / "tuning_set.parquet"
HOLDOUT_PATH = DATA_DIR / "holdout_LOCKED" / "holdout_set.parquet"
FEATURE_SETS_PATH = ART_DIR / "feature_sets.json"
PROTOCOL_JSON = ART_DIR / "tuning_protocol.json"

# Hold-out: the last ~6 months of every district. Nobody touches it until Task 6.
HOLDOUT_START = "2026-03-01"          # first hold-out day (data ends 2026-08-28)
MAX_HORIZON = 3
PURGE_DAYS = MAX_HORIZON              # tuning rows whose t+h label would land in the hold-out are dropped

# Rolling-origin CV on the tuning set (5 x 90-day test blocks, one block ~ one season).
# Week 3 used 5 x 135, which no longer fits once ~6 months are frozen away.
CV = dict(n_splits=5, test_days=90, min_train_days=400)

SEED = 42
N_TRIALS = {"reg": 60, "clf": 40}      # per study = (model, horizon, feature_set[, imbalance strategy])
N_STARTUP_TRIALS = 15                  # random trials before TPE kicks in / before pruning starts

# Week 3 brief default: today's PM2.5 / risk flag is NOT a feature (newest PM2.5 info = lag_1d).
# Every task must use the same setting. Flip it here only if the team decides otherwise.
INCLUDE_CURRENT_DAY_OUTCOME = False

FEATURE_SET_NAMES = ("full", "pruned")
PRECISION_FLOOR = 0.75                 # clf objective: best recall subject to precision >= this (per fold)

# Pruning rule: drop a feature only if its share of total mean|SHAP| is below this in EVERY
# (model, task, horizon) combination. These are hypotheses from the Week 3 brief, so never drop them.
SHAP_SHARE_THRESHOLD = 0.001
MUST_KEEP = ["inversion_proxy_v2", "is_downwind_helwan", "is_downwind_shobra",
             "dust_transport_proxy", "station_id_encoded"]

# Week 3 "default" hyper-parameters (XGBoost from the Week 3 notebook; LightGBM = library defaults).
DEFAULT_PARAMS = {
    "lightgbm": {},
    "xgboost": dict(n_estimators=100, max_depth=6, learning_rate=0.1),
}

# MLflow convention (see run_name / standard_tags / log_cv_result)
MLFLOW_EXPERIMENT = "PM25_week4_tuning"
MLFLOW_URI_DEFAULT = f"sqlite:///{(ROOT / 'mlflow.db').as_posix()}"   # DB store: needed for the model registry (Task 6)
STUDY_STORAGE = f"sqlite:///{(ART_DIR / 'optuna_studies.db').as_posix()}"

DEFAULT_METRIC = {"reg": "rmse", "clf": "recall_at_precision"}
DIRECTION = {"rmse": "minimize", "mae": "minimize", "logloss": "minimize",
             "r2": "maximize", "auc": "maximize", "recall_at_precision": "maximize"}
SHORT = {"lightgbm": "lgbm", "xgboost": "xgb"}


# ======================================================================================
# 2. HOLD-OUT / TUNING SPLIT
# ======================================================================================
def split_tuning_holdout(df: pd.DataFrame, holdout_start: str = HOLDOUT_START,
                         purge_days: int = PURGE_DAYS):
    """Week 3 prepared frame -> (tuning, holdout, purged), each with a fresh 0..n-1 index.

    holdout : day >= holdout_start (all 5 districts)
    purged  : the `purge_days` days just before the hold-out. Their t+h targets fall on/after the
              hold-out start, so they belong to neither set (dropped).
    tuning  : everything before that, so every tuning label (t+1..t+3) is observed before the hold-out.
    """
    h0 = pd.Timestamp(holdout_start)
    day = df[sp.DATE_COL]
    holdout = df[day >= h0].reset_index(drop=True)
    purged = df[(day >= h0 - pd.Timedelta(days=purge_days)) & (day < h0)].reset_index(drop=True)
    tuning = df[day < h0 - pd.Timedelta(days=purge_days)].reset_index(drop=True)
    return tuning, holdout, purged


def content_hash(df: pd.DataFrame) -> str:
    """SHA-256 of the frame's content, independent of how/when the parquet file was written
    (column-sorted; dates as int64 ns; numbers as float64; strings as text)."""
    h = hashlib.sha256()
    for c in sorted(df.columns):
        s = df[c]
        if pd.api.types.is_datetime64_any_dtype(s):
            arr = s.astype("datetime64[ns]").astype("int64").to_numpy().tobytes()
        elif pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
            arr = s.to_numpy(dtype="float64").tobytes()
        else:
            arr = "\x1f".join(s.astype(str)).encode()
        h.update(str(c).encode() + b"\x00" + arr + b"\x01")
    return h.hexdigest()


def save_protocol(extra: dict) -> dict:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    proto = dict(protocol_version=PROTOCOL_VERSION, holdout_start=HOLDOUT_START, purge_days=PURGE_DAYS,
                 cv=CV, seed=SEED, n_trials=N_TRIALS, n_startup_trials=N_STARTUP_TRIALS,
                 include_current_day_outcome=INCLUDE_CURRENT_DAY_OUTCOME,
                 precision_floor=PRECISION_FLOOR, **extra)
    PROTOCOL_JSON.write_text(json.dumps(proto, indent=2, default=str))
    return proto


def load_protocol() -> dict:
    return json.loads(PROTOCOL_JSON.read_text())


def load_tuning_data() -> pd.DataFrame:
    """THE loader for Tasks 2-5. Contains no hold-out rows (asserted)."""
    df = pd.read_parquet(TUNING_PATH)
    df[sp.DATE_COL] = pd.to_datetime(df[sp.DATE_COL])
    assert df[sp.DATE_COL].max() + pd.Timedelta(days=MAX_HORIZON) < pd.Timestamp(HOLDOUT_START), \
        "tuning data overlaps the hold-out period"
    return df


def load_holdout(confirm: str | None = None) -> pd.DataFrame:
    """Task 6 ONLY. Score the final models once. Verifies the content hash recorded at freeze time."""
    if confirm != "TASK6_FINAL_SCORING":
        raise PermissionError("The hold-out is frozen until Task 6. Tasks 2-5 must use load_tuning_data().")
    df = pd.read_parquet(HOLDOUT_PATH)
    df[sp.DATE_COL] = pd.to_datetime(df[sp.DATE_COL])
    expected = load_protocol().get("holdout_sha256")
    if expected and content_hash(df) != expected:
        raise RuntimeError("hold-out content hash mismatch: the file was modified after freezing")
    return df


# ======================================================================================
# 3. CV + FEATURE SETS
# ======================================================================================
def get_cv_splits(df: pd.DataFrame, horizon: int):
    """splitting.get_cv_splits with the Week 4 settings. Always use this, never hand-built folds."""
    return sp.get_cv_splits(df, horizon=horizon, **CV)


def describe_cv(df: pd.DataFrame, horizon: int = 1) -> pd.DataFrame:
    return sp.PanelTimeSeriesSplit(horizon=horizon, **CV).describe(df)


def base_features(df: pd.DataFrame) -> list[str]:
    return sp.feature_columns(df, include_current_day_outcome=INCLUDE_CURRENT_DAY_OUTCOME,
                              station_encoding="label")


def load_feature_set(name: str, df: pd.DataFrame | None = None) -> list[str]:
    """'full' or 'pruned' -> list of column names (checked against df if given)."""
    if name not in FEATURE_SET_NAMES:
        raise ValueError(f"feature set must be one of {FEATURE_SET_NAMES}")
    feats = json.loads(FEATURE_SETS_PATH.read_text())[name]
    if df is not None:
        missing = [c for c in feats if c not in df.columns]
        assert not missing, f"features missing from df: {missing[:5]}"
    return feats


def target_col(horizon: int, task: str) -> str:
    return {"reg": f"target_{horizon}d", "clf": f"target_risk_{horizon}d"}[task]


def validate_protocol(tuning: pd.DataFrame, holdout: pd.DataFrame, purged: pd.DataFrame,
                      verbose: bool = True) -> bool:
    """Leakage / integrity checks for the Week 4 split. Raises AssertionError on the first problem.
    (splitting.validate() cannot be used on the tuning set: it hard-codes the 5x135-day Week 3 CV.)"""
    log = print if verbose else (lambda *a, **k: None)
    h0 = pd.Timestamp(HOLDOUT_START)
    d_t, d_h = tuning[sp.DATE_COL], holdout[sp.DATE_COL]

    assert d_t.max() < h0 <= d_h.min(), "tuning must end before the hold-out starts"
    assert (d_t.max() + pd.Timedelta(days=MAX_HORIZON)) < h0, "a tuning label (t+3) lands in the hold-out"
    log(f"OK  tuning ends {d_t.max().date()}; its t+3 labels end {(d_t.max() + pd.Timedelta(days=3)).date()} "
        f"< hold-out start {h0.date()}")
    assert not purged.empty and (purged[sp.DATE_COL] < h0).all()
    assert set(map(tuple, tuning[[sp.STATION_COL, sp.DATE_COL]].values)).isdisjoint(
        set(map(tuple, holdout[[sp.STATION_COL, sp.DATE_COL]].values)))
    log("OK  no (district, day) row is in both sets; purged rows are in neither")

    n_dist = tuning[sp.STATION_COL].nunique()
    assert holdout[sp.STATION_COL].nunique() == n_dist
    assert len(tuning) == tuning[sp.DATE_COL].nunique() * n_dist, "tuning must be a complete day x district panel"
    assert len(holdout) == d_h.nunique() * n_dist, "hold-out must be a complete day x district panel"
    log(f"OK  both sets are complete panels with all {n_dist} districts")

    # recompute targets from the raw PM2.5 inside each set so we know they're intact
    for name, d in (("tuning", tuning), ("holdout", holdout)):
        g = d.sort_values([sp.STATION_COL, sp.DATE_COL]).groupby(sp.STATION_COL)
        for h in sp.HORIZONS:
            exp = g[sp.PM_COL].shift(-h)
            m = exp.notna()
            assert np.allclose(d.loc[m.index[m], f"target_{h}d"].to_numpy(), exp[m].to_numpy()), (name, h)
    log("OK  target_{1,2,3}d still equal PM2.5 at t+h inside each set")

    te_rows = {}
    for h in sp.HORIZONS:
        prev_end = None
        for k, (tr, te) in enumerate(get_cv_splits(tuning, h), 1):
            a, b = tuning.iloc[tr], tuning.iloc[te]
            assert a[sp.STATION_COL].nunique() == n_dist and b[sp.STATION_COL].nunique() == n_dist
            assert not set(tr) & set(te)
            assert (b[sp.DATE_COL].min() - a[sp.DATE_COL].max()).days == h + 1, "purge gap wrong"
            assert (a[sp.DATE_COL].max() + pd.Timedelta(days=h)) < b[sp.DATE_COL].min()
            assert a[sp.DATE_COL].nunique() >= CV["min_train_days"]
            assert b[sp.DATE_COL].nunique() == CV["test_days"]
            if prev_end is not None:
                assert (b[sp.DATE_COL].min() - prev_end).days == 1, "test windows must be contiguous"
            prev_end = b[sp.DATE_COL].max()
            te_rows.setdefault(h, []).append(tuple(te))
    assert te_rows[1] == te_rows[2] == te_rows[3], "test rows must be identical across horizons"
    log(f"OK  CV ({CV['n_splits']} x {CV['test_days']}d): all districts on both sides, expanding train, "
        f"contiguous tests, purge = horizon, identical test rows for t+1/t+2/t+3")
    return True


# ======================================================================================
# 4. MODELS + CV EVALUATION
# ======================================================================================
def build_model(model_name: str, task: str, params: dict | None = None, seed: int = SEED):
    """LightGBM / XGBoost sklearn-API estimator with the shared fixed settings + `params`."""
    params = dict(params or {})
    if model_name == "lightgbm":
        import lightgbm as lgb
        base = dict(random_state=seed, n_jobs=-1, verbose=-1, deterministic=True, force_row_wise=True)
        cls = lgb.LGBMRegressor if task == "reg" else lgb.LGBMClassifier
    elif model_name == "xgboost":
        import xgboost as xgb
        base = dict(random_state=seed, n_jobs=-1, tree_method="hist")
        if task == "clf":
            base["eval_metric"] = "logloss"
        cls = xgb.XGBRegressor if task == "reg" else xgb.XGBClassifier
    else:
        raise ValueError(f"unknown model {model_name!r} (use 'lightgbm' or 'xgboost')")
    base.update(params)
    return cls(**base)


def _reg_metrics(y, p) -> dict:
    err = np.asarray(y, float) - np.asarray(p, float)
    ss_res, ss_tot = float((err ** 2).sum()), float(((y - np.mean(y)) ** 2).sum())
    return dict(rmse=float(np.sqrt(np.mean(err ** 2))), mae=float(np.mean(np.abs(err))),
                r2=1.0 - ss_res / ss_tot if ss_tot > 0 else np.nan)


def _clf_metrics(y, p, floor: float) -> dict:
    from sklearn.metrics import log_loss, precision_recall_curve, roc_auc_score
    y = np.asarray(y)
    out = dict(pos_rate=float(y.mean()), auc=np.nan, logloss=np.nan,
               recall_at_precision=0.0, threshold=np.nan)
    if len(np.unique(y)) < 2:
        return out
    out["auc"] = float(roc_auc_score(y, p))
    out["logloss"] = float(log_loss(y, np.clip(p, 1e-7, 1 - 1e-7), labels=[0, 1]))
    prec, rec, thr = precision_recall_curve(y, p)
    ok = prec[:-1] >= floor                      # the final (precision=1, recall=0) point has no threshold
    if ok.any():
        i = np.flatnonzero(ok)[np.argmax(rec[:-1][ok])]
        out["recall_at_precision"], out["threshold"] = float(rec[i]), float(thr[i])
    return out


@dataclass
class CVResult:
    model_name: str
    task: str
    horizon: int
    metric: str                          # the primary (optimised) metric
    fold_metrics: pd.DataFrame           # one row per fold; columns = fold + every metric
    seconds: float
    n_features: int
    oof: pd.DataFrame | None = field(default=None, repr=False)   # fold, row (positional in df), y_true, y_pred

    @property
    def score(self) -> float:
        return float(np.nanmean(self.fold_metrics[self.metric]))

    @property
    def score_std(self) -> float:        # ddof=0, as in the Week 3 notebooks
        return float(np.nanstd(self.fold_metrics[self.metric], ddof=0))

    def summary(self) -> pd.DataFrame:
        cols = [c for c in self.fold_metrics.columns if c != "fold"]
        return pd.DataFrame({"mean": self.fold_metrics[cols].mean(), "std": self.fold_metrics[cols].std(ddof=0)})

    def metrics_flat(self) -> dict:
        """Flat {name: float} dict for mlflow.log_metrics (NaN dropped)."""
        out = {"cv_score": self.score, "cv_score_std": self.score_std, "fit_seconds": self.seconds}
        for m, row in self.summary().iterrows():
            out[f"cv_{m}_mean"], out[f"cv_{m}_std"] = float(row["mean"]), float(row["std"])
        for _, r in self.fold_metrics.iterrows():
            for m in self.fold_metrics.columns:
                if m != "fold":
                    out[f"fold_{int(r['fold'])}_{m}"] = float(r[m])
        return {k: v for k, v in out.items() if np.isfinite(v)}


def cv_evaluate(model_name: str, params: dict, df: pd.DataFrame, horizon: int, features: list[str],
                task: str = "reg", metric: str | None = None, imbalance: str = "none",
                precision_floor: float = PRECISION_FLOOR, report=None, return_oof: bool = False,
                seed: int = SEED) -> CVResult:
    """Rolling-origin CV of ONE parameter set. Pooled districts, fold-level metrics.

    task      'reg'  -> target_{h}d, metrics rmse / mae / r2
              'clf'  -> target_risk_{h}d, metrics auc / logloss / recall_at_precision (+ its threshold)
    imbalance (clf only) 'none' | 'scale_pos_weight' (N_neg/N_pos of the TRAIN fold) |
              'class_weight' ('balanced' sample weights computed on the TRAIN fold)
    report    optional callback report(fold_number, running_mean_of_primary_metric) (used for pruning)
    """
    metric = metric or DEFAULT_METRIC[task]
    X = df[features]
    y = df[target_col(horizon, task)].to_numpy()
    t0, rows, oof = time.time(), [], []
    for k, (tr, te) in enumerate(get_cv_splits(df, horizon), 1):
        p, fit_kw = dict(params), {}
        if task == "clf" and imbalance != "none":
            ytr, n = y[tr], len(tr)
            if imbalance == "scale_pos_weight":
                p["scale_pos_weight"] = sp.scale_pos_weight(ytr)
            elif imbalance == "class_weight":
                pos = float(ytr.sum())
                fit_kw["sample_weight"] = np.where(ytr == 1, n / (2 * pos), n / (2 * (n - pos)))
            else:
                raise ValueError(imbalance)
        model = build_model(model_name, task, p, seed).fit(X.iloc[tr], y[tr], **fit_kw)
        if task == "reg":
            pred = model.predict(X.iloc[te])
            m = _reg_metrics(y[te], pred)
        else:
            pred = model.predict_proba(X.iloc[te])[:, 1]
            m = _clf_metrics(y[te], pred, precision_floor)
        rows.append(dict(fold=k, **m))
        if return_oof:
            oof.append(pd.DataFrame(dict(fold=k, row=te, y_true=y[te], y_pred=pred)))
        if report is not None:
            report(k, float(np.nanmean([r[metric] for r in rows])))
    return CVResult(model_name, task, horizon, metric, pd.DataFrame(rows), time.time() - t0, len(features),
                    pd.concat(oof, ignore_index=True) if return_oof else None)


def result_row(res: CVResult, *, feature_set: str, label: str = "", imbalance: str = "none",
               params: dict | None = None) -> dict:
    """One flat row for the team's shared results CSV (artifacts/results/*.csv)."""
    r = dict(label=label, model=res.model_name, task=res.task, horizon=res.horizon, feature_set=feature_set,
             imbalance=imbalance, metric=res.metric, cv_mean=res.score, cv_std=res.score_std,
             n_features=res.n_features, seconds=round(res.seconds, 1), params=json.dumps(params or {}, default=str))
    for m, row in res.summary().iterrows():
        r[f"{m}_mean"], r[f"{m}_std"] = row["mean"], row["std"]
    return r


# ======================================================================================
# 5. SEARCH SPACES + THE SHARED OPTUNA OBJECTIVE
# ======================================================================================
def lightgbm_space(trial, task: str = "reg") -> dict:
    return dict(
        num_leaves=trial.suggest_int("num_leaves", 8, 128, log=True),
        learning_rate=trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        n_estimators=trial.suggest_int("n_estimators", 100, 800, step=50),
        min_child_samples=trial.suggest_int("min_child_samples", 5, 100, log=True),
        subsample=trial.suggest_float("subsample", 0.5, 1.0),
        subsample_freq=1,                       # LightGBM ignores `subsample` unless subsample_freq > 0
        colsample_bytree=trial.suggest_float("colsample_bytree", 0.4, 1.0),
        reg_alpha=trial.suggest_float("reg_alpha", 1e-8, 10.0, log=True),
        reg_lambda=trial.suggest_float("reg_lambda", 1e-8, 10.0, log=True),
    )


def xgboost_space(trial, task: str = "reg") -> dict:
    return dict(
        max_depth=trial.suggest_int("max_depth", 3, 8),
        learning_rate=trial.suggest_float("learning_rate", 0.01, 0.2, log=True),
        n_estimators=trial.suggest_int("n_estimators", 100, 800, step=50),
        min_child_weight=trial.suggest_float("min_child_weight", 1.0, 20.0, log=True),
        subsample=trial.suggest_float("subsample", 0.5, 1.0),
        colsample_bytree=trial.suggest_float("colsample_bytree", 0.4, 1.0),
        gamma=trial.suggest_float("gamma", 1e-8, 5.0, log=True),
        reg_lambda=trial.suggest_float("reg_lambda", 1e-3, 10.0, log=True),
    )


DEFAULT_SPACES = {"lightgbm": lightgbm_space, "xgboost": xgboost_space}


def make_objective(model_name: str, horizon: int, df: pd.DataFrame, features: list[str], task: str = "reg",
                   metric: str | None = None, param_space=None, imbalance: str = "none",
                   precision_floor: float = PRECISION_FLOOR, on_complete=None):
    """Optuna objective: trial -> mean CV score of `metric` (default RMSE for 'reg').

        param_space   callable(trial) -> params dict. Default = the agreed DEFAULT_SPACES[model_name].
        on_complete   optional callback(trial, params, CVResult) after a finished (non-pruned) trial.

    Folds are reported to Optuna one by one (running mean), so a MedianPruner can stop bad trials early.
    Remember the direction: DIRECTION[metric] ('minimize' for rmse, 'maximize' for recall_at_precision).
    """
    import optuna
    metric = metric or DEFAULT_METRIC[task]
    space = param_space or (lambda trial: DEFAULT_SPACES[model_name](trial, task))

    def objective(trial):
        params = space(trial)

        def report(k, running):
            trial.report(running, step=k)
            if trial.should_prune():
                raise optuna.TrialPruned()

        res = cv_evaluate(model_name, params, df, horizon, features, task, metric, imbalance,
                          precision_floor, report=report)
        trial.set_user_attr("cv_std", res.score_std)
        trial.set_user_attr("fold_scores", [float(v) for v in res.fold_metrics[metric]])
        trial.set_user_attr("seconds", res.seconds)
        if on_complete is not None:
            on_complete(trial, params, res)
        return res.score

    return objective


def run_study(model_name: str, horizon: int, df: pd.DataFrame, features: list[str], feature_set: str, *,
              owner: str, task: str = "reg", metric: str | None = None, param_space=None,
              imbalance: str = "none", n_trials: int | None = None, timeout: float | None = None,
              log_trials: bool = True, use_mlflow: bool = True, storage: str | None = STUDY_STORAGE,
              precision_floor: float = PRECISION_FLOOR):
    """One complete, comparable tuning study: Optuna (TPE + MedianPruner, fixed seed) + MLflow.

    The study is stored in sqlite under its run name, so an interrupted study RESUMES (n_trials is the
    TOTAL budget, not 'additional'). Returns the optuna Study.
    """
    import optuna
    metric = metric or DEFAULT_METRIC[task]
    n_trials = n_trials or N_TRIALS[task]
    name = run_name(model_name, task, horizon, feature_set, imbalance if task == "clf" else "")
    ART_DIR.mkdir(parents=True, exist_ok=True)
    study = optuna.create_study(
        study_name=name, storage=storage, load_if_exists=True, direction=DIRECTION[metric],
        sampler=optuna.samplers.TPESampler(seed=SEED, n_startup_trials=N_STARTUP_TRIALS),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=N_STARTUP_TRIALS, n_warmup_steps=1))
    TS = optuna.trial.TrialState
    done = sum(t.state in (TS.COMPLETE, TS.PRUNED) for t in study.trials)
    remaining = max(0, n_trials - done)

    tags = standard_tags(model_name, task, horizon, feature_set, owner, "tuning_study", imbalance)
    mlflow = setup_mlflow() if use_mlflow else None

    def on_complete(trial, params, res):
        if mlflow is not None and log_trials:
            log_cv_result(res, params=params, features=features, feature_set=feature_set, owner=owner,
                          run_type="trial", imbalance=imbalance, nested=True,
                          run_name=f"{name}-trial{trial.number:03d}",
                          extra_tags={"study": name, "trial": str(trial.number)})

    objective = make_objective(model_name, horizon, df, features, task, metric, param_space, imbalance,
                               precision_floor, on_complete)

    def _optimize():
        if remaining:
            study.optimize(objective, n_trials=remaining, timeout=timeout, gc_after_trial=True)

    if mlflow is None:
        _optimize()
        return study
    with mlflow.start_run(run_name=name):
        mlflow.set_tags(tags)
        mlflow.log_params({"n_trials_budget": n_trials, "n_features": len(features), "seed": SEED,
                           "metric": metric, "imbalance": imbalance, "n_folds": CV["n_splits"]})
        _optimize()
        comp = [t for t in study.trials if t.state == TS.COMPLETE]
        if comp:
            mlflow.log_params({f"best_{k}": v for k, v in study.best_params.items()})
            mlflow.log_metrics({"best_cv_score": study.best_value,
                                "best_cv_score_std": float(study.best_trial.user_attrs.get("cv_std", np.nan)),
                                "n_trials_complete": len(comp),
                                "n_trials_pruned": sum(t.state == TS.PRUNED for t in study.trials)})
            mlflow.log_dict({"features": features, "best_params": study.best_params,
                             "fold_scores": study.best_trial.user_attrs.get("fold_scores")}, "best_trial.json")
    return study


# ======================================================================================
# 6. MLFLOW LOGGING CONVENTION
# ======================================================================================
def setup_mlflow(tracking_uri: str | None = None, experiment: str = MLFLOW_EXPERIMENT):
    """Everyone logs to the same experiment name. Tracking URI: arg > $MLFLOW_TRACKING_URI > local sqlite."""
    import mlflow
    mlflow.set_tracking_uri(tracking_uri or os.environ.get("MLFLOW_TRACKING_URI") or MLFLOW_URI_DEFAULT)
    mlflow.set_experiment(experiment)
    return mlflow


def run_name(model_name: str, task: str, horizon: int, feature_set: str, suffix: str = "") -> str:
    """'{lgbm|xgb}-{reg|clf}-h{1|2|3}-{full|pruned}[-{imbalance}]'   e.g. 'lgbm-reg-h2-pruned'.
    Trials append '-trialNNN'. Baselines/finals use the same pattern with a different run_type tag."""
    base = f"{SHORT[model_name]}-{task}-h{horizon}-{feature_set}"
    return f"{base}-{suffix}" if suffix and suffix != "none" else base


def standard_tags(model_name: str, task: str, horizon: int, feature_set: str, owner: str,
                  run_type: str, imbalance: str = "none", extra: dict | None = None) -> dict:
    """run_type in {'baseline', 'tuning_study', 'trial', 'final'}. All tag values are strings."""
    tags = dict(week="4", protocol_version=PROTOCOL_VERSION, owner=owner, model=model_name, task=task,
                horizon=str(horizon), feature_set=feature_set, run_type=run_type, imbalance=imbalance,
                holdout_start=HOLDOUT_START, cv=f"{CV['n_splits']}x{CV['test_days']}d")
    tags.update({k: str(v) for k, v in (extra or {}).items()})
    return tags


def log_cv_result(res: CVResult, *, params: dict, features: list[str], feature_set: str, owner: str,
                  run_type: str, imbalance: str = "none", run_name: str | None = None, nested: bool = False,
                  extra_tags: dict | None = None) -> str:
    """Log ONE CV evaluation the agreed way. Returns the MLflow run id.

    params     every hyper-parameter (+ n_features, imbalance, seed)
    metrics    cv_score / cv_score_std (primary metric, mean & std over folds), cv_<metric>_mean/_std for every
               metric, fold_<k>_<metric> for every fold, fit_seconds
    artifacts  features.json (the exact feature list), fold_metrics.csv
    """
    import mlflow
    name = run_name or globals()["run_name"](res.model_name, res.task, res.horizon, feature_set,
                                             imbalance if res.task == "clf" else "")
    with mlflow.start_run(run_name=name, nested=nested) as run:
        mlflow.set_tags(standard_tags(res.model_name, res.task, res.horizon, feature_set, owner, run_type,
                                      imbalance, {"primary_metric": res.metric, **(extra_tags or {})}))
        mlflow.log_params({**params, "n_features": len(features), "imbalance": imbalance, "seed": SEED})
        mlflow.log_metrics(res.metrics_flat())
        mlflow.log_dict({"feature_set": feature_set, "n_features": len(features), "features": list(features)},
                        "features.json")
        mlflow.log_text(res.fold_metrics.to_csv(index=False), "fold_metrics.csv")
        return run.info.run_id


# ======================================================================================
# 7. SHAP-BASED FEATURE PRUNING
# ======================================================================================
def _contributions(model, model_name: str, X: pd.DataFrame) -> np.ndarray:
    """Exact TreeSHAP values (n_rows x n_features) from the libraries' native routines - the same
    algorithm the `shap` package wraps, without its version-dependent output shapes. For classifiers the
    values are in log-odds space."""
    if model_name == "lightgbm":
        c = model.predict(X, pred_contrib=True)
    else:
        import xgboost as xgb
        c = model.get_booster().predict(xgb.DMatrix(X, feature_names=list(X.columns)), pred_contribs=True)
    c = np.asarray(c)
    return c[:, :-1]                                   # last column is the bias term


def cv_shap_importance(model_name: str, task: str, horizon: int, df: pd.DataFrame, features: list[str],
                       params: dict | None = None, seed: int = SEED) -> pd.Series:
    """Mean |SHAP| per feature, computed OUT-OF-SAMPLE: in every CV fold the model is fitted on the train
    window and explained on the test window; the 5 fold means are averaged (equal weight)."""
    params = DEFAULT_PARAMS[model_name] if params is None else params
    X, y = df[features], df[target_col(horizon, task)].to_numpy()
    per_fold = []
    for tr, te in get_cv_splits(df, horizon):
        model = build_model(model_name, task, params, seed).fit(X.iloc[tr], y[tr])
        per_fold.append(np.abs(_contributions(model, model_name, X.iloc[te])).mean(axis=0))
    return pd.Series(np.mean(per_fold, axis=0), index=features)


def decide_pruning(imp: pd.DataFrame, week3_zero_gain: list[str] | None = None,
                   threshold: float = SHAP_SHARE_THRESHOLD, must_keep: list[str] = MUST_KEEP) -> pd.DataFrame:
    """imp: features x combos (e.g. 'lightgbm|reg|h1') of mean|SHAP|.
    A feature is DROPPED only if its share of total mean|SHAP| is < `threshold` in EVERY combo and it is not
    in `must_keep`. Returns a per-feature report (sorted by importance) with columns
    max_share, mean_share, n_combos_below, week3_zero_gain, decision ('keep'/'drop'), reason."""
    share = imp / imp.sum(axis=0)
    rep = pd.DataFrame({"max_share": share.max(axis=1), "mean_share": share.mean(axis=1),
                        "n_combos_below": (share < threshold).sum(axis=1)})
    rep["week3_zero_gain"] = rep.index.isin(week3_zero_gain or [])
    near_zero = rep["max_share"] < threshold
    protected = rep.index.isin(must_keep)
    rep["decision"] = np.where(near_zero & ~protected, "drop", "keep")
    rep["reason"] = np.select(
        [near_zero & ~protected, near_zero & protected],
        [f"share < {threshold:.1%} in all {share.shape[1]} model/task/horizon combos",
         "near-zero SHAP but protected (physical-proxy / district feature)"], default="")
    return rep.sort_values("mean_share", ascending=False)


def save_feature_sets(full: list[str], pruned: list[str], report: pd.DataFrame, meta: dict) -> None:
    ART_DIR.mkdir(parents=True, exist_ok=True)
    dropped = {f: r for f, r in report.loc[report.decision == "drop", "reason"].items()}
    assert set(pruned) <= set(full) and set(full) - set(pruned) == set(dropped)
    FEATURE_SETS_PATH.write_text(json.dumps(
        dict(full=list(full), pruned=list(pruned), dropped=dropped, meta=meta), indent=2, default=str))


def check_environment() -> dict:
    """Print/return versions of everything the tuning stack needs."""
    import importlib
    out = {}
    for m in ("numpy", "pandas", "sklearn", "lightgbm", "xgboost", "optuna", "mlflow", "pyarrow", "matplotlib"):
        try:
            out[m] = importlib.import_module(m).__version__
        except Exception as e:                          # noqa: BLE001
            out[m] = f"MISSING ({type(e).__name__})"
    return out
