#!/usr/bin/env python3
"""
Molecular case briefs: one JSON per patient, for the demo's "molecular brief" panel and the agent.

  python -m graph.patients.make_briefs --cancer all        # ccrcc and brca
  python -m graph.patients.make_briefs --cancer brca

Writes <out>/<patient_id>.json plus <out>/index.json (default out: data/processed/briefs).
Each brief: measured proteins (top up/down), somatic mutations, phenotypes, medications, exposures, prior cancers,
and an explicit list of MISSING information. Every observation keeps a citable source ID (pt:<patient>:<rel>:<curie>).
Outcome labels (stage, grade, recurrence, survival) are deliberately NOT included.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROC = Path("data/processed")
COHORTS = {
    "ccrcc": {"dir": PROC, "dataset": "CPTAC-CCRCC discovery cohort (cptac: umich proteomics, mssm clinical, harmonized MAF)",
              "key_proteins": ["VHL", "EPAS1", "PBRM1", "BAP1", "SETD2", "CA9", "VEGFA"]},
    "brca": {"dir": PROC / "brca", "dataset": "CPTAC-BRCA prospective cohort (cptac: umich proteomics, mssm clinical, harmonized MAF)",
             "key_proteins": ["ERBB2", "ESR1", "PGR", "GATA3", "FOXA1", "KRT5", "CDH1", "PIK3CA", "TP53"]},
}
TOP_N = 15  # proteins listed per direction (full lists stay in patient_proteins)

try:
    from common.contracts import patient_edge_id
except Exception:
    def patient_edge_id(pid, rel, curie):
        return f"pt:{pid}:{rel}:{curie}"


def read(path):
    path = Path(path)
    for p, fn in ((path.with_suffix(".parquet"), pd.read_parquet), (path.with_suffix(".csv"), pd.read_csv)):
        if p.exists():
            try:
                return fn(p)
            except Exception:
                return pd.DataFrame()
    return pd.DataFrame()


def clean(v):
    """JSON-safe scalar: NaN -> None, numpy -> python."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if hasattr(v, "item"):
        v = v.item()
    return None if isinstance(v, float) and pd.isna(v) else v


def by_patient(df):
    return {pid: g for pid, g in df.groupby("patient_id")} if len(df) and "patient_id" in df else {}


def build(cancer, names, out):
    cfg = COHORTS[cancer]
    pt_dir = cfg["dir"] / "patients"
    pts, prot = read(pt_dir / "patients"), read(pt_dir / "patient_proteins")
    if prot.empty:
        print(f"[{cancer}] no patient_proteins found in {pt_dir}; skipping")
        return []
    ph, drugs, expo, hist = (by_patient(read(pt_dir / n)) for n in
                             ["patient_phenotypes", "patient_drugs", "patient_exposures", "patient_history"])
    mut_detail = by_patient(read(cfg["dir"] / "patient_mutation_edges"))  # keeps variant classes + driver flag
    measured = set(read(cfg["dir"] / "ccrcc_prot_z").columns) - {"sample_id"}
    pts_by = {str(r.patient_id): r for r in pts.itertuples()} if len(pts) else {}
    prot_by = by_patient(prot)
    z_ref = str(prot["z_ref"].iloc[0]) if "z_ref" in prot else "unknown"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    index = []

    for pid in sorted(set(prot_by) | set(pts_by)):
        p = pts_by.get(pid)
        pr = prot_by.get(pid, pd.DataFrame())
        missing = []

        def proteins(direction):
            if pr.empty:
                return []
            d = pr[pr["direction"] == direction].sort_values("rank").head(TOP_N)
            rel = "protein_up" if direction == "up" else "protein_down"
            return [{"symbol": r.symbol, "curie": r.curie, "z": round(float(r.z), 2), "rank": int(r.rank),
                     "source_id": patient_edge_id(pid, rel, r.curie)} for r in d.itertuples()]

        def simple(table, rel):
            g = table.get(pid)
            if g is None or g.empty:
                return []
            return [{"curie": r.curie, "name": names.get(r.curie, getattr(r, "name", None) or r.curie),
                     "source_text": clean(getattr(r, "source_text", None)),
                     "source_id": patient_edge_id(pid, rel, r.curie)} for r in g.itertuples()]

        muts = []
        g = mut_detail.get(pid)
        if g is not None and not g.empty:
            for r in g.itertuples():
                muts.append({"symbol": r.gene_symbol, "curie": r.curie, "classes": clean(r.classes),
                             "protein_changes": clean(r.protein_changes), "known_driver": bool(r.is_driver),
                             "source_id": patient_edge_id(pid, "has_mutation", r.curie)})

        phen, meds = simple(ph, "has_phenotype"), simple(drugs, "takes_drug")
        exps, prior = simple(expo, "exposed_to"), simple(hist, "history_of")

        if p is None:
            missing.append("No clinical record for this patient in CPTAC (proteomics only).")
        if z_ref == "cohort":
            missing.append("No matched normal tissue: protein changes are relative to other tumors in the cohort.")
        if pr.empty:
            missing.append("No proteomics measurements.")
        if not muts:
            missing.append("No protein-altering mutations in Cancer Gene Census genes were called (or no mutation data).")
        if not phen:
            missing.append("No comorbidities recorded or mapped to phenotype terms.")
        if not meds:
            missing.append("No medication list recorded or matched.")
        if p is not None and clean(getattr(p, "ever_smoker", None)) is None:
            missing.append("Smoking history unknown.")
        if measured:
            not_measured = [g for g in cfg["key_proteins"] if g not in measured]
            if not_measured:
                missing.append(f"Not measured at protein level in this cohort: {', '.join(not_measured)}.")

        brief = {
            "cancer": cancer, "patient_id": pid, "patient_curie": f"PATIENT:{pid}",
            "data_origin": "real", "execution": "cached", "generated_at": now,
            "source": {"dataset": cfg["dataset"], "cptac_case_id": pid},
            "clinical_summary": clean(getattr(p, "clinical_summary", None)) if p is not None else None,
            "demographics": {"age": clean(getattr(p, "age", None)) if p is not None else None,
                             "sex": clean(getattr(p, "sex", None)) if p is not None else None},
            "proteins": {"reference": z_ref, "n_up": int((pr["direction"] == "up").sum()) if len(pr) else 0,
                         "n_down": int((pr["direction"] == "down").sum()) if len(pr) else 0,
                         "top_up": proteins("up"), "top_down": proteins("down")},
            "mutations": muts, "phenotypes": phen, "medications": meds, "exposures": exps, "prior_cancers": prior,
            "missing_information": missing,
            "subgroup": None,  # filled from clusters.json by the app; not computed here
            "outcome_labels_included": False,
        }
        (out / f"{pid}.json").write_text(json.dumps(brief, indent=2))
        index.append({"cancer": cancer, "patient_id": pid, "n_mutations": len(muts), "n_phenotypes": len(phen),
                      "n_medications": len(meds), "n_missing": len(missing)})
    print(f"[{cancer}] wrote {len(index)} briefs")
    return index


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cancer", default="all", choices=["all"] + sorted(COHORTS))
    ap.add_argument("--out", default=str(PROC / "briefs"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    nodes = read(PROC / "nodes")
    names = dict(zip(nodes["curie"], nodes["name"])) if len(nodes) else {}
    index = []
    for c in (sorted(COHORTS) if args.cancer == "all" else [args.cancer]):
        index += build(c, names, out)
    (out / "index.json").write_text(json.dumps({"cancer": None, "patient_id": None, "data_origin": "real",
                                                "execution": "cached", "briefs": index}, indent=2))
    print(f"wrote {out / 'index.json'} ({len(index)} patients)")


if __name__ == "__main__":
    main()
