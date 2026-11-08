#!/usr/bin/env python3
"""
Run the WS2 patient pipeline on another CPTAC cancer WITHOUT touching the frozen ccRCC outputs.

  python -m graph.patients.build_cohort --cancer brca

Reuses ws2_proteomics, ws2_clinical, ws2_extra_edges and export_patients unchanged; only their output folders and
the disease-specific settings below are swapped. Writes:
  data/processed/<cancer>/            intermediates (protein z-scores, edges, QC files)
  data/processed/<cancer>/patients/   contract-shaped tables (same columns as data/processed/patients/)
The graph, GNN-A and agent are NOT retrained (portability test).
"""
import argparse
import re
import sys
from pathlib import Path

CANCERS = {
    "brca": {
        "dataset": "Brca",
        # no normal tissue in CPTAC-BRCA -> z-scores relative to the tumor cohort
        "z_mode": "cohort",
        "qc_markers": ["ERBB2", "GRB7", "ESR1", "GATA3", "FOXA1", "KRT5", "CDH1"],
        "drivers": ["PIK3CA", "TP53", "GATA3", "CDH1", "MAP3K1", "KMT2C", "PTEN", "AKT1", "ERBB2"],
        # the CURRENT cancer must never appear as a 'prior cancer' edge (BRCA version of the renal-metastasis leak)
        "own_cancer": r"breast|mammary|ductal|lobular|dcis",
        # tumor-describing terms that would leak stage if mapped to phenotypes
        "leaky_terms": ["breast mass", "breast lump", "breast cancer", "axillary", "nipple discharge"],
    },
}
PROC = Path("data/processed")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cancer", required=True, choices=sorted(CANCERS))
    args = ap.parse_args()
    cfg, base = CANCERS[args.cancer], PROC / args.cancer
    base.mkdir(parents=True, exist_ok=True)
    if not (PROC / "nodes.parquet").exists():
        raise SystemExit("data/processed/nodes.parquet missing: run python -m graph.build_kg --no-torch first")

    import cptac
    from graph.patients import export_patients as ex
    from graph.patients import ws2_clinical as wc
    from graph.patients import ws2_extra_edges as wx
    from graph.patients import ws2_proteomics as wp

    ds = getattr(cptac, cfg["dataset"])()
    prot = ds.get_proteomics("umich")
    tumors = {i for i in prot.index.astype(str) if "." not in i}
    print(f"[{args.cancer}] proteomics: {len(tumors)} tumors x {prot.shape[1]} protein columns")

    def clinical():
        c = ds.get_dataframe("clinical", "mssm")
        c = c.loc[:, ~c.columns.duplicated()]
        c.index = c.index.astype(str)
        return c[c.index.isin(tumors)]  # only patients with proteomics

    def mutations():
        m = ds.get_somatic_mutation("harmonized")
        m.index = m.index.astype(str)
        return m[m.index.isin(tumors)]

    # 1) proteins -> signed patient-protein edges
    wp.OUT, wp.MARKERS = base, cfg["qc_markers"]
    wp.load_from_cptac = lambda: prot
    sys.argv = ["ws2_proteomics", "--mode", cfg["z_mode"]]
    print("\n=== proteomics ===")
    wp.main()

    # 2) clinical -> phenotypes, mutations, labels (labels are for descriptive checks only, never clustering)
    wc.OUT, wc.DRIVERS = base, cfg["drivers"]
    wc.load = lambda: (clinical(), mutations())
    sys.argv = ["ws2_clinical"]
    print("\n=== clinical + mutations ===")
    wc.main()

    # 3) medications, exposures, prior (non-current) cancers; shares the cached PrimeKG node list
    wx.OUT = base
    wx.RENAL = re.compile(cfg["own_cancer"], re.I)
    wx.load_clinical = clinical
    sys.argv = ["ws2_extra_edges"]
    print("\n=== drugs / exposures / history ===")
    wx.main()

    # 4) contract-shaped tables; graph nodes still come from the shared data/processed/nodes.parquet
    orig_read = ex.read
    ex.read = lambda name, b=None: orig_read(name, PROC if name == "nodes" else base)
    ex.PROC, ex.OUT = base, base / "patients"
    ex.LEAKY_PHENOTYPE_TERMS = list(ex.LEAKY_PHENOTYPE_TERMS) + cfg["leaky_terms"]
    sys.argv = ["export_patients"]
    print("\n=== export ===")
    ex.main()
    print(f"\nDone. {args.cancer} tables in {base / 'patients'} (ccRCC tables in {PROC / 'patients'} untouched).")


if __name__ == "__main__":
    main()
