#!/usr/bin/env python3
"""
WS1-09: E2 baselines. Three non-graph models predicting y_stage on the frozen WS2 folds.

  clinical_lr   age, sex, BMI, smoking, ECOG + common comorbidity flags -> logistic regression
  prot_pca_lr   tumor proteomics: top-1000 most variable proteins -> 20 PCs -> logistic regression
  concat_lr     both feature sets together

Everything that learns from data (imputation, variance ranking, scaling, PCA, the model) is fitted
inside each training fold only, so no test-fold information leaks into training.

Nested CV (default): within each outer training fold, an inner 5-fold CV on those training patients only
picks the L2 strength C (and, for protein models, the number of PCs). The tuned model is then refitted on
the whole training fold and scored ONCE on the outer test fold. The test fold never influences tuning.
Selection uses log loss (a proper scoring rule, steadier than AUROC on ~70 inner-training patients and it
penalizes the overconfidence untuned models showed). --no-nested reproduces the fixed C=1, 20-PC models.

Writes (contract section 6 / 8):
  data/processed/gnn_a/predictions.parquet   patient_id, model, fold, risk   (out of fold; GNN rows kept)
  eval/results/e2.csv                        model, fold, auroc (+ log_loss)
  eval/results/e2_params.csv                 hyperparameters chosen in each outer fold

Run from the repo root:
  python -m models.baselines
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import log_loss, roc_auc_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

PAT = Path("data/processed/patients")
PRED = Path("data/processed/gnn_a/predictions.parquet")
E2 = Path("eval/results/e2.csv")
PROT_CANDIDATES = [PAT / "proteomics_tumor.parquet", Path("data/processed/ccrcc_prot_tumor.parquet")]
LABEL_COLS = {"y_stage", "y_grade", "y_recur", "os_days", "os_event", "rfs_days"}  # never features
MODELS = ["clinical_lr", "prot_pca_lr", "concat_lr"]
C_GRID = [0.001, 0.003, 0.01, 0.03, 0.1, 0.3, 1.0]
PC_GRID = [5, 10, 20]


def param_grid(name):
    grid = {"lr__C": C_GRID}
    if name != "clinical_lr":
        grid["pre__prot__pca__n_components"] = PC_GRID
    return grid


# ---------- I/O (parquet, with CSV fallback for machines without pyarrow) ----------
def read(path):
    path = Path(path)
    if path.exists():
        return pd.read_parquet(path)
    if path.with_suffix(".csv").exists():
        return pd.read_csv(path.with_suffix(".csv"))
    raise FileNotFoundError(path)


def write(df, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(path, index=False)
    except ImportError:
        path = path.with_suffix(".csv")
        df.to_csv(path, index=False)
    print(f"  wrote {path}  {df.shape}")


# ---------- features ----------
def clinical_features(min_patients):
    p = read(PAT / "patients.parquet")
    f = pd.DataFrame({"patient_id": p["patient_id"].astype(str)})
    f["age"] = pd.to_numeric(p["age"], errors="coerce")
    f["sex_male"] = p["sex"].map({"male": 1.0, "female": 0.0})
    for c in ["bmi", "ever_smoker", "pack_years", "ecog"]:
        f[c] = pd.to_numeric(p[c], errors="coerce")
    ph = read(PAT / "patient_phenotypes.parquet")
    common = ph["curie"].value_counts()
    common = common[common >= min_patients].index  # rare flags are mostly noise with ~88 training patients
    flags = (ph[ph["curie"].isin(common)].assign(v=1.0)
             .pivot_table(index="patient_id", columns="curie", values="v", aggfunc="max")
             .add_prefix("ph_"))
    f = f.set_index("patient_id").join(flags).fillna({c: 0.0 for c in flags.columns})
    return f


def proteomics_matrix():
    for path in PROT_CANDIDATES:
        try:
            m = read(path)
        except FileNotFoundError:
            continue
        if True:
            if "sample_id" in m.columns:
                m = m.set_index("sample_id")
            m.index = m.index.astype(str)
            print(f"Proteomics: {path} ({m.shape[0]} tumors x {m.shape[1]} proteins)")
            return m
    raise FileNotFoundError("No tumor proteomics matrix. Copy data/processed/ccrcc_prot_tumor.parquet to "
                            "data/processed/patients/proteomics_tumor.parquet (see the WS2 handoff note).")


class TopVariance(BaseEstimator, TransformerMixin):
    """Keep the k columns with the highest variance in the TRAINING data (NaN-aware)."""

    def __init__(self, k=1000):
        self.k = k

    def fit(self, X, y=None):
        var = np.nanvar(np.asarray(X, dtype=float), axis=0)
        var = np.where(np.isnan(var), -1, var)
        self.idx_ = np.sort(np.argsort(var)[::-1][: min(self.k, X.shape[1])])
        return self

    def transform(self, X):
        return np.asarray(X, dtype=float)[:, self.idx_]


def clinical_pipe():
    return Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])


def protein_pipe(k, n_pcs):
    return Pipeline([("topvar", TopVariance(k)), ("impute", SimpleImputer(strategy="median")),
                     ("scale", StandardScaler()), ("pca", PCA(n_components=n_pcs, svd_solver="full"))])


def build_model(name, clin_cols, prot_cols, args):
    lr = LogisticRegression(C=args.C, max_iter=5000)
    if name == "clinical_lr":
        pre = ColumnTransformer([("clin", clinical_pipe(), clin_cols)])
    elif name == "prot_pca_lr":
        pre = ColumnTransformer([("prot", protein_pipe(args.top_k, args.n_pcs), prot_cols)])
    else:
        pre = ColumnTransformer([("clin", clinical_pipe(), clin_cols),
                                 ("prot", protein_pipe(args.top_k, args.n_pcs), prot_cols)])
    return Pipeline([("pre", pre), ("lr", lr)])


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default="y_stage", choices=["y_stage", "y_grade"])
    ap.add_argument("--top-k", type=int, default=1000, help="most variable proteins kept (per training fold)")
    ap.add_argument("--n-pcs", type=int, default=20)
    ap.add_argument("--C", type=float, default=1.0, help="inverse L2 regularization strength")
    ap.add_argument("--min-patients", type=int, default=5, help="min patients for a comorbidity flag")
    ap.add_argument("--no-nested", action="store_true", help="skip tuning; use fixed --C and --n-pcs")
    ap.add_argument("--inner-metric", default="neg_log_loss", choices=["neg_log_loss", "roc_auc"])
    args = ap.parse_args()

    labels = read(PAT / "labels.parquet")
    folds = read(PAT / "folds.parquet")
    clin = clinical_features(args.min_patients)
    prot = proteomics_matrix().add_prefix("prot_")

    df = (labels[["patient_id", args.label]].merge(folds, on="patient_id")
          .set_index("patient_id").join(clin).join(prot, how="inner"))
    df = df[df[args.label].notna()]
    clin_cols = [c for c in clin.columns]
    prot_cols = [c for c in prot.columns]
    assert not (set(clin_cols) | set(prot_cols)) & LABEL_COLS, "a label column leaked into the features"
    print(f"Patients: {len(df)}  ({args.label}: {df[args.label].value_counts().to_dict()})  "
          f"clinical features: {len(clin_cols)}  proteins: {len(prot_cols)}")

    y = df[args.label].astype(int).to_numpy()
    feats = clin_cols + prot_cols
    print("Mode:", "fixed settings (C=%g, %d PCs)" % (args.C, args.n_pcs) if args.no_nested
          else f"nested CV, inner 5-fold on training patients only, selecting by {args.inner_metric}")
    preds, aucs, chosen = [], [], []
    for name in MODELS:
        oof = np.full(len(df), np.nan)
        for f in sorted(df["fold"].unique()):
            tr, te = (df["fold"] != f).to_numpy(), (df["fold"] == f).to_numpy()
            model = build_model(name, clin_cols, prot_cols, args)
            if not args.no_nested:
                inner = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
                model = GridSearchCV(model, param_grid(name), scoring=args.inner_metric, cv=inner, n_jobs=-1)
            model.fit(df.loc[tr, feats], y[tr])
            oof[te] = model.predict_proba(df.loc[te, feats])[:, 1]
            aucs.append({"model": name, "fold": int(f), "auroc": roc_auc_score(y[te], oof[te]),
                         "log_loss": log_loss(y[te], oof[te], labels=[0, 1])})
            if not args.no_nested:
                chosen.append({"model": name, "fold": int(f), **{k.split("__")[-1]: v
                                                                for k, v in model.best_params_.items()}})
        preds.append(pd.DataFrame({"patient_id": df.index, "model": name, "fold": df["fold"].astype(int).values,
                                   "risk": oof}))

    preds, aucs = pd.concat(preds, ignore_index=True), pd.DataFrame(aucs)
    chosen = pd.DataFrame(chosen)

    # results summary
    print(f"\nE2 baselines ({args.label}), AUROC over 5 folds:")
    for name in MODELS:
        a = aucs[aucs["model"] == name]["auroc"]
        p = preds[preds["model"] == name]
        r = p.set_index("patient_id").loc[df.index, "risk"]
        print(f"  {name:12s} mean {a.mean():.3f} +/- {a.std():.3f}   (pooled out-of-fold {roc_auc_score(y, r):.3f})"
              f"   log loss {log_loss(y, r):.3f}   risk range {r.min():.2f}-{r.max():.2f}")
    base = y.mean()
    print(f"  (reference: predicting the base rate {base:.2f} for everyone gives log loss "
          f"{log_loss(y, np.full(len(y), base)):.3f})")
    if len(chosen):
        print("\nSettings chosen in each outer fold (inner CV on training patients only):")
        print(chosen.to_string(index=False))

    if args.label != "y_stage":  # contract files are for y_stage; keep side analyses separate
        write(preds, PRED.with_name(f"predictions_{args.label}.parquet"))
        aucs.to_csv(E2.with_name(f"e2_{args.label}.csv"), index=False)
        if len(chosen):
            chosen.to_csv(E2.with_name(f"e2_params_{args.label}.csv"), index=False)
        return

    # merge into the shared files, keeping any rows other models (e.g. gnn) already wrote
    try:
        old = read(PRED)
        preds = pd.concat([old[~old["model"].isin(MODELS)], preds], ignore_index=True)
    except FileNotFoundError:
        pass
    write(preds, PRED)
    E2.parent.mkdir(parents=True, exist_ok=True)
    if E2.exists():
        old = pd.read_csv(E2)
        aucs = pd.concat([old[~old["model"].isin(MODELS)], aucs], ignore_index=True)
    aucs.to_csv(E2, index=False)
    print(f"  wrote {E2}  {aucs.shape}")
    if len(chosen):
        chosen.to_csv(E2.with_name("e2_params.csv"), index=False)
        print(f"  wrote {E2.with_name('e2_params.csv')}  {chosen.shape}")


if __name__ == "__main__":
    main()