"""Cohort PCA: GNN patient embeddings vs raw proteomics (exploratory subtyping).   python -m eval.cohort_pca

Unsupervised, no labels used to fit anything:
  gnn   the GNN-A patient vectors E2 uses (models.e2_patients.gnn_features: weighted block means of frozen node
        embeddings for the patient's phenotypes / top-50 up / top-50 down proteins), standardised, PCA
  prot  CPTAC umich tumour proteomics (proteins in >= 80% of tumours), median-imputed, top-1000 variance,
        standardised, PCA
Described afterwards (colouring / association only, never an input):
  driver-mutation group from patient_mutations (BAP1 > PBRM1 > SETD2 > VHL only > none), an interferon/antigen-
  presentation protein score (mean z of STAT1, GBP1, GBP4, GBP5, CD74, TAP1, TAP2, PSME1, PSME2, HLA-DRA, B2M, IDO1
  where measured), and stage / grade (descriptive AUROC of each PC; this is not a prediction and not tuned).
Also: k-means (k = 2..5 on the first 10 PCs; k by silhouette) vs mutation groups (ARI / NMI), top proteins
correlated with each PC, and how much the two spaces agree (shared 10-nearest-neighbours per patient).
Outputs: eval/results/cohort_pca.json, eval/results/cohort_pca.png, eval/results/cohort_pca_scores.csv
`--plot-only` redraws the figure from the CSV + JSON (no data/processed needed).
"""
import json

import numpy as np
import pandas as pd
from scipy.stats import kruskal, mannwhitneyu, spearmanr
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.metrics import adjusted_rand_score, normalized_mutual_info_score, roc_auc_score, silhouette_score
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

from common.contracts import P

IFN = ['STAT1', 'GBP1', 'GBP4', 'GBP5', 'CD74', 'TAP1', 'TAP2', 'PSME1', 'PSME2', 'HLA-DRA', 'B2M', 'IDO1']
DRIVERS = ['BAP1', 'PBRM1', 'SETD2']
DEMO = ['C3L-00416', 'C3N-00244', 'C3L-00790']
GROUP_COLORS = {'BAP1': '#2a78d6', 'PBRM1': '#eb6834', 'SETD2': '#1baf7a', 'VHL only': '#eda100', 'none of these': '#a8a7a1'}
SEQ = ['#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b']  # dataviz reference blue ramp, light -> dark
INK, INK2, GRID, SURFACE = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'

def make_figure(pcs, var, group, ifn, pids, out):
    """2 x 2 small multiples (rows: mutation group / IFN score; cols: GNN / proteomics) -> eval/results/cohort_pca.png"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    from matplotlib.lines import Line2D

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 11), facecolor=SURFACE)
    fig.subplots_adjust(hspace=0.42, wspace=0.28)
    cmap = LinearSegmentedColormap.from_list('seq', SEQ)
    title = {'gnn': 'GNN patient embedding', 'prot': 'Raw proteomics (top-1000 proteins)'}
    for ci, k in enumerate(['gnn', 'prot']):
        S, v = pcs[k], var[k]
        for ri in range(2):
            ax = axes[ri, ci]; ax.set_facecolor(SURFACE)
            if ri == 0:
                for g, c in GROUP_COLORS.items():
                    m = (group == g).values
                    ax.scatter(S[m, 0], S[m, 1], s=46, color=c, edgecolor=SURFACE, linewidth=1.5, zorder=3, label=f'{g} (n={int(m.sum())})')
            else:
                sc = ax.scatter(S[:, 0], S[:, 1], s=46, c=ifn.values, cmap=cmap, edgecolor=SURFACE, linewidth=1.5, zorder=3)
            for pid in DEMO:
                i = pids.index(pid)
                ax.scatter(S[i, 0], S[i, 1], s=130, facecolor='none', edgecolor=INK, linewidth=1.4, zorder=4)
                ax.annotate(pid, (S[i, 0], S[i, 1]), xytext=(7, 6), textcoords='offset points', fontsize=8.5, color=INK)
            ax.set_xlabel(f'PC1 ({v[0]:.0%} of variance)', color=INK2); ax.set_ylabel(f'PC2 ({v[1]:.0%})', color=INK2)
            ax.grid(color=GRID, lw=0.8); ax.set_axisbelow(True)
            for s in ax.spines.values():
                s.set_visible(False)
            ax.tick_params(colors=INK2, length=0)
            d = out['spaces'][k]['pcs']['PC1']
            kp = d['mutation_group_kruskal_p']
            sub = ((f'PC1 vs mutation group: Kruskal p={kp:.2g}' if kp is not None else 'PC1 vs mutation group: n/a') if ri == 0
                   else f"PC1 vs IFN score: Spearman {d['ifn_spearman']:+.2f}")
            ax.set_title(f'{title[k]}\n{sub}', loc='left', fontsize=10.5, color=INK)
    axes[0, 1].legend(loc='upper left', bbox_to_anchor=(1.01, 1), frameon=False, fontsize=9, labelcolor=INK, title='Driver mutation',
                      title_fontsize=9.5)
    cb = fig.colorbar(sc, ax=axes[1, :].tolist(), shrink=0.8, pad=0.02); cb.set_label('Interferon / antigen-presentation protein score (z)', color=INK2)
    cb.outline.set_visible(False); cb.ax.tick_params(colors=INK2, length=0)
    fig.suptitle(f'Cohort PCA: {len(pids)} CPTAC ccRCC tumours (unsupervised; colours added afterwards)', x=0.02, ha='left',
                 fontsize=13.5, color=INK)
    fig.text(0.02, 0.005, 'Ringed: demo patients. Mutation groups by priority BAP1 > PBRM1 > SETD2 > VHL only. '
             'No labels or mutation calls were used to build either space.', fontsize=8.5, color=INK2)
    fig.savefig(P.results / 'cohort_pca.png', dpi=150, facecolor=SURFACE, bbox_inches='tight')


import sys
if '--plot-only' in sys.argv:  # redraw from cohort_pca_scores.csv + cohort_pca.json (no data/processed needed)
    _sc = pd.read_csv(P.results / 'cohort_pca_scores.csv'); out = json.loads((P.results / 'cohort_pca.json').read_text())
    pids = _sc.patient_id.tolist()
    make_figure({k: _sc[[f'{k}_pc1', f'{k}_pc2']].values for k in ('gnn', 'prot')},
                {k: out['spaces'][k]['explained_variance_pc1_3'] for k in ('gnn', 'prot')},
                pd.Series(_sc.mutation_group.values, index=pids), pd.Series(_sc.ifn_score.values, index=pids), pids, out)
    print('wrote', P.results / 'cohort_pca.png'); sys.exit(0)

from models.e2_patients import TopVar, gnn_features, load_proteomics  # noqa: E402 (torch; compute path only)
from models.gnn_a_api import GnnA  # noqa: E402

ph = pd.read_parquet(P.patient_phenotypes); pr = pd.read_parquet(P.patient_proteins)
lab = pd.read_parquet(P.labels).set_index('patient_id')
mu = pd.read_parquet(P.patients_dir / 'patient_mutations.parquet')
pids = pd.read_parquet(P.folds).patient_id.tolist()

# ---- the two spaces (no labels)
Xg, _, _ = gnn_features(GnnA(str(P.processed), 'main'), pids, ph, pr)
prot = load_proteomics(P.processed / 'cptac' / 'umich_proteomics.parquet').reindex(pids)
Xp = SimpleImputer(strategy='median').fit_transform(prot.values.astype(float))
Xp_top = TopVar(1000).fit(Xp).transform(Xp)
spaces = {'gnn': StandardScaler().fit_transform(Xg), 'prot': StandardScaler().fit_transform(Xp_top)}
pcs, var = {}, {}
for k, X in spaces.items():
    p = PCA(10, random_state=0).fit(X)
    pcs[k], var[k] = p.transform(X), p.explained_variance_ratio_

# ---- descriptors (after the fact)
sym = mu['symbol'] if 'symbol' in mu else mu['name']   # patient_mutations: gene symbol is in `name`
mutated = {g: set(mu.patient_id[sym == g]) for g in DRIVERS + ['VHL']}
group = pd.Series('none of these', index=pids)
for g in ['VHL'] + DRIVERS[::-1]:          # priority BAP1 > PBRM1 > SETD2 > VHL only
    group[group.index.isin(mutated[g])] = 'VHL only' if g == 'VHL' else g
pz = pd.DataFrame(StandardScaler().fit_transform(Xp), index=pids, columns=prot.columns)
ifn_cols = [c for c in IFN if c in pz.columns]
ifn = pz[ifn_cols].mean(axis=1)

out = {'n_patients': len(pids), 'ifn_proteins_used': ifn_cols, 'mutation_groups': group.value_counts().to_dict(), 'spaces': {}}
for k in spaces:
    S = pcs[k]
    d = {'explained_variance_pc1_3': [round(float(v), 3) for v in var[k][:3]], 'pcs': {}}
    for j in range(3):
        s = S[:, j]
        rho, p_rho = spearmanr(s, ifn.values)
        grp = [s[(group == g).values] for g in GROUP_COLORS if (group == g).sum() >= 5]
        b, pb = s[(group == 'BAP1').values], s[(group == 'PBRM1').values]
        pc = {'ifn_spearman': round(float(rho), 3), 'ifn_p': float(p_rho),
              'mutation_group_kruskal_p': float(kruskal(*grp).pvalue) if len(grp) > 1 else None,
              'bap1_vs_pbrm1_mannwhitney_p': float(mannwhitneyu(b, pb).pvalue) if len(b) >= 3 and len(pb) >= 3 else None}
        for lbl in ('y_stage', 'y_grade'):
            y = lab.loc[pids, lbl]
            ok = y.notna().values
            auc = roc_auc_score(y[ok].astype(int), s[ok])
            pc[f'{lbl}_auroc_descriptive'] = round(float(max(auc, 1 - auc)), 3)
        r = np.array([spearmanr(s, pz[c].values, nan_policy='omit')[0] for c in pz.columns])
        o = np.argsort(r)
        pc['top_pos_proteins'] = [(pz.columns[i], round(float(r[i]), 2)) for i in o[::-1][:8]]
        pc['top_neg_proteins'] = [(pz.columns[i], round(float(r[i]), 2)) for i in o[:8]]
        d['pcs'][f'PC{j + 1}'] = pc
    Z = S[:, :10]
    sil = {kk: float(silhouette_score(Z, KMeans(kk, n_init=20, random_state=0).fit_predict(Z))) for kk in range(2, 6)}
    kbest = max(sil, key=sil.get)
    cl = KMeans(kbest, n_init=20, random_state=0).fit_predict(Z)
    d['kmeans'] = {'silhouette_by_k': {str(a): round(b, 3) for a, b in sil.items()}, 'k': kbest,
                   'sizes': np.bincount(cl).tolist(),
                   'ari_vs_mutation_group': round(float(adjusted_rand_score(group.values, cl)), 3),
                   'nmi_vs_mutation_group': round(float(normalized_mutual_info_score(group.values, cl)), 3),
                   'crosstab_cluster_x_group': pd.crosstab(cl, group.values).to_dict(),
                   'ifn_mean_by_cluster': {str(c): round(float(ifn.values[cl == c].mean()), 2) for c in range(kbest)}}
    out['spaces'][k] = d

nb = {k: NearestNeighbors(n_neighbors=11).fit(pcs[k][:, :10]).kneighbors(return_distance=False)[:, 1:] for k in spaces}
shared = [len(set(a) & set(b)) / 10 for a, b in zip(nb['gnn'], nb['prot'])]
rng = np.random.default_rng(0)
null = [np.mean([len(set(a) & set(rng.choice(len(pids), 10, replace=False))) / 10 for a in nb['gnn']]) for _ in range(200)]
out['agreement'] = {'mean_shared_10nn': round(float(np.mean(shared)), 3), 'random_expectation': round(float(np.mean(null)), 3),
                    'max_abs_corr_gnn_pc1_vs_prot_pcs': round(float(max(abs(spearmanr(pcs['gnn'][:, 0], pcs['prot'][:, j])[0]) for j in range(3))), 3)}
(P.results / 'cohort_pca.json').write_text(json.dumps(out, indent=1, default=str))
pd.DataFrame({'patient_id': pids, 'gnn_pc1': pcs['gnn'][:, 0], 'gnn_pc2': pcs['gnn'][:, 1], 'prot_pc1': pcs['prot'][:, 0],
              'prot_pc2': pcs['prot'][:, 1], 'mutation_group': group.values, 'ifn_score': ifn.values}).round(4) \
    .to_csv(P.results / 'cohort_pca_scores.csv', index=False)

make_figure(pcs, var, group, ifn, pids, out)

for k in spaces:
    d = out['spaces'][k]
    print(f"== {k}: var PC1-3 {d['explained_variance_pc1_3']} | kmeans k={d['kmeans']['k']} sizes {d['kmeans']['sizes']} "
          f"ARI vs mutation {d['kmeans']['ari_vs_mutation_group']} | IFN by cluster {d['kmeans']['ifn_mean_by_cluster']}")
    for j, pc in d['pcs'].items():
        print(f"   {j}: IFN rho {pc['ifn_spearman']:+.2f} (p={pc['ifn_p']:.1g}) | mut kruskal p={pc['mutation_group_kruskal_p']} | "
              f"BAP1/PBRM1 p={pc['bap1_vs_pbrm1_mannwhitney_p']} | stage AUC {pc['y_stage_auroc_descriptive']} grade {pc['y_grade_auroc_descriptive']}")
        print(f"       + {[p for p, _ in pc['top_pos_proteins'][:6]]}  - {[p for p, _ in pc['top_neg_proteins'][:6]]}")
print('agreement', out['agreement'], '| groups', out['mutation_groups'])
