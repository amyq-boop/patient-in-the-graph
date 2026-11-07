#!/usr/bin/env python3
"""
WS2 (steps 1-2): CPTAC ccRCC proteomics -> signed patient -> protein edges.

Outputs in data/processed/ (parquet if pyarrow is installed, otherwise CSV):
  ccrcc_prot_raw          raw umich proteomics, columns "SYMBOL|DBID" (local cache, never re-download)
  ccrcc_prot_id_map       gene symbol <-> database ID, for WS1 ID normalization
  ccrcc_prot_tumor        tumor x protein matrix after filtering (WS3 proteomics-only baseline)
  ccrcc_prot_z            tumor x protein z-scores
  patient_protein_edges   top-K up / top-K down signed edges per tumor (the graph edges)
  ws2_proteomics_qc.json  counts and sanity checks to paste into Slack

Usage:
  python ws2_proteomics.py                          # download through the cptac package
  python ws2_proteomics.py --from-file prot.tsv     # fallback: your own matrix (e.g. from PDC)
  python ws2_proteomics.py --from-file prot.tsv --transpose   # if the file is genes x samples
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("data/processed")
SEP = "|"  # separates gene symbol and database ID in flattened column names
MARKERS = ["CA9", "NDUFA4L2", "VEGFA", "EPAS1", "VHL"]  # CA9 should be strongly up in most ccRCC tumors


# ---------- I/O helpers ----------
def _has_parquet():
    try:
        import pyarrow  # noqa: F401
        return True
    except ImportError:
        return False


def save(df, name, index=True):
    OUT.mkdir(parents=True, exist_ok=True)
    if _has_parquet():
        path = OUT / f"{name}.parquet"
        df.to_parquet(path, index=index)
    else:
        path = OUT / f"{name}.csv"
        df.to_csv(path, index=index)
    print(f"  saved {path}  shape={df.shape}")
    return path


def load_cached(name):
    for ext, reader in ((".parquet", pd.read_parquet), (".csv", lambda p: pd.read_csv(p, index_col=0))):
        path = OUT / f"{name}{ext}"
        if path.exists():
            print(f"Using cached {path} (pass --refresh to re-download)")
            return reader(path)
    return None


# ---------- step 1: get the data ----------
def load_from_cptac():
    import cptac
    print("cptac version:", getattr(cptac, "__version__", "unknown"))
    cc = cptac.Ccrcc()
    print("Data sources available for ccRCC:")
    print(cc.list_data_sources())
    return cc.get_proteomics("umich")


def load_from_file(path, transpose):
    sep = "\t" if str(path).endswith((".tsv", ".txt")) else ","
    df = pd.read_csv(path, sep=sep, index_col=0)
    return df.T if transpose else df


def flatten_columns(df):
    """cptac returns MultiIndex columns (Name, Database_ID). Flatten to 'SYMBOL|DBID' and build an ID map."""
    if isinstance(df.columns, pd.MultiIndex):
        symbols = df.columns.get_level_values(0).astype(str)
        dbids = (df.columns.get_level_values(1).astype(str)
                 if df.columns.nlevels > 1 else pd.Index([""] * df.shape[1]))
    else:
        symbols = df.columns.astype(str)
        dbids = pd.Index([""] * df.shape[1])
    flat = df.copy()
    flat.columns = [f"{s}{SEP}{d}" for s, d in zip(symbols, dbids)]
    flat.index = flat.index.astype(str)
    flat.index.name = "sample_id"
    id_map = pd.DataFrame({"column": flat.columns, "symbol": symbols, "database_id": dbids})
    return flat, id_map


# ---------- step 2: tumor/normal split, filtering, z-scores, edges ----------
def collapse_duplicate_symbols(df):
    """Several columns can share a gene symbol (isoforms/IDs). Keep the best-measured one per symbol."""
    counts = df.notna().sum().sort_values(ascending=False)
    ordered = df[counts.index]
    symbols = ordered.columns.str.split(SEP, regex=False).str[0]
    keep = ~pd.Index(symbols).duplicated()
    out = ordered.loc[:, keep]
    out.columns = symbols[keep]
    return out, int((~keep).sum())


def split_samples(df, normal_suffix):
    idx = df.index.to_series()
    is_normal = idx.str.endswith(normal_suffix)
    is_tumor = ~idx.str.contains(".", regex=False)
    other = idx[~is_normal & ~is_tumor].tolist()
    tumors, normals = df[is_tumor.values], df[is_normal.values]
    normals.index = normals.index.str[: -len(normal_suffix)]  # normal ID -> patient ID
    return tumors, normals, other


def zscore(tumors, normals, mode, min_normals):
    """
    normal_ref: z = (tumor - mean of normal tissue) / sd of normal tissue, per protein.
                Proteins with fewer than min_normals normal observations fall back to cohort stats.
    cohort:     z against the tumor cohort itself (use if normals are missing).
    Neither mode uses labels, so this does not leak stage/grade/survival.
    """
    mu_t, sd_t = tumors.mean(), tumors.std(ddof=1)
    if mode == "cohort" or normals.empty:
        mu, sd, fallback = mu_t, sd_t, pd.Series(True, index=tumors.columns)
    else:
        n_obs = normals.notna().sum()
        mu_n, sd_n = normals.mean(), normals.std(ddof=1)
        fallback = (n_obs < min_normals) | (sd_n <= 0) | sd_n.isna()
        mu = mu_n.where(~fallback, mu_t)
        sd = sd_n.where(~fallback, sd_t)
    sd = sd.replace(0, np.nan)
    return (tumors - mu) / sd, int(fallback.sum())


def build_edges(z, k):
    rows = []
    for pid, vals in z.iterrows():
        vals = vals.dropna()
        up = vals[vals > 0].nlargest(k)
        down = vals[vals < 0].nsmallest(k)
        for direction, sel in (("up", up), ("down", down)):
            for rank, (gene, val) in enumerate(sel.items(), start=1):
                rows.append({"patient_id": pid, "gene_symbol": gene, "z": float(val),
                             "sign": 1 if direction == "up" else -1, "direction": direction, "rank": rank})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-file", help="fallback proteomics matrix (samples x genes unless --transpose)")
    ap.add_argument("--transpose", action="store_true", help="input file is genes x samples")
    ap.add_argument("--refresh", action="store_true", help="ignore the local cache and re-download")
    ap.add_argument("--normal-suffix", default=".N")
    ap.add_argument("--mode", choices=["normal_ref", "cohort"], default="normal_ref")
    ap.add_argument("--min-tumor-frac", type=float, default=0.5, help="keep proteins seen in >= this share of tumors")
    ap.add_argument("--min-normals", type=int, default=10)
    ap.add_argument("--top-k", type=int, default=50)
    args = ap.parse_args()

    # Step 1: load (cache first, so a flaky Zenodo download only has to succeed once)
    raw = None if args.refresh else load_cached("ccrcc_prot_raw")
    if raw is None:
        src = load_from_file(args.from_file, args.transpose) if args.from_file else load_from_cptac()
        raw, id_map = flatten_columns(src)
        save(raw, "ccrcc_prot_raw")
        save(id_map, "ccrcc_prot_id_map", index=False)
    raw.index = raw.index.astype(str)
    print(f"Raw matrix: {raw.shape[0]} samples x {raw.shape[1]} protein columns")

    # Step 2
    prot, n_dupes = collapse_duplicate_symbols(raw)
    tumors, normals, other = split_samples(prot, args.normal_suffix)
    if other:
        print(f"WARNING: {len(other)} samples are neither tumor nor '{args.normal_suffix}' normal and were dropped: {other[:5]}")
    keep = tumors.notna().mean() >= args.min_tumor_frac
    tumors, normals = tumors.loc[:, keep], normals.loc[:, normals.columns.intersection(tumors.columns[keep])]
    z, n_fallback = zscore(tumors, normals, args.mode, args.min_normals)
    edges = build_edges(z, args.top_k)

    save(tumors, "ccrcc_prot_tumor")
    save(z, "ccrcc_prot_z")
    save(edges, "patient_protein_edges", index=False)

    per_patient = edges.groupby("patient_id").size()
    qc = {
        "mode": args.mode,
        "n_tumors": int(tumors.shape[0]),
        "n_normals": int(normals.shape[0]),
        "n_patients_with_matched_normal": int(len(set(tumors.index) & set(normals.index))),
        "n_protein_columns_raw": int(raw.shape[1]),
        "n_duplicate_symbol_columns_dropped": n_dupes,
        "n_proteins_kept": int(tumors.shape[1]),
        "n_proteins_using_cohort_fallback": n_fallback,
        "edges_per_patient_min": int(per_patient.min()) if len(per_patient) else 0,
        "edges_per_patient_max": int(per_patient.max()) if len(per_patient) else 0,
        "patients_with_fewer_than_2k_edges": int((per_patient < 2 * args.top_k).sum()),
        "markers": {g: {"median_z": round(float(z[g].median()), 2),
                        "share_tumors_z_gt_2": round(float((z[g] > 2).mean()), 2),
                        "share_tumors_in_top_up_edges": round(float(
                            edges.query("gene_symbol == @g and direction == 'up'")["patient_id"].nunique()
                            / max(len(z), 1)), 2)}
                    for g in MARKERS if g in z.columns},
        "markers_missing": [g for g in MARKERS if g not in z.columns],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "ws2_proteomics_qc.json").write_text(json.dumps(qc, indent=2))
    print(json.dumps(qc, indent=2))
    if qc["edges_per_patient_min"] != 2 * args.top_k:
        print("NOTE: some patients have fewer than", 2 * args.top_k, "edges (too few up or down proteins).")


if __name__ == "__main__":
    main()