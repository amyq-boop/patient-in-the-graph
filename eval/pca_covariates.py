#!/usr/bin/env python3
"""
What does each principal component of the tumour proteomics represent?

  python -m eval.pca_covariates --cancer ccrcc
  python -m eval.pca_covariates --cancer brca

For each cancer and each scaling (scaled = proteins standardized; unscaled = centred only):
  - PCA (10 PCs) on the tumour protein matrix (drop proteins missing in >20% of tumours, median impute,
    top-1000 most variable proteins). Same feature recipe as models/stratify.py.
  - Association of every PC with every covariate, on one scale: R^2
      numeric covariates  -> Spearman rho^2 (p from Spearman)
      categorical         -> one-way ANOVA R^2 = between-group / total variance (p from ANOVA)
    Benjamini-Hochberg q across the whole grid; * q<0.05, ** q<0.01, *** q<0.001.
  - Covariates: ESTIMATE stromal/immune/purity, interferon/antigen-presentation protein score, driver mutations,
    age, sex, technical factors (TMT plex if available, tissue source site / ID prefix, country),
    stage/grade (DESCRIPTIVE ONLY). BRCA adds ESR1 RNA and ERBB2 copy number (positive-control axes).
Outputs in eval/results/pca/: pca_covariates_<cancer>_<scaling>.png/.csv and pca_scatter_<cancer>_<scaling>.png
None of the covariates are used to build the PCs; they are only compared afterwards.
"""
import argparse
import glob
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler

PROC = Path("data/processed")
OUT = Path("eval/results/pca")
N_PCS, TOP_VAR, MAX_MISSING = 10, 1000, 0.2
IFN_GENES = ["HLA-A", "HLA-B", "HLA-C", "B2M", "TAP1", "TAP2", "PSMB8", "PSMB9", "STAT1", "IRF1",
             "GBP1", "ISG15", "IFIT1", "IFIT3", "MX1", "CXCL10"]
COHORTS = {
    "ccrcc": {"prot": [PROC / "patients/proteomics_tumor", PROC / "ccrcc_prot_tumor"], "patients": PROC / "patients",
              "dataset": "Ccrcc"},
    "brca": {"prot": [PROC / "brca/ccrcc_prot_tumor"], "patients": PROC / "brca/patients", "dataset": "Brca"},
}


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
            m = m.set_index("sample_id") if "sample_id" in m.columns else m
            m.index = m.index.astype(str)
            return m.apply(pd.to_numeric, errors="coerce")
    raise SystemExit(f"no tumour proteomics matrix in {cfg['prot']}")


def pca(X, scaled):
    X = X.loc[:, X.notna().mean() >= 1 - MAX_MISSING]
    X = X.fillna(X.median())
    X = X[X.var().sort_values(ascending=False).index[:TOP_VAR]]
    Z = StandardScaler(with_std=scaled).fit_transform(X)
    p = PCA(n_components=N_PCS, random_state=0).fit(Z)
    scores = pd.DataFrame(p.transform(Z), index=X.index, columns=[f"PC{i + 1}" for i in range(N_PCS)])
    load = pd.DataFrame(p.components_.T, index=X.columns, columns=scores.columns)
    return scores, p.explained_variance_ratio_, load


# ---------------- covariates ----------------
def gene_cols(df, gene):
    return df.loc[:, df.columns.get_level_values(0) == gene] if isinstance(df.columns, pd.MultiIndex) else df[[gene]]


def build_covariates(cancer, cfg, X, ids):
    cov = pd.DataFrame(index=ids)
    kinds = {}

    def add(name, series, kind):
        cov[name] = pd.Series(series).reindex(ids)
        kinds[name] = kind

    # interferon / antigen presentation protein score (mean standardized protein level)
    g = [x for x in IFN_GENES if x in X.columns]
    if g:
        Zg = (X[g] - X[g].mean()) / X[g].std()
        add("IFN/antigen-presentation score", Zg.mean(axis=1), "num")

    pts, labels = read(cfg["patients"] / "patients"), read(cfg["patients"] / "labels")
    if pts is not None:
        pts = pts.set_index(pts["patient_id"].astype(str))
        add("age", pd.to_numeric(pts["age"], errors="coerce"), "num")
        if pts["sex"].nunique() > 1:
            add("sex", pts["sex"], "cat")
    if labels is not None:
        labels = labels.set_index(labels["patient_id"].astype(str))
        for c, name in [("y_stage", "stage III-IV (descriptive)"), ("y_grade", "high grade (descriptive)")]:
            if c in labels and labels[c].notna().sum() > 10 and labels[c].nunique() > 1:
                add(name, labels[c].astype("float").map({0.0: "low", 1.0: "high"}), "cat")

    # technical: ID prefix / tissue source site
    if cancer == "ccrcc":
        add("ID prefix (C3L/C3N)", pd.Series([i[:3] for i in ids], index=ids), "cat")
    else:
        add("tissue source site", pd.Series([re.match(r"(\d+)BR", i).group(1) if re.match(r"\d+BR", i) else "other"
                                             for i in ids], index=ids), "cat")

    try:
        import cptac
        ds = getattr(cptac, cfg["dataset"])()
    except Exception as e:
        print(f"NOTE: cptac unavailable ({e}); ESTIMATE, mutation and plex covariates skipped")
        return cov, kinds

    try:
        est = ds.get_dataframe("tumor_purity", "washu").select_dtypes("number")
        est.index = est.index.astype(str)
        for c in ["StromalScore", "ImmuneScore", "TumorPurity"]:
            if c in est:
                add(f"ESTIMATE {c}", est[c], "num")
    except Exception as e:
        print(f"NOTE: ESTIMATE unavailable ({e})")

    try:
        maf = ds.get_somatic_mutation("harmonized")
        maf.index = maf.index.astype(str)
        coding = maf[~maf["Mutation"].isin(["Silent", "RNA", "Intron", "3'UTR", "5'UTR", "IGR", "3'Flank", "5'Flank"])]
        mutated = lambda gene: pd.Series([i in set(coding.index[coding["Gene"] == gene]) for i in ids], index=ids)
        if cancer == "ccrcc":
            grp = pd.Series("none of these", index=ids)
            for gene, lab in [("VHL", "VHL only"), ("SETD2", "SETD2"), ("PBRM1", "PBRM1"), ("BAP1", "BAP1")]:
                grp[mutated(gene)] = lab  # later genes overwrite: priority BAP1 > PBRM1 > SETD2 > VHL only
            add("driver mutation group", grp, "cat")
        else:
            for gene in ["TP53", "PIK3CA", "GATA3", "CDH1"]:
                add(f"{gene} mutated", mutated(gene).map({True: "yes", False: "no"}), "cat")
    except Exception as e:
        print(f"NOTE: mutations unavailable ({e})")

    if cancer == "ccrcc":
        try:
            cl = ds.get_dataframe("clinical", "mssm")
            cl.index = cl.index.astype(str)
            if "participant_country" in cl:
                add("participant country", cl["participant_country"], "cat")
        except Exception as e:
            print(f"NOTE: clinical country unavailable ({e})")
    else:
        try:  # BRCA positive-control axes from RNA / DNA (never used in the PCA)
            rna = ds.get_dataframe("transcriptomics", "broad")
            esr1 = gene_cols(rna, "ESR1").sum(axis=1)  # transcript-level -> gene-level TPM
            esr1.index = esr1.index.astype(str)
            add("ESR1 RNA (log1p TPM)", np.log1p(esr1), "num")
        except Exception as e:
            print(f"NOTE: ESR1 RNA unavailable ({e})")
        try:
            cnv = ds.get_dataframe("CNV", "washu")
            erbb2 = gene_cols(cnv, "ERBB2").iloc[:, 0]
            erbb2.index = erbb2.index.astype(str)
            add("ERBB2 copy number", erbb2, "num")
        except Exception as e:
            print(f"NOTE: ERBB2 CNV unavailable ({e})")

    # technical: TMT plex, if the umich sample sheet is in the cptac cache
    try:
        root = os.path.dirname(cptac.__file__)
        sheets = glob.glob(os.path.join(root, "data", f"umich-{cancer}", "*sample*"))
        for s in sheets:
            d = pd.read_csv(s, sep="\t")
            if {"Participant", "Experiment"} <= set(d.columns):
                d = d[d.get("Type", "Tumor").astype(str).str.lower().str.startswith("tumor")] if "Type" in d else d
                d["pid"] = d["Participant"].astype(str).str.replace(r"^X", "", regex=True)
                add("TMT plex", d.drop_duplicates("pid").set_index("pid")["Experiment"].astype(str), "cat")
                break
        if "TMT plex" not in cov:
            print("NOTE: no TMT plex sheet found in the cptac cache; batch covariate skipped")
    except Exception as e:
        print(f"NOTE: TMT plex unavailable ({e})")
    return cov, kinds


# ---------------- association grid ----------------
def bh(p):
    p = np.asarray(p, float); q = np.full(len(p), np.nan); ok = ~np.isnan(p)
    if ok.sum():
        o = np.argsort(p[ok]); r = p[ok][o] * ok.sum() / np.arange(1, ok.sum() + 1)
        tmp = np.empty(ok.sum()); tmp[o] = np.minimum(np.minimum.accumulate(r[::-1])[::-1], 1); q[ok] = tmp
    return q


def associate(scores, cov, kinds):
    rows = []
    for c in cov.columns:
        for pc in scores.columns:
            d = pd.DataFrame({"pc": scores[pc], "x": cov[c]}).dropna()
            r2 = p = np.nan
            if kinds[c] == "num" and len(d) > 10 and d["x"].nunique() > 2:
                rho, p = stats.spearmanr(d["pc"], d["x"]); r2 = rho ** 2
            elif kinds[c] == "cat":
                groups = [g["pc"].to_numpy() for _, g in d.groupby("x") if len(g) >= 3]
                if len(groups) >= 2:
                    p = stats.f_oneway(*groups).pvalue
                    allv = np.concatenate(groups); sst = ((allv - allv.mean()) ** 2).sum()
                    r2 = sum(len(g) * (g.mean() - allv.mean()) ** 2 for g in groups) / sst if sst else np.nan
            rows.append({"covariate": c, "pc": pc, "r2": r2, "p": p, "n": len(d)})
    res = pd.DataFrame(rows)
    res["q"] = bh(res["p"])
    return res


def plot_heatmap(res, var, cancer, scaling, path):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    R = res.pivot(index="covariate", columns="pc", values="r2").reindex(columns=[f"PC{i + 1}" for i in range(N_PCS)])
    Q = res.pivot(index="covariate", columns="pc", values="q").reindex(columns=R.columns)
    fig, ax = plt.subplots(figsize=(1.0 + 0.75 * N_PCS, 0.9 + 0.42 * len(R)))
    im = ax.imshow(R.to_numpy(float), cmap="Blues", vmin=0, vmax=max(0.3, np.nanmax(R.to_numpy(float))), aspect="auto")
    ax.set_xticks(range(N_PCS), [f"{c}\n{v:.0%}" for c, v in zip(R.columns, var)], fontsize=8)
    ax.set_yticks(range(len(R)), R.index, fontsize=8)
    for i in range(R.shape[0]):
        for j in range(R.shape[1]):
            q = Q.iat[i, j]
            star = "***" if q < 0.001 else "**" if q < 0.01 else "*" if q < 0.05 else ""
            if star:
                ax.text(j, i, star, ha="center", va="center", fontsize=8,
                        color="white" if R.iat[i, j] > 0.25 else "black")
    ax.set_title(f"{cancer.upper()} proteomics PCA ({scaling}): variance in each PC explained by each covariate (R²)\n"
                 "* q<0.05  ** q<0.01  *** q<0.001 (BH across grid); covariates were not used to build the PCs", fontsize=9)
    fig.colorbar(im, ax=ax, fraction=0.03, label="R²")
    fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)


def plot_scatter(scores, cov, kinds, var, cancer, scaling, path):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    wanted = (["ESTIMATE StromalScore", "ESTIMATE TumorPurity", "driver mutation group"] if cancer == "ccrcc"
              else ["ESR1 RNA (log1p TPM)", "TP53 mutated", "ERBB2 copy number"])
    cols = [c for c in wanted if c in cov]
    if not cols:
        return
    fig, axes = plt.subplots(1, len(cols), figsize=(5 * len(cols), 4.3))
    axes = np.atleast_1d(axes)
    for ax, c in zip(axes, cols):
        x, y, v = scores["PC1"], scores["PC2"], cov[c]
        if kinds[c] == "num":
            sc = ax.scatter(x, y, c=v, cmap="viridis", s=22, edgecolor="white", linewidth=0.4)
            fig.colorbar(sc, ax=ax, fraction=0.046)
            d = pd.DataFrame({"a": x, "b": v}).dropna()
            ttl = f"PC1 vs {c}: Spearman {stats.spearmanr(d['a'], d['b'])[0]:.2f}"
        else:
            for lev, g in v.groupby(v):
                ax.scatter(x[g.index], y[g.index], s=22, label=f"{lev} (n={len(g)})", edgecolor="white", linewidth=0.4)
            ax.legend(fontsize=7, frameon=False)
            d = pd.DataFrame({"a": x, "b": v}).dropna()
            grp = [g["a"] for _, g in d.groupby("b") if len(g) >= 3]
            ttl = f"PC1 vs {c}: Kruskal p={stats.kruskal(*grp).pvalue:.2g}" if len(grp) >= 2 else c
        ax.set_title(ttl, fontsize=9)
        ax.set_xlabel(f"PC1 ({var[0]:.0%})"); ax.set_ylabel(f"PC2 ({var[1]:.0%})")
    fig.suptitle(f"{cancer.upper()} proteomics PCA ({scaling}); colours added afterwards", fontsize=10)
    fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cancer", required=True, choices=sorted(COHORTS))
    ap.add_argument("--scaling", default="both", choices=["both", "scaled", "unscaled"])
    args = ap.parse_args()
    cfg = COHORTS[args.cancer]
    OUT.mkdir(parents=True, exist_ok=True)
    X = load_matrix(cfg)
    ids = list(X.index)
    cov, kinds = build_covariates(args.cancer, cfg, X, ids)
    print(f"{args.cancer}: {len(ids)} tumours, covariates: {list(cov.columns)}")
    for scaling in (["scaled", "unscaled"] if args.scaling == "both" else [args.scaling]):
        scores, var, load = pca(X, scaled=scaling == "scaled")
        res = associate(scores, cov, kinds)
        stem = f"{args.cancer}_{scaling}"
        res.to_csv(OUT / f"pca_covariates_{stem}.csv", index=False)
        load.to_csv(OUT / f"pca_loadings_{stem}.csv")
        plot_heatmap(res, var, args.cancer, scaling, OUT / f"pca_covariates_{stem}.png")
        plot_scatter(scores, cov, kinds, var, args.cancer, scaling, OUT / f"pca_scatter_{stem}.png")
        print(f"\n== {stem}: variance explained PC1-PC5 {np.round(var[:5] * 100, 1).tolist()} %")
        for pc in [f"PC{i}" for i in (1, 2, 3)]:
            top = res[res.pc == pc].sort_values("r2", ascending=False).head(3)
            hits = "; ".join(f"{r.covariate} R²={r.r2:.2f} (q={r.q:.1g})" for r in top.itertuples() if pd.notna(r.r2))
            print(f"   {pc}: {hits}")
            print(f"        top proteins: +{list(load[pc].nlargest(6).index)}  -{list(load[pc].nsmallest(6).index)}")
        print(f"   wrote {OUT}/pca_covariates_{stem}.png, pca_scatter_{stem}.png, .csv")


if __name__ == "__main__":
    main()
