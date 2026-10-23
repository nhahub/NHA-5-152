"""
Task 5 - LSTM / GRU for the PM2.5 panel, using Task 1's splitting.py (same folds, targets, features).

Regression  (PM2.5 at t+h):          python lstm_gru.py --task reg --horizon 1 2 3
Classification (health risk at t+h): python lstm_gru.py --task clf --horizon 1 2 3

=
import argparse, os, random
import numpy as np
import pandas as pd
import splitting as sp

# ------------------------------------------------------------------ windows
def build_windows(X, stations, seq_len):
    """X: (n_rows, n_feat) in df order (sorted by district, then day). Returns (n_rows, seq_len, n_feat):
    for row t the features of days t-seq_len+1..t of the SAME district (no look-ahead). The first rows of a
    district are edge-padded with its first row."""
    n, f = X.shape
    out = np.empty((n, seq_len, f), dtype=np.float32)
    for s in np.unique(stations):
        idx = np.flatnonzero(stations == s)
        Xs = X[idx]
        pad = np.concatenate([np.repeat(Xs[:1], seq_len - 1, axis=0), Xs], axis=0)
        for j in range(len(idx)):
            out[idx[j]] = pad[j:j + seq_len]
    return out

# ------------------------------------------------------------------ metrics
def reg_metrics(y, p):
    err = p - y
    ss_res, ss_tot = (err ** 2).sum(), ((y - y.mean()) ** 2).sum()
    mape = np.mean(np.abs(err) / np.maximum(np.abs(y), np.finfo(np.float64).eps))  # same as sklearn
    return dict(RMSE=float(np.sqrt((err ** 2).mean())), MAE=float(np.abs(err).mean()), MAPE=float(mape),
                R2=float(1 - ss_res / ss_tot), bias=float(err.mean()))

def clf_metrics(y, prob, thr=0.5):
    from sklearn.metrics import precision_score, recall_score, f1_score, roc_auc_score
    y = np.asarray(y).astype(int); pred = (prob >= thr).astype(int)
    return dict(precision=float(precision_score(y, pred, zero_division=0)),
                recall=float(recall_score(y, pred, zero_division=0)),
                f1=float(f1_score(y, pred, zero_division=0)),
                auc=float(roc_auc_score(y, prob)), n_pos_test=int(y.sum()))

# ------------------------------------------------------------------ model / training
def make_model(kind, n_feat, hidden, layers, dropout):
    import torch.nn as nn
    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            rnn = nn.LSTM if kind == "lstm" else nn.GRU
            self.rnn = rnn(n_feat, hidden, num_layers=layers, batch_first=True,
                           dropout=dropout if layers > 1 else 0.0)
            self.drop = nn.Dropout(dropout)
            self.head = nn.Linear(hidden, 1)
        def forward(self, x):
            o, _ = self.rnn(x)
            return self.head(self.drop(o[:, -1])).squeeze(-1)   # regression value, or a logit for clf
    return Net()

def fit_predict(kind, Xtr, ytr, Xva, yva, Xte, a, seed, pos_weight=None):
    """Returns raw outputs on Xte (standardised PM2.5 for reg, logits for clf) and the epochs run."""
    import torch
    torch.manual_seed(seed); np.random.seed(seed); random.seed(seed)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = make_model(kind, Xtr.shape[2], a.hidden, a.layers, a.dropout).to(dev)
    opt = torch.optim.Adam(model.parameters(), lr=a.lr, weight_decay=a.wd)
    if pos_weight is None:
        loss_fn = torch.nn.HuberLoss()
    else:
        loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor(float(pos_weight)).to(dev))
    T = lambda z: torch.tensor(np.asarray(z), dtype=torch.float32)
    Xtr_t, ytr_t = T(Xtr), T(ytr)
    best, best_state, bad = np.inf, None, 0
    for ep in range(a.epochs):
        model.train()
        perm = torch.randperm(len(Xtr_t))
        for i in range(0, len(perm), a.batch):
            b = perm[i:i + a.batch]
            opt.zero_grad()
            loss = loss_fn(model(Xtr_t[b].to(dev)), ytr_t[b].to(dev))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
        model.eval()
        with torch.no_grad():
            v = loss_fn(model(T(Xva).to(dev)), T(yva).to(dev)).item()
        if v < best - 1e-5:
            best, bad = v, 0
            best_state = {k: x.detach().clone() for k, x in model.state_dict().items()}
        else:
            bad += 1
            if bad >= a.patience:
                break
    model.load_state_dict(best_state)
    model.eval()
    with torch.no_grad():
        return model(T(Xte).to(dev)).cpu().numpy(), ep + 1

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", choices=["reg", "clf"], default="reg")
    ap.add_argument("--data", default="prepared_dataset.parquet")
    ap.add_argument("--out", default="task5_outputs")
    ap.add_argument("--horizon", type=int, nargs="+", default=[1])
    ap.add_argument("--models", nargs="+", default=["lstm", "gru"])
    ap.add_argument("--seq-len", type=int, default=14)
    ap.add_argument("--hidden", type=int, default=32)
    ap.add_argument("--layers", type=int, default=1)
    ap.add_argument("--dropout", type=float, default=0.3)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--wd", type=float, default=1e-4)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--epochs", type=int, default=80)
    ap.add_argument("--patience", type=int, default=10)
    ap.add_argument("--val-frac", type=float, default=0.15, help="last share of TRAIN dates used for early stopping")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--include-current-day-outcome", action="store_true",
                    help="add today's PM2.5 as a feature (team must agree on this setting)")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    clf = a.task == "clf"

    df = pd.read_parquet(a.data)            # do NOT edit / re-sort: fold indices are row positions
    stations = df[sp.STATION_COL].values
    rows_m, preds = [], []
    for h in a.horizon:
        for k in range(5):
            f = sp.get_fold(df, k, horizon=h, task=a.task, station_encoding="onehot",
                            include_current_day_outcome=a.include_current_day_outcome)
            feats, tr, te, target = f["features"], f["train_idx"], f["test_idx"], f["target"]
            mu, sd = df.iloc[tr][feats].mean().values, df.iloc[tr][feats].std().replace(0, 1).values  # TRAIN only
            Z = ((df[feats].values - mu) / sd).astype(np.float32)
            W = build_windows(Z, stations, a.seq_len)
            ytr_all = df[target].values.astype(np.float32)
            if clf:
                ym, ys, pw = 0.0, 1.0, f["scale_pos_weight"]
            else:
                ym, ys, pw = float(f["y_train"].mean()), float(f["y_train"].std()), None
            days = np.sort(df.iloc[tr][sp.DATE_COL].unique())          # time-based validation split
            cut = days[int(len(days) * (1 - a.val_frac))]
            is_val = (df.iloc[tr][sp.DATE_COL] >= cut).values
            tr_fit, tr_val = tr[~is_val], tr[is_val]
            ytrue = f["y_test"].values
            for kind in a.models:
                raw, ep = fit_predict(kind, W[tr_fit], (ytr_all[tr_fit] - ym) / ys,
                                      W[tr_val], (ytr_all[tr_val] - ym) / ys, W[te], a, a.seed, pw)
                if clf:
                    p = 1 / (1 + np.exp(-raw)); m = clf_metrics(ytrue, p)
                    msg = f"F1={m['f1']:.3f} recall={m['recall']:.3f} AUC={m['auc']:.3f}"
                else:
                    p = raw * ys + ym; m = reg_metrics(ytrue, p)
                    msg = f"RMSE={m['RMSE']:.2f} MAE={m['MAE']:.2f} R2={m['R2']:.3f}"
                rows_m.append(dict(model=kind, horizon=h, fold=k + 1, epochs=ep, n_test=len(te), **m))
                preds.append(pd.DataFrame(dict(model=kind, horizon=h, fold=k + 1,
                                               station_id=df.iloc[te][sp.STATION_COL].values,
                                               day=df.iloc[te][sp.DATE_COL].values, y_true=ytrue, y_pred=p)))
                print(f"h={h} fold={k+1} {kind:4s} {msg} (epochs {ep})", flush=True)
    bf = pd.DataFrame(rows_m)
    cols = ["precision", "recall", "f1", "auc"] if clf else ["RMSE", "MAE", "MAPE", "R2", "bias"]
    bf.to_csv(f"{a.out}/metrics_by_fold_{a.task}.csv", index=False)
    bf.groupby(["model", "horizon"])[cols].agg(["mean", "std"]).round(3).to_csv(f"{a.out}/metrics_summary_{a.task}.csv")
    pd.concat(preds).to_csv(f"{a.out}/predictions_{a.task}.csv", index=False)
    print(bf.groupby(["model", "horizon"])[cols].mean().round(3))

if __name__ == "__main__":
    main()
