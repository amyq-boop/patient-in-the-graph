#!/usr/bin/env python3
"""
Patient stratification -> clusters.json (one per cancer).

  python -m models.stratify --cancer ccrcc
  python -m models.stratify --cancer brca
  python -m models.stratify --cancer ccrcc --gnn-emb path/to/patient_embeddings.parquet   # adds the GNN comparison

Unsupervised ONLY: no stage, grade, recurrence or survival is used to build features, choose k or fit clusters.
Stage/grade are summarized per cluster AFTERWARDS, labelled descriptive.

Method (proteomics-only):
  tumor protein matrix -> drop proteins missing in >20% of tumors -> median impute -> top-1000 variance -> standardize
  -> PCA(20) -> k-means for k=2..6; k chosen by silhouette (all k reported).
Stability: 100 subsamples of 80% of patients, full pipeline refit each time.
  - adjusted Rand index (ARI) vs the reference clustering on the shared patients
  - per-cluster Jaccard (Hennig's clusterboot): >0.75 stable, 0.6-0.75 pattern, <0.5 dissolved
  - per-patient co-assignment rate with its reference cluster-mates
Signatures (statistics, not graph lookups):
  - proteins: Welch t-test cluster vs rest on every filtered protein, Benjamini-Hochberg q
  - pathways: hypergeometric test of each cluster's significant up-proteins against PrimeKG pathway membership
    (background = measured proteins with any pathway), BH q. Graph edges give membership; the test gives enrichment.
GNN comparison (if --gnn-emb): same clustering on the embeddings (k matched and k by silhouette), its stability, and
  ARI between proteomics and GNN clusterings.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler

PROC = Path("data/processed")
COHORTS = {
    "ccrcc": {"prot": [PROC / "patients/proteomics_tumor", PROC / "ccrcc_prot_tumor"], "patients": PROC / "patients"},
    "brca": {"prot": [PROC / "brca/ccrcc_prot_tumor"], "patients": PROC / "brca/patients"},
}
MAX_MISSING, TOP_VAR, N_PCS, K_RANGE, N_BOOT, SUB_FRAC, SEED = 0.2, 1000, 20, range(2, 7), 100, 0.8, 0


def read(path):
    path = Path(path)
    for p, fn in ((path.with_suffix(".parquet"), pd.read_parquet), (path.with_suffix(".csv"), pd.read_csv)):
        if p.exists():
            return fn(p)
    return None


def load_matrix(cfg):
    for p in cfg["prot"]:
        m = read(p)
        if m is not None:
            if "sample_id" in m.columns:
                m = m.set_index("sample_id")
            m.index = m.index.astype(str)
            print(f"proteomics: {p} {m.shape}")
            return m.apply(pd.to_numeric, errors="coerce")
    raise SystemExit(f"no tumor proteomics matrix found in {cfg['prot']}")


def embed(X, n_pcs=N_PCS, top_var=TOP_VAR):
    """Unsupervised feature pipeline, fit on whatever rows are given (refit inside every bootstrap)."""
    X = X.loc[:, X.notna().mean() >= 1 - MAX_MISSING]
    X = X.fillna(X.median())
    X = X[X.var().sort_values(ascending=False).index[:top_var]]
    Z = StandardScaler().fit_transform(X)
    return PCA(n_components=min(n_pcs, Z.shape[0] - 1, Z.shape[1]), random_state=SEED).fit_transform(Z)


def kmeans(E, k):
    return KMeans(n_clusters=k, n_init=50, random_state=SEED).fit_predict(E)


def choose_k(E):
    scores = [{"k": k, "silhouette": round(float(silhouette_score(E, kmeans(E, k))), 4)} for k in K_RANGE]
    return max(scores, key=lambda s: s["silhouette"])["k"], scores


def stability(X, ref, k, featurize):
    """Subsample, refit the WHOLE pipeline, compare with the reference labels on shared patients."""
    rng = np.random.default_rng(SEED)
    n, ids = len(ref), np.arange(len(ref))
    aris, jac = [], {c: [] for c in range(k)}
    same, together = np.zeros((n, n)), np.zeros((n, n))
    for _ in range(N_BOOT):
        sub = np.sort(rng.choice(ids, int(SUB_FRAC * n), replace=False))
        lab = kmeans(featurize(X.iloc[sub]), k)
        aris.append(adjusted_rand_score(ref[sub], lab))
        for c in range(k):
            a = set(sub[ref[sub] == c])
            if a:
                jac[c].append(max(len(a & set(sub[lab == b])) / len(a | set(sub[lab == b])) for b in range(k)))
        together[np.ix_(sub, sub)] += 1
        same[np.ix_(sub, sub)] += (lab[:, None] == lab[None, :])
    co = np.divide(same, together, out=np.full_like(same, np.nan), where=together > 0)
    per_patient = [float(np.nanmean(co[i, (ref == ref[i]) & (ids != i)])) if (ref == ref[i]).sum() > 1 else None
                   for i in range(n)]
    jmean = {c: round(float(np.mean(v)), 3) if v else None for c, v in jac.items()}
    return {"n_subsamples": N_BOOT, "subsample_fraction": SUB_FRAC,
            "ari_mean": round(float(np.mean(aris)), 3), "ari_sd": round(float(np.std(aris)), 3),
            "cluster_jaccard": jmean,
            "interpretation": "Jaccard >0.75 stable, 0.6-0.75 pattern worth noting, <0.5 not reproducible (Hennig 2007)"}, per_patient


def bh(p):
    p = np.asarray(p, float)
    q = np.full(len(p), np.nan)
    ok = ~np.isnan(p)
    if ok.sum():
        o = np.argsort(p[ok]); r = p[ok][o] * ok.sum() / np.arange(1, ok.sum() + 1)
        q_ok = np.minimum.accumulate(r[::-1])[::-1]
        tmp = np.empty(ok.sum()); tmp[o] = np.minimum(q_ok, 1); q[ok] = tmp
    return q


def signatures(X, labels, k, gene_curie, pathways, n_top=20, q_max=0.05):
    X = X.loc[:, X.notna().mean() >= 1 - MAX_MISSING]
    out = {}
    for c in range(k):
        a, b = X[labels == c], X[labels != c]
        t, p = stats.ttest_ind(a, b, axis=0, equal_var=False, nan_policy="omit")
        t, p = np.asarray(t, float), np.asarray(p, float)
        q = bh(p)
        diff = (a.mean() - b.mean()).to_numpy()
        df = pd.DataFrame({"symbol": X.columns, "t": t, "diff": diff, "q": q}).dropna()
        sig = df[df["q"] < q_max]

        def row(r):
            return {"symbol": r.symbol, "curie": gene_curie.get(r.symbol), "mean_diff": round(float(r.diff), 3),
                    "t": round(float(r.t), 2), "q": float(f"{r.q:.3g}")}
        up = sig[sig["t"] > 0].sort_values("t", ascending=False)
        down = sig[sig["t"] < 0].sort_values("t")
        out[c] = {"n_significant_up": int(len(up)), "n_significant_down": int(len(down)),
                  "signature_proteins_up": [row(r) for r in up.head(n_top).itertuples()],
                  "signature_proteins_down": [row(r) for r in down.head(n_top).itertuples()],
                  "signature_pathways": enrich(set(up["symbol"].head(100)), set(X.columns), pathways)}
    return out


def enrich(hits, measured, pathways, q_max=0.05, n_top=10):
    if not pathways or not hits:
        return []
    universe = {g for genes in pathways.values() for g in genes} & measured
    hits = hits & universe
    if not hits:
        return []
    rows = []
    for (curie, name), genes in pathways.items():
        g = genes & universe
        x = len(hits & g)
        if x >= 2:
            rows.append((curie, name, x, len(g), stats.hypergeom.sf(x - 1, len(universe), len(g), len(hits))))
    if not rows:
        return []
    df = pd.DataFrame(rows, columns=["curie", "name", "overlap", "pathway_size", "p"])
    df["q"] = bh(df["p"])
    df = df[df["q"] < q_max].sort_values("p").head(n_top)
    return [{"curie": r.curie, "name": r.name, "overlap": int(r.overlap), "pathway_size": int(r.pathway_size),
             "n_cluster_genes_tested": len(hits), "p": float(f"{r.p:.3g}"), "q": float(f"{r.q:.3g}")}
            for r in df.itertuples()]


def load_graph_annotations():
    nodes = read(PROC / "nodes")
    if nodes is None:
        print("WARNING: nodes.parquet not found; no curies or pathway enrichment")
        return {}, {}
    genes = nodes[(nodes["type"] == "gene") & nodes["curie"].str.startswith("NCBIGene:")]
    gene_curie = dict(zip(genes["name"], genes["curie"]))
    try:
        e = pd.read_parquet(PROC / "edges.parquet", columns=["src", "dst", "rel", "src_type", "dst_type"])
    except Exception:
        print("WARNING: edges.parquet not readable; no pathway enrichment")
        return gene_curie, {}
    e = e[(e["rel"] == "pathway_protein") & (e["src_type"] == "gene") & (e["dst_type"] == "pathway")]
    g = nodes.set_index("gidx")
    sym, pw_curie, pw_name = g["name"], g["curie"], g["name"]
    pathways = {}
    for d, grp in e.groupby("dst"):
        pathways[(pw_curie[d], pw_name[d])] = set(sym.reindex(grp["src"]).dropna())
    print(f"pathways: {len(pathways)} PrimeKG/Reactome pathways for enrichment")
    return gene_curie, pathways


def descriptive(labels, ids, lab_df, k):
    """Computed AFTER clustering; never used to build it."""
    if lab_df is None or lab_df.empty:
        return {}, None
    lab = lab_df.set_index("patient_id").reindex(ids)
    res, tests = {}, {}
    for col in ["y_stage", "y_grade"]:
        if col not in lab or lab[col].notna().sum() == 0:
            continue
        tab = pd.crosstab(labels, lab[col])
        for c in range(k):
            res.setdefault(c, {})[col] = {str(int(v)): int(tab.loc[c, v]) if c in tab.index else 0 for v in tab.columns}
        if tab.shape[0] > 1 and tab.shape[1] > 1:
            tests[col] = {"chi2_p": float(f"{stats.chi2_contingency(tab)[1]:.3g}"), "n": int(tab.values.sum())}
    return res, tests


def cluster_block(name, X_feat, featurize, ids):
    E = featurize(X_feat)
    k, ks = choose_k(E)
    labels = kmeans(E, k)
    stab, per_patient = stability(X_feat, labels, k, featurize)
    return {"name": name, "k": k, "k_selection": ks, "labels": labels, "stability": stab, "per_patient": per_patient, "E": E}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cancer", required=True, choices=sorted(COHORTS))
    ap.add_argument("--gnn-emb", help="patient embeddings (parquet/csv): patient_id + numeric columns")
    ap.add_argument("--out", help="default data/processed/clusters/clusters_<cancer>.json")
    args = ap.parse_args()
    cfg = COHORTS[args.cancer]
    out = Path(args.out or PROC / "clusters" / f"clusters_{args.cancer}.json")

    X = load_matrix(cfg)
    ids = list(X.index)
    gene_curie, pathways = load_graph_annotations()

    prot = cluster_block("proteomics_pca_kmeans", X, embed, ids)
    labels, k = prot["labels"], prot["k"]
    pca2 = PCA(n_components=2, random_state=SEED).fit(prot["E"])
    xy = pca2.transform(prot["E"])
    sigs = signatures(X, labels, k, gene_curie, pathways)
    desc, desc_tests = descriptive(labels, ids, read(cfg["patients"] / "labels"), k)
    print(f"proteomics clustering: k={k}, sizes={np.bincount(labels).tolist()}, "
          f"ARI {prot['stability']['ari_mean']}+/-{prot['stability']['ari_sd']}, Jaccard {prot['stability']['cluster_jaccard']}")

    comparison = {"status": "pending: GNN patient embeddings not provided (rerun with --gnn-emb)"}
    if args.gnn_emb:
        g = read(Path(args.gnn_emb).with_suffix("")) if not Path(args.gnn_emb).exists() else (
            pd.read_parquet(args.gnn_emb) if args.gnn_emb.endswith(".parquet") else pd.read_csv(args.gnn_emb))
        g["patient_id"] = g["patient_id"].astype(str)
        g = g.set_index("patient_id").reindex(ids).select_dtypes("number")
        shared = g.dropna().index
        def gfeat(D):
            Z = StandardScaler().fit_transform(D)
            return PCA(n_components=min(N_PCS, Z.shape[0] - 1, Z.shape[1]), random_state=SEED).fit_transform(Z)
        gnn = cluster_block("gnn_embedding_pca_kmeans", g.loc[shared], gfeat, list(shared))
        gl_matched = kmeans(gnn["E"], k)
        pos = [ids.index(s) for s in shared]
        comparison = {
            "status": "computed", "n_patients": int(len(shared)), "gnn_source": str(args.gnn_emb),
            "gnn_k_by_silhouette": gnn["k"], "gnn_k_selection": gnn["k_selection"], "gnn_stability": gnn["stability"],
            "ari_proteomics_vs_gnn_k_matched": round(float(adjusted_rand_score(labels[pos], gl_matched)), 3),
            "ari_proteomics_vs_gnn_own_k": round(float(adjusted_rand_score(labels[pos], gnn["labels"])), 3),
            "proteomics_stability_ari": prot["stability"]["ari_mean"],
            "note": "Higher stability = more reproducible subgroups. ARI near 0 = the two representations group patients differently.",
        }
        print(f"GNN comparison: ARI(proteomics, GNN) = {comparison['ari_proteomics_vs_gnn_k_matched']} (k matched)")

    result = {
        "cancer": args.cancer, "patient_id": None, "data_origin": "real", "execution": "cached",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "method": {"representation": "proteomics", "pipeline": f"filter missing>{MAX_MISSING:.0%}, median impute, "
                   f"top-{TOP_VAR} variance, standardize, PCA({N_PCS}), k-means(n_init=50)", "seed": SEED,
                   "labels_used_for_clustering": False,
                   "coordinates": f"first two PCs of the clustering space ({pca2.explained_variance_ratio_.sum():.0%} of its variance)"},
        "k": int(k), "k_selection": prot["k_selection"], "stability": prot["stability"],
        "clusters": [{"cluster": c, "size": int((labels == c).sum()), "jaccard_stability": prot["stability"]["cluster_jaccard"][c],
                      **sigs[c], "descriptive_after_clustering": desc.get(c, {})} for c in range(k)],
        "descriptive_tests_after_clustering": desc_tests,
        "assignments": [{"patient_id": pid, "cluster": int(labels[i]), "x": round(float(xy[i, 0]), 3),
                         "y": round(float(xy[i, 1]), 3),
                         "co_assignment_rate": None if prot["per_patient"][i] is None else round(prot["per_patient"][i], 3)}
                        for i, pid in enumerate(ids)],
        "comparison_proteomics_vs_gnn": comparison,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
