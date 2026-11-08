#!/usr/bin/env python3
"""
WS2 -> data contract section 5. Run from the repo root AFTER ws2_proteomics.py, ws2_clinical.py
and ws2_extra_edges.py, and after WS1 has built data/processed/nodes.parquet.

Writes data/processed/patients/:
  patients.parquet            patient_id, curie (PATIENT:<id>), age, sex, clinical_summary, other non-label fields
  patient_phenotypes.parquet  patient_id, curie, source_field, source_text
  patient_proteins.parquet    patient_id, curie (NCBIGene), symbol, z, direction, rank, z_ref
  labels.parquet              patient_id, y_stage, y_grade, y_recur, os_days, os_event (+ rfs_days)
  folds.parquet               patient_id, fold (0-4), stratified on y_stage
Proposed (NOT in the contract yet; post in Slack before anyone consumes them):
  patient_mutations.parquet, patient_drugs.parquet, patient_exposures.parquet, patient_history.parquet
    all with: patient_id, curie, name, source_field, source_text, weight

Usage:
  python graph/patients/export_patients.py
  python -m common.validate all
"""
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

PROC = Path("data/processed")
OUT = PROC / "patients"
TOP_K = 50
FOLD_SEED = 0  # matches e1_split.json's seed

try:  # shared list of tumor-describing terms (renal mass, lytic lesion of bone, ...)
    from common.contracts import LEAKY_PHENOTYPE_TERMS
except Exception:
    LEAKY_PHENOTYPE_TERMS = ["renal mass", "lytic lesion", "pulmonary nodule"]
    print("NOTE: common.contracts not importable; using a fallback LEAKY_PHENOTYPE_TERMS list")


# ---------- I/O ----------
def read(name, base=PROC):
    for ext, fn in ((".parquet", pd.read_parquet), (".csv", pd.read_csv)):
        if (base / f"{name}{ext}").exists():
            return fn(base / f"{name}{ext}")
    raise FileNotFoundError(f"{base / name}.parquet not found. Run the WS2 scripts (or WS1 for nodes) first.")


def write(df, name):
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        df.to_parquet(OUT / f"{name}.parquet", index=False)
        print(f"  wrote {OUT / name}.parquet  {df.shape}")
    except ImportError:
        df.to_csv(OUT / f"{name}.csv", index=False)
        print(f"  wrote {OUT / name}.csv  {df.shape}  (install pyarrow: the contract wants parquet)")


# ---------- node lookups ----------
class Nodes:
    def __init__(self, nodes):
        self.df = nodes
        self.canon = {}  # any CURIE or alias -> canonical curie
        for cur, aliases in zip(nodes["curie"], nodes["aliases"].fillna("")):
            self.canon[cur] = cur
            for a in str(aliases).split("|"):
                if a:
                    self.canon.setdefault(a, cur)
        self.name_of = dict(zip(nodes["curie"], nodes["name"]))
        self.by_name = {}  # (type, lowercase name or alt name) -> curie
        for cur, typ, name, alts in zip(nodes["curie"], nodes["type"], nodes["name"], nodes["alt_names"].fillna("")):
            for n in [name] + str(alts).split("|"):
                if isinstance(n, str) and n:
                    self.by_name.setdefault((typ, n.lower().strip()), cur)

    def resolve(self, curie):
        return self.canon.get(curie)

    def gene_for_symbol(self, symbol):
        cur = self.by_name.get(("gene", str(symbol).lower()))
        return cur if cur and cur.startswith("NCBIGene:") else None

    def by_type_name(self, typ, name):
        return self.by_name.get((typ, str(name).lower().strip()))


# ---------- contract tables ----------
def proteins(nodes):
    z = read("ccrcc_prot_z")
    if "sample_id" in z.columns:
        z = z.set_index("sample_id")
    elif z.columns[0].lower() in ("unnamed: 0", "patient_id", "index"):
        z = z.set_index(z.columns[0])
    qc = json.loads((PROC / "ws2_proteomics_qc.json").read_text()) if (PROC / "ws2_proteomics_qc.json").exists() else {}
    z_ref = "cohort" if qc.get("mode") == "cohort" else "normal"
    gene = {s: nodes.gene_for_symbol(s) for s in z.columns}
    keep = [s for s, g in gene.items() if g]
    print(f"Proteins: {len(keep)} of {z.shape[1]} map to NCBIGene nodes in nodes.parquet")
    z = z[keep]
    rows = []
    for pid, vals in z.iterrows():  # top-K is chosen among graph genes, so every patient keeps 2*K edges
        vals = vals.dropna()
        for direction, sel in (("up", vals[vals > 0].nlargest(TOP_K)), ("down", vals[vals < 0].nsmallest(TOP_K))):
            for rank, (sym, val) in enumerate(sel.items(), start=1):
                rows.append({"patient_id": str(pid), "curie": gene[sym], "symbol": sym, "z": float(val),
                             "direction": direction, "rank": rank, "z_ref": z_ref})
    return pd.DataFrame(rows)


def phenotypes(nodes):
    ph = read("patient_phenotype_edges")
    if ph.empty:  # e.g. CPTAC-BRCA records no comorbidities
        print("Phenotypes: 0 edges (no medical_condition data)")
        return pd.DataFrame(columns=["patient_id", "curie", "source_field", "source_text"])
    if "source_text" not in ph.columns:
        print("WARNING: rerun ws2_clinical.py (new version) so source_text holds the full raw term")
        ph["source_text"] = ph.get("matched_text", "")
    ph["curie"] = ph["curie"].map(nodes.resolve)
    missing = ph["curie"].isna().sum()
    leaky = ph["source_text"].str.lower().apply(lambda t: any(term.lower() in t for term in LEAKY_PHENOTYPE_TERMS))
    print(f"Phenotypes: {len(ph)} edges, {missing} not in nodes.parquet (dropped), {int(leaky.sum())} leaky (dropped)")
    ph = ph[ph["curie"].notna() & ~leaky]
    return ph[["patient_id", "curie", "source_field", "source_text"]].drop_duplicates(["patient_id", "curie"])


def labels():
    lab = read("labels")
    out = pd.DataFrame({
        "patient_id": lab["patient_id"].astype(str),
        "y_stage": lab["stage_high"].astype("Int64"),
        "y_grade": lab["grade_high"].astype("Int64"),
        "y_recur": lab["rfs_event"].astype("Int64"),
        "os_days": lab["os_days"],
        "os_event": lab["os_event"].astype("Int64"),
        "rfs_days": lab["rfs_days"],
    })
    return out


def folds(lab, k=5):
    y = lab["y_stage"].fillna(-1).astype(int).to_numpy()
    try:
        from sklearn.model_selection import StratifiedKFold
        fold = np.empty(len(y), dtype=int)
        for i, (_, te) in enumerate(StratifiedKFold(k, shuffle=True, random_state=FOLD_SEED).split(np.zeros(len(y)), y)):
            fold[te] = i
    except ImportError:
        rng, fold = np.random.default_rng(FOLD_SEED), np.empty(len(y), dtype=int)
        for c in np.unique(y):
            idx = rng.permutation(np.where(y == c)[0])
            fold[idx] = np.arange(len(idx)) % k
    return pd.DataFrame({"patient_id": lab["patient_id"], "fold": fold})


def patients_table(ph, drugs, nodes):
    f = read("clinical_features")
    f["patient_id"] = f["patient_id"].astype(str)
    cond = ph.assign(name=ph["curie"].map(nodes.name_of)).groupby("patient_id")["name"].apply(
        lambda s: sorted(set(map(str, s))))
    meds = drugs.groupby("patient_id")["name"].apply(lambda s: sorted(set(map(str, s)))) if len(drugs) else pd.Series(dtype=object)

    def smoking(v):
        return "smoking history unknown" if pd.isna(v) else ("ever smoker" if v == 1 else "never smoker")

    def summary(r):
        parts = []
        age = "" if pd.isna(r.age) else f"{int(r.age)}-year-old "
        sex = {1.0: "male", 0.0: "female"}.get(r.sex_male, "patient")
        parts.append(f"{age}{sex}".strip())
        if pd.notna(r.bmi):
            parts.append(f"BMI {r.bmi:.1f}")
        parts.append(smoking(r.ever_smoker))
        c = cond.get(r.patient_id, [])
        parts.append("comorbidities: " + (", ".join(c) if c else "none recorded"))
        m = meds.get(r.patient_id, [])
        if m:
            parts.append("medications: " + ", ".join(m[:8]) + (" and others" if len(m) > 8 else ""))
        return "; ".join(parts) + "."  # no stage, grade, tumor or outcome information by construction

    out = pd.DataFrame({
        "patient_id": f["patient_id"],
        "curie": "PATIENT:" + f["patient_id"],
        "age": f["age"],
        "sex": f["sex_male"].map({1.0: "male", 0.0: "female"}),
        "clinical_summary": f.apply(summary, axis=1),
        "bmi": f["bmi"], "ever_smoker": f["ever_smoker"], "pack_years": f["pack_years"], "ecog": f["ecog"],
    })
    return out


# ---------- proposed extra neighbour tables ----------
def extra(name, nodes, typ, source_field, curie_col=None, name_col="primekg_name", text_col="raw"):
    try:
        e = read(name)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame()
    if e.empty or "patient_id" not in e.columns:
        print(f"{name}: 0 edges")
        return pd.DataFrame()
    if curie_col:
        cur = e[curie_col].map(nodes.resolve)
    else:
        cur = pd.Series([None] * len(e), index=e.index)
    cur = cur.fillna(e[name_col].map(lambda n: nodes.by_type_name(typ, n)) if name_col in e else cur)
    out = pd.DataFrame({"patient_id": e["patient_id"].astype(str), "curie": cur,
                        "source_field": source_field, "source_text": e[text_col] if text_col in e else "",
                        "weight": 1.0})
    print(f"{name}: {len(out)} edges, {int(out['curie'].isna().sum())} not in nodes.parquet (dropped)")
    out = out[out["curie"].notna()].drop_duplicates(["patient_id", "curie"])
    out.insert(2, "name", out["curie"].map(nodes.name_of))
    return out


def main():
    nodes = Nodes(read("nodes"))
    prot = proteins(nodes)
    ph = phenotypes(nodes)
    lab = labels()
    fo = folds(lab)
    mut = extra("patient_mutation_edges", nodes, "gene", "somatic_mutation", curie_col="curie",
                name_col="gene_symbol", text_col="protein_changes")
    drugs = extra("patient_drug_edges", nodes, "drug", "medication_name_vitamins_supplements", curie_col="curie")
    expo = extra("patient_exposure_edges", nodes, "exposure", "tobacco_or_alcohol")
    hist = extra("patient_history_edges", nodes, "disease", "cancer_type")
    pts = patients_table(ph, drugs, nodes)

    for df, n in [(pts, "patients"), (ph, "patient_phenotypes"), (prot, "patient_proteins"), (lab, "labels"),
                  (fo, "folds")]:
        write(df, n)  # contract tables are always written, even when empty
    for df, n in [(mut, "patient_mutations"), (drugs, "patient_drugs"), (expo, "patient_exposures"),
                  (hist, "patient_history")]:
        if len(df):
            write(df, n)

    per = prot.groupby("patient_id").size()
    print("\nSummary")
    print(f"  patients: {len(pts)}   protein edges/patient: {per.min()}-{per.max()}   "
          f"phenotype edges: {len(ph)} across {ph['patient_id'].nunique()} patients")
    print(f"  y_stage: {lab['y_stage'].value_counts().to_dict()}   folds: {fo['fold'].value_counts().sort_index().to_dict()}")
    print(f"  GNN-A weights: phenotypes 1.0 vs median |z| of protein edges {prot['z'].abs().median():.1f}")
    print("  example summary:", pts["clinical_summary"].iloc[0])
    print("\nNext: python -m common.validate all")


if __name__ == "__main__":
    main()
