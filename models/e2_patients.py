"""E2: does putting the patient in the graph help?   python -m models.e2_patients

GNN-A step 3 (patient outcome head) + baselines, all OUT OF FOLD on the shared folds.parquet (never re-split).
Primary label y_stage (CONTRACTS.md); y_grade and y_recur reported as extended.

Models (predictions.parquet uses the contract's model names):
  clinical_lr     age, sex, BMI, ever_smoker, pack_years + phenotype flags (curies seen in >= 5 patients)
  prot_pca_lr     full CPTAC umich proteomics (tumours; proteins present in >= 80%) -> top-1000 variance -> PCA20, fit in-fold
  concat_lr       clinical + proteomics PCA
  gnn             frozen GNN-A `main` embeddings. A patient is attached inductively, as in GnnA: per relation block, the
                  signed weighted mean of its neighbours' (unit-normalised) embeddings -> [phenotypes (w=1) | proteins up
                  (w=z) | proteins down (w=|z|)] = 384 features -> standardise -> logistic regression
  gnn_no_dismech  same with GNN-A `no_dismech` embeddings (ablation: what curation adds)
  gnn_mut         gnn + a block of mutated genes (patient_mutations, w=1)   [ablation; not a contract model value]
Every LR picks C by an inner 4-fold CV on the training folds only (nested CV). GNN weights are frozen: ~88 training
patients per fold would overfit end-to-end training.

contributions.parquet (gnn, primary label): because the gnn head is linear on block means, the out-of-fold logit splits
exactly into per-neighbour terms: score_j = sum_{d in block(j)} beta_d * a_j * h_jd / sigma_d with a_j = w_j / sum|w| in
its block (the intercept absorbs the centring). Top 10 per patient by score (risk-increasing first).
"""
from __future__ import annotations

import argparse, json, time

import numpy as np
import pandas as pd
import torch.nn.functional as F
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegressionCV
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from common.contracts import P, PRIMARY_LABEL, patient_neighbours
from models.gnn_a_api import GnnA

CONTRACT_MODELS = ['clinical_lr', 'prot_pca_lr', 'concat_lr', 'gnn', 'gnn_no_dismech']
EXTRA_MODELS = ['gnn_mut']
LABELS = [PRIMARY_LABEL, 'y_grade', 'y_recur']
CS = [0.001, 0.01, 0.1, 1.0]


class TopVar(BaseEstimator, TransformerMixin):
    def __init__(self, k=1000):
        self.k = k

    def fit(self, X, y=None):
        self.idx_ = np.argsort(-np.nanvar(X, axis=0))[:self.k]
        return self

    def transform(self, X):
        return X[:, self.idx_]


def lr():
    return LogisticRegressionCV(Cs=CS, cv=StratifiedKFold(4, shuffle=True, random_state=0), scoring='roc_auc',
                                class_weight='balanced', max_iter=5000)


# ------------------------------------------------------------------ data
def load_proteomics(cache):
    if cache.exists():
        return pd.read_parquet(cache)
    import cptac
    prot = cptac.Ccrcc().get_proteomics('umich')
    if isinstance(prot.columns, pd.MultiIndex):
        prot.columns = prot.columns.get_level_values(0)
    prot = prot.T.groupby(level=0).mean().T
    prot = prot[~prot.index.astype(str).str.endswith('.N')]
    prot = prot.loc[:, prot.notna().mean() >= 0.8]
    prot.index = prot.index.astype(str)
    cache.parent.mkdir(parents=True, exist_ok=True)
    prot.to_parquet(cache)
    return prot


def clinical_features(pat, ph):
    X = pd.DataFrame(index=pat.patient_id)
    for c in ['age', 'bmi', 'pack_years']:
        X[c] = pd.to_numeric(pat.set_index('patient_id')[c], errors='coerce')
    X['sex_male'] = (pat.set_index('patient_id').sex.astype(str).str.lower().str.startswith('m')).astype(float)
    X['ever_smoker'] = pd.to_numeric(pat.set_index('patient_id').ever_smoker, errors='coerce').astype(float)
    common = ph.curie.value_counts()
    for c in common[common >= 5].index:
        X[f'ph_{c}'] = X.index.isin(ph.patient_id[ph.curie == c]).astype(float)
    return X


def gnn_features(g, pids, ph, pr, mu=None):
    """Block means of unit-normalised neighbour embeddings + the per-neighbour bookkeeping for contributions."""
    hn = {t: F.normalize(v, dim=-1).numpy() for t, v in g.h.items()}
    d = next(iter(hn.values())).shape[1]
    blocks = ['phenotype', 'up', 'down'] + (['mut'] if mu is not None else [])
    X = np.zeros((len(pids), d * len(blocks)), np.float32)
    book = {}
    for i, pid in enumerate(pids):
        curies, w = patient_neighbours(pid, ph, pr)
        nb = {'phenotype': [], 'up': [], 'down': [], 'mut': []}
        n_ph = int((ph.patient_id == pid).sum())
        for j, (c, wj) in enumerate(zip(curies, w)):
            gi = g.cur2g.get(c)
            if gi is None:
                continue
            blk = 'phenotype' if j < n_ph else ('up' if wj > 0 else 'down')
            nb[blk].append((c, gi, abs(float(wj))))
        if mu is not None:
            for c in mu.curie[mu.patient_id == pid]:
                gi = g.cur2g.get(c)
                if gi is not None:
                    nb['mut'].append((c, gi, 1.0))
        book[pid] = {}
        for b_i, blk in enumerate(blocks):
            items = nb[blk]
            if not items:
                continue
            ws = np.array([x[2] for x in items]); a = ws / ws.sum()
            H = np.stack([hn[g.NT[x[1]]][g.LIDX[x[1]]] for x in items])
            X[i, b_i * d:(b_i + 1) * d] = a @ H
            book[pid][blk] = (b_i, [x[0] for x in items], a, H)
    return X, blocks, book


# ------------------------------------------------------------------ CV
def run_cv(name, make_model, X, y, folds):
    risk = np.full(len(y), np.nan)
    fitted = {}
    for f in sorted(np.unique(folds)):
        tr, te = folds != f, folds == f
        m = make_model().fit(X[tr], y[tr])
        risk[te] = m.predict_proba(X[te])[:, 1]
        fitted[f] = m
    per_fold = [roc_auc_score(y[folds == f], risk[folds == f]) for f in sorted(np.unique(folds))]
    return risk, per_fold, fitted


def boot_ci(y, r, n=2000, seed=0):
    rng = np.random.default_rng(seed); vals = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(np.unique(y[i])) == 2:
            vals.append(roc_auc_score(y[i], r[i]))
    return [round(float(np.percentile(vals, 2.5)), 3), round(float(np.percentile(vals, 97.5)), 3)]


def paired_boot(y, r1, r2, n=2000, seed=0):
    rng = np.random.default_rng(seed); d = []
    for _ in range(n):
        i = rng.integers(0, len(y), len(y))
        if len(np.unique(y[i])) == 2:
            d.append(roc_auc_score(y[i], r1[i]) - roc_auc_score(y[i], r2[i]))
    d = np.array(d)
    return dict(mean_diff=round(float(d.mean()), 3), ci95=[round(float(np.percentile(d, 2.5)), 3), round(float(np.percentile(d, 97.5)), 3)],
                p_le_0=round(float((d <= 0).mean()), 3))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(P.results))
    args = ap.parse_args()
    T0 = time.time()
    import os
    os.makedirs(args.out, exist_ok=True); os.makedirs('models/results', exist_ok=True)
    pat = pd.read_parquet(P.patients); ph = pd.read_parquet(P.patient_phenotypes); pr = pd.read_parquet(P.patient_proteins)
    lab = pd.read_parquet(P.labels).set_index('patient_id'); fold = pd.read_parquet(P.folds).set_index('patient_id').fold
    mu = pd.read_parquet(P.patients_dir / 'patient_mutations.parquet')
    pids = fold.index.tolist()
    folds = fold.loc[pids].values

    prot = load_proteomics(P.processed / 'cptac' / 'umich_proteomics.parquet').reindex(pids)
    clin = clinical_features(pat, ph).reindex(pids)
    print(f'patients {len(pids)} | proteomics {prot.shape} ({int(prot.notna().any(axis=1).sum())} with data) | clinical {clin.shape}', flush=True)
    g_main, g_nodm = GnnA(str(P.processed), 'main'), GnnA(str(P.processed), 'no_dismech')
    Xg, blocks_g, book_g = gnn_features(g_main, pids, ph, pr)
    Xn, _, _ = gnn_features(g_nodm, pids, ph, pr)
    Xm, _, _ = gnn_features(g_main, pids, ph, pr, mu)
    nc = clin.shape[1]
    Xc, Xp = clin.values.astype(float), prot.values.astype(float)
    Xcp = np.hstack([Xc, Xp])
    clin_pipe = lambda: make_pipeline(SimpleImputer(strategy='median'), StandardScaler(), lr())
    prot_pipe = lambda: make_pipeline(SimpleImputer(strategy='median'), TopVar(1000), StandardScaler(), PCA(20, random_state=0), lr())
    concat_pipe = lambda: make_pipeline(ColumnTransformer([
        ('c', make_pipeline(SimpleImputer(strategy='median'), StandardScaler()), list(range(nc))),
        ('p', make_pipeline(SimpleImputer(strategy='median'), TopVar(1000), StandardScaler(), PCA(20, random_state=0)), list(range(nc, Xcp.shape[1])))]), lr())
    gnn_pipe = lambda: make_pipeline(StandardScaler(), lr())
    MODELS = {'clinical_lr': (clin_pipe, Xc), 'prot_pca_lr': (prot_pipe, Xp), 'concat_lr': (concat_pipe, Xcp),
              'gnn': (gnn_pipe, Xg), 'gnn_no_dismech': (gnn_pipe, Xn), 'gnn_mut': (gnn_pipe, Xm)}

    rows, preds, summary, fitted_gnn = [], [], {}, None
    for label in LABELS:
        y = lab.loc[pids, label].values
        ok = ~pd.isna(y)
        y, f_ = y[ok].astype(int), folds[ok]
        summary[label] = {'n': int(ok.sum()), 'positives': int(y.sum()), 'models': {}}
        risks = {}
        for name, (mk, X) in MODELS.items():
            risk, per_fold, fitted = run_cv(name, mk, X[ok], y, f_)
            risks[name] = risk
            for f, a in zip(sorted(np.unique(f_)), per_fold):
                rows.append(dict(label=label, model=name, fold=int(f), auroc=round(float(a), 4)))
            summary[label]['models'][name] = dict(auroc_mean=round(float(np.mean(per_fold)), 3), auroc_sd=round(float(np.std(per_fold)), 3),
                                                  pooled_oof_auroc=round(float(roc_auc_score(y, risk)), 3), pooled_ci95=boot_ci(y, risk))
            if label == PRIMARY_LABEL and name in CONTRACT_MODELS:
                for pid, f, r in zip(np.array(pids)[ok], f_, risk):
                    preds.append(dict(patient_id=pid, model=name, fold=int(f), risk=float(r)))
            if label == PRIMARY_LABEL and name == 'gnn':
                fitted_gnn = fitted
            print(f'{label:8s} {name:15s} AUROC {np.mean(per_fold):.3f} ± {np.std(per_fold):.3f} | pooled {roc_auc_score(y, risk):.3f} ({time.time() - T0:.0f}s)', flush=True)
        base = max(['clinical_lr', 'prot_pca_lr', 'concat_lr'], key=lambda m: summary[label]['models'][m]['pooled_oof_auroc'])
        summary[label]['best_baseline'] = base
        summary[label]['paired_bootstrap_vs_best_baseline'] = {m: paired_boot(y, risks[m], risks[base]) for m in ['gnn', 'gnn_no_dismech', 'gnn_mut']}
        summary[label]['paired_bootstrap_gnn_vs_no_dismech'] = paired_boot(y, risks['gnn'], risks['gnn_no_dismech'])
        summary[label]['paired_bootstrap_gnn_mut_vs_gnn'] = paired_boot(y, risks['gnn_mut'], risks['gnn'])

    # contributions (gnn, primary label, out-of-fold model for each patient)
    d = Xg.shape[1] // len(blocks_g)
    contrib = []
    for pid, f in zip(pids, folds):
        pipe = fitted_gnn[f]
        sc, clf = pipe.named_steps['standardscaler'], pipe.named_steps['logisticregressioncv']
        beta = clf.coef_[0] / sc.scale_
        for blk, (b_i, curies, a, H) in book_g[pid].items():
            s = a * (H @ beta[b_i * d:(b_i + 1) * d])
            contrib += [dict(patient_id=pid, curie=c, score=float(v)) for c, v in zip(curies, s)]
    C = pd.DataFrame(contrib).groupby(['patient_id', 'curie'], as_index=False).score.sum()
    C = C.sort_values(['patient_id', 'score'], ascending=[True, False])
    C['rank'] = C.groupby('patient_id').cumcount() + 1
    C = C[C['rank'] <= 10].reset_index(drop=True)

    P.predictions.parent.mkdir(parents=True, exist_ok=True)
    pred = pd.DataFrame(preds)
    pred.to_parquet(P.predictions, index=False)
    C.to_parquet(P.predictions.parent / 'contributions.parquet', index=False)
    E = pd.DataFrame(rows)
    E[E.label == PRIMARY_LABEL][['model', 'fold', 'auroc']].to_csv(f'{args.out}/e2.csv', index=False)
    E.to_csv(f'{args.out}/e2_extended.csv', index=False)
    pred.to_csv('models/results/predictions_y_stage.csv', index=False)
    C.to_csv('models/results/contributions_gnn.csv', index=False)
    summary['notes'] = __doc__
    summary['runtime_s'] = round(time.time() - T0)
    json.dump(summary, open(f'{args.out}/e2_summary.json', 'w'), indent=1, default=str)
    try:
        import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
        order = CONTRACT_MODELS + EXTRA_MODELS
        fig, axes = plt.subplots(1, len(LABELS), figsize=(5 * len(LABELS), 4), squeeze=False)
        for ax, label in zip(axes[0], LABELS):
            mm = summary[label]['models']
            ax.barh(order, [mm[m]['auroc_mean'] for m in order], xerr=[mm[m]['auroc_sd'] for m in order], capsize=3,
                    color=['#999999'] * 3 + ['#4C72B0', '#8FAADC', '#DD8452'])
            ax.axvline(0.5, ls='--', c='grey'); ax.set_xlim(0.3, 1); ax.invert_yaxis()
            ax.set_title(f'{label} (n={summary[label]["n"]}, pos={summary[label]["positives"]})'); ax.set_xlabel('AUROC (5 shared folds)')
        plt.tight_layout(); plt.savefig(f'{args.out}/e2.png', dpi=150)
    except Exception as ex:
        print('plot skipped:', ex)
    print(json.dumps({k: v for k, v in summary.items() if k != 'notes'}, indent=1, default=str))


if __name__ == '__main__':
    main()
