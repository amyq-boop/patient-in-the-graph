#!/usr/bin/env python3
"""
WS2 (steps 3-5): clinical -> phenotype edges, mutation -> gene edges, labels, clinical features, CV folds.

Outputs in data/processed/ (parquet if pyarrow is installed, otherwise CSV):
  patient_phenotype_edges   patient -> HPO term (comorbidities from medical_condition, obesity from BMI)
  patient_mutation_edges    patient -> gene (protein-altering somatic mutations)
  labels                    stage, grade, overall survival, recurrence (NEVER use these as edges/features)
  clinical_features         non-leaky clinical fields for the WS3 clinical-only baseline
  folds                     5 fixed stratified folds by patient; everyone uses these
  unmatched_conditions.csv  medical_condition terms no rule matched; extend CONDITION_MAP with them
  ws2_clinical_qc.json      counts and sanity checks

Usage:
  python ws2_clinical.py                 # build everything
  python ws2_clinical.py --verify-ids    # also check every HPO ID's label with Translator NodeNorm (needs internet)
  python ws2_clinical.py --mut-genes all # mutation edges for all genes (default: Cancer Gene Census genes only)
"""
import argparse
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("data/processed")

# Regex on free text -> HPO term. Run --verify-ids once to confirm each ID's label.
CONDITION_MAP = [
    (r"hypertens|high blood pressure|\bhtn\b", "HP:0000822", "Hypertension"),
    (r"diabet", "HP:0000819", "Diabetes mellitus"),
    (r"hyperlipid|hypercholesterol|dyslipid|high cholesterol", "HP:0003077", "Hyperlipidemia"),
    (r"obes", "HP:0001513", "Obesity"),
    (r"chronic kidney|\bckd\b|renal insufficiency|renal failure", "HP:0012622", "Chronic kidney disease"),
    (r"hypothyroid", "HP:0000821", "Hypothyroidism"),
    (r"coronary|\bcad\b", "HP:0001677", "Coronary artery atherosclerosis"),
    (r"myocardial infarction|heart attack", "HP:0001658", "Myocardial infarction"),
    (r"\bcopd\b|chronic obstructive|emphysema", "HP:0006510", "Chronic pulmonary obstruction"),
    (r"asthma", "HP:0002099", "Asthma"),
    (r"reflux|\bgerd\b", "HP:0002020", "Gastroesophageal reflux"),
    (r"depress", "HP:0000716", "Depressivity"),
    (r"anemi|anaemi", "HP:0001903", "Anemia"),
    (r"kidney stone|nephrolith|renal calcul", "HP:0000787", "Nephrolithiasis"),
    (r"\bgout\b", "HP:0001997", "Gout"),
    (r"atrial fib", "HP:0005110", "Atrial fibrillation"),
    (r"heart failure stage (iv|iii|ii|i|[1-4])\b", "HP:0001635", "Congestive heart failure"),  # stage 0 = none
    (r"sleep apnea", "HP:0010535", "Sleep apnea"),
    (r"pulmonary embol|thromboemb|deep vein thromb|\bdvt\b", "HP:0001907", "Thromboembolism"),
]
OBESITY = ("HP:0001513", "Obesity")

# Protein-altering MAF classes kept as mutation edges (silent/UTR/intron/RNA are dropped)
KEEP_CLASSES = {"Missense_Mutation", "Nonsense_Mutation", "Frame_Shift_Del", "Frame_Shift_Ins",
                "In_Frame_Del", "In_Frame_Ins", "Splice_Site", "Nonstop_Mutation", "Translation_Start_Site"}
DRIVERS = ["VHL", "PBRM1", "SETD2", "BAP1", "KDM5C", "PTEN", "TP53", "MTOR"]

MISSING = re.compile(r"^\s*$|not reported|unknown|not evaluated|not applicable|^nan$|^none$", re.I)


# ---------- helpers ----------
def save(df, name, index=False):
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        import pyarrow  # noqa: F401
        path = OUT / f"{name}.parquet"
        df.to_parquet(path, index=index)
    except ImportError:
        path = OUT / f"{name}.csv"
        df.to_csv(path, index=index)
    print(f"  saved {path}  shape={df.shape}")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def col(df, name):
    """Find a column by normalized name (survives spacing/punctuation differences). Returns a Series or None."""
    hits = [c for c in df.columns if norm(c) == norm(name)]
    return df[hits[0]] if hits else None


def is_missing(v):
    return pd.isna(v) or bool(MISSING.search(str(v)))


def num(series):
    return pd.to_numeric(series, errors="coerce") if series is not None else None


# ---------- load ----------
def load():
    import cptac
    cc = cptac.Ccrcc()
    clin = cc.get_dataframe("clinical", "mssm")
    maf = cc.get_dataframe("somatic_mutation", "harmonized")
    clin = clin.loc[:, ~clin.columns.duplicated()]  # e.g. 'history_source' appears 3 times
    clin.index = clin.index.astype(str)
    maf.index = maf.index.astype(str)
    return clin, maf


# ---------- step 3a: phenotype edges ----------
def phenotype_edges(clin):
    """One edge per (patient, HPO term). source_text is the full '|'-separated term that matched."""
    rows, unmatched = [], {}
    cond = col(clin, "medical_condition")
    if cond is None:
        print("WARNING: no medical_condition column found")
    for pid in clin.index:
        text = "" if cond is None or is_missing(cond[pid]) else str(cond[pid])
        hits = set()
        for tok in [t.strip() for t in re.split(r"[|;]+", text) if t.strip()]:
            if is_missing(tok):
                continue
            matched = False
            for pattern, curie, label in CONDITION_MAP:
                m = re.search(pattern, tok, re.I)
                if m:
                    matched = True
                    if curie not in hits:
                        hits.add(curie)
                        rows.append({"patient_id": pid, "curie": curie, "label": label,
                                     "source_field": "medical_condition", "source_text": tok,
                                     "matched_text": m.group(0)})
            if not matched:
                unmatched[tok.lower()] = unmatched.get(tok.lower(), 0) + 1
        bmi = bmi_for(clin, pid)
        if bmi is not None and bmi >= 30 and OBESITY[0] not in hits:
            rows.append({"patient_id": pid, "curie": OBESITY[0], "label": OBESITY[1],
                         "source_field": "bmi", "source_text": f"BMI {bmi:.1f}", "matched_text": f"BMI {bmi:.1f}"})
    edges = pd.DataFrame(rows, columns=["patient_id", "curie", "label", "source_field", "source_text", "matched_text"])
    unmatched = pd.Series(unmatched, name="count", dtype=int).sort_values(ascending=False).rename_axis("term").reset_index()
    return edges, unmatched


def bmi_for(clin, pid):
    b = num(col(clin, "bmi"))
    if b is not None and pd.notna(b[pid]):
        return float(b[pid])
    h, w = num(col(clin, "height_at_time_of_surgery_cm")), num(col(clin, "weight_at_time_of_surgery_kg"))
    if h is not None and w is not None and pd.notna(h[pid]) and pd.notna(w[pid]) and h[pid] > 0:
        return float(w[pid] / (h[pid] / 100) ** 2)
    return None


# ---------- step 3b: mutation edges ----------
def mutation_edges(maf, patients, mode):
    m = maf[maf["Mutation"].isin(KEEP_CLASSES)].copy()
    m = m[m.index.isin(patients)]
    if mode == "cgc" and "CGC_Name" in m.columns:
        m = m[m["CGC_Name"].notna() | m["Gene"].isin(DRIVERS)]
    elif mode == "drivers":
        m = m[m["Gene"].isin(DRIVERS)]
    m["patient_id"] = m.index
    edges = (m.groupby(["patient_id", "Gene"])
               .agg(entrez=("Entrez_Gene_Id", "first"),
                    n_mutations=("Mutation", "size"),
                    classes=("Mutation", lambda s: ";".join(sorted(set(s)))),
                    protein_changes=("Location", lambda s: ";".join(map(str, s.dropna()))))
               .reset_index().rename(columns={"Gene": "gene_symbol"}))
    edges["entrez"] = edges["entrez"].astype("Int64")
    edges["curie"] = "NCBIGene:" + edges["entrez"].astype(str)
    edges["is_driver"] = edges["gene_symbol"].isin(DRIVERS)
    return edges


# ---------- step 4: labels and clinical features ----------
def parse_stage(v):
    if is_missing(v):
        return np.nan
    s = str(v).upper()
    m = re.search(r"STAGE\s*(IV|III|II|I)", s) or re.search(r"^\s*(IV|III|II|I)", s)
    return {"I": 1, "II": 2, "III": 3, "IV": 4}[m.group(1)] if m else np.nan


def parse_grade(v):
    if is_missing(v):
        return np.nan
    m = re.search(r"(?:G|GRADE)\s*([1-4])", str(v).upper())
    return int(m.group(1)) if m else np.nan


def build_labels(clin):
    lab = pd.DataFrame(index=clin.index)
    lab.index.name = "patient_id"
    st, gr = col(clin, "tumor_stage_pathological"), col(clin, "histologic_grade")
    lab["stage_raw"] = st
    lab["stage"] = st.map(parse_stage) if st is not None else np.nan
    lab["stage_high"] = (lab["stage"] >= 3).astype(float).where(lab["stage"].notna())
    lab["grade_raw"] = gr
    lab["grade"] = gr.map(parse_grade) if gr is not None else np.nan
    lab["grade_high"] = (lab["grade"] >= 3).astype(float).where(lab["grade"].notna())
    lab["os_days"] = num(col(clin, "Overall survival, days"))
    lab["os_event"] = num(col(clin, "Survival status (1, dead; 0, alive)"))
    lab["rfs_days"] = num(col(clin, "Recurrence-free survival, days"))
    lab["rfs_event"] = num(col(clin, "Recurrence status (1, yes; 0, no)"))
    return lab.reset_index()


def build_clinical_features(clin, pheno):
    """Non-leaky features only. Excluded on purpose: stage, T/N/M, grade, tumor size (defines pT), necrosis,
    margins, residual tumor, lymph nodes, perineural invasion, and everything recorded after surgery."""
    f = pd.DataFrame(index=clin.index)
    f.index.name = "patient_id"
    f["age"] = num(col(clin, "age"))
    sex = col(clin, "sex")
    f["sex_male"] = sex.str.lower().eq("male").astype(float).where(~sex.map(is_missing)) if sex is not None else np.nan
    f["bmi"] = [bmi_for(clin, p) for p in clin.index]
    smk = col(clin, "tobacco_smoking_history")

    def ever(v):
        if is_missing(v):
            return np.nan
        s = str(v).lower()
        return 0.0 if ("non-smoker" in s or "never" in s) else (1.0 if "smok" in s or "reformed" in s else np.nan)
    f["ever_smoker"] = smk.map(ever) if smk is not None else np.nan
    f["pack_years"] = num(col(clin, "number_of_pack_years_smoked"))
    ecog = col(clin, "performance_status_assessment_ecog_performance_status_score")
    f["ecog"] = ecog.astype(str).str.extract(r"(\d)")[0].astype(float) if ecog is not None else np.nan
    flags = (pheno.assign(v=1.0).pivot_table(index="patient_id", columns="label", values="v", aggfunc="max")
             .add_prefix("has_") if len(pheno) else pd.DataFrame())
    f = f.join(flags).fillna({c: 0.0 for c in flags.columns})
    return f.reset_index()


# ---------- step 5: folds ----------
def make_folds(labels, primary, k=5, seed=42):
    y = labels[primary].fillna(-1).astype(int).to_numpy()
    try:
        from sklearn.model_selection import StratifiedKFold
        skf = StratifiedKFold(n_splits=k, shuffle=True, random_state=seed)
        fold = np.empty(len(y), dtype=int)
        for i, (_, test) in enumerate(skf.split(np.zeros(len(y)), y)):
            fold[test] = i
    except ImportError:  # manual stratification
        rng, fold = np.random.default_rng(seed), np.empty(len(y), dtype=int)
        for cls in np.unique(y):
            idx = rng.permutation(np.where(y == cls)[0])
            fold[idx] = np.arange(len(idx)) % k
    return pd.DataFrame({"patient_id": labels["patient_id"], "fold": fold, "stratified_on": primary})


# ---------- optional: verify HPO IDs ----------
def verify_ids(curies):
    import requests
    r = requests.get("https://nodenormalization-sri.renci.org/get_normalized_nodes",
                     params={"curie": sorted(curies)}, timeout=60)
    r.raise_for_status()
    res = r.json()
    (OUT / "nodenorm_hpo_cache.json").write_text(json.dumps(res, indent=2))
    expected = {c: l for _, c, l in CONDITION_MAP}
    print("\nHPO ID check (NodeNorm label vs. our label):")
    for c in sorted(curies):
        node = res.get(c)
        if not node:
            print(f"  {c:12s} ours: {expected.get(c, '?'):32s} NOT FOUND   <-- FIX")
            continue
        pref, got = node["id"].get("identifier"), node["id"].get("label", "")
        print(f"  {c:12s} ours: {expected.get(c, '?'):32s} nodenorm: {got}  (preferred ID: {pref})")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mut-genes", choices=["cgc", "drivers", "all"], default="cgc")
    ap.add_argument("--primary", choices=["auto", "stage_high", "grade_high"], default="stage_high")  # contract: y_stage
    ap.add_argument("--verify-ids", action="store_true")
    args = ap.parse_args()

    clin, maf = load()
    patients = list(clin.index)
    cond = col(clin, "medical_condition")
    if cond is not None:
        print("\nTop raw medical_condition values:")
        print(cond.value_counts(dropna=False).head(15).to_string())

    pheno, unmatched = phenotype_edges(clin)
    muts = mutation_edges(maf, patients, args.mut_genes)
    labels = build_labels(clin)
    feats = build_clinical_features(clin, pheno)

    minority = {c: float(min(labels[c].mean(), 1 - labels[c].mean())) for c in ["stage_high", "grade_high"]
                if labels[c].notna().any()}
    primary = max(minority, key=minority.get) if args.primary == "auto" else args.primary
    folds = make_folds(labels, primary)

    save(pheno, "patient_phenotype_edges")
    save(muts, "patient_mutation_edges")
    save(labels, "labels")
    save(feats, "clinical_features")
    save(folds, "folds")
    unmatched.to_csv(OUT / "unmatched_conditions.csv", index=False)

    ph_pp = pheno.groupby("patient_id").size().reindex(patients, fill_value=0)
    mu_pp = muts.groupby("patient_id").size().reindex(patients, fill_value=0)
    qc = {
        "n_patients": len(patients),
        "phenotype_edges_total": int(len(pheno)),
        "phenotype_edges_per_patient": ph_pp.value_counts().sort_index().to_dict(),
        "phenotype_term_counts": pheno["label"].value_counts().to_dict(),
        "unmatched_condition_terms": int(len(unmatched)),
        "mutation_gene_mode": args.mut_genes,
        "patients_in_maf": len(set(maf.index) & set(patients)),
        "mutation_edges_total": int(len(muts)),
        "mutation_edges_per_patient_median": float(mu_pp.median()),
        "driver_mutation_frequency": {g: round(float(muts.query("gene_symbol == @g")["patient_id"].nunique()
                                                     / len(patients)), 2) for g in DRIVERS},
        "stage_counts": labels["stage"].value_counts(dropna=False).sort_index().to_dict(),
        "grade_counts": labels["grade"].value_counts(dropna=False).sort_index().to_dict(),
        "stage_high_rate": round(float(labels["stage_high"].mean()), 2),
        "grade_high_rate": round(float(labels["grade_high"].mean()), 2),
        "primary_label": primary,
        "os_events": int(labels["os_event"].sum()),
        "recurrence_events": int(labels["rfs_event"].sum()),
        "secondary_label_ok (>=15 events)": {"os": bool(labels["os_event"].sum() >= 15),
                                             "recurrence": bool(labels["rfs_event"].sum() >= 15)},
        "fold_sizes": folds["fold"].value_counts().sort_index().to_dict(),
    }
    qc = json.loads(json.dumps(qc, default=str))  # make keys/values JSON-safe
    (OUT / "ws2_clinical_qc.json").write_text(json.dumps(qc, indent=2))
    print(json.dumps(qc, indent=2))
    if len(unmatched):
        print(f"\n{len(unmatched)} medical_condition terms matched no rule. Top 10 (see unmatched_conditions.csv):")
        print(unmatched.head(10).to_string(index=False))
    if args.verify_ids:
        verify_ids({c for _, c, _ in CONDITION_MAP})


if __name__ == "__main__":
    main()
