#!/usr/bin/env python3
"""
WS1-04: BIFO edge classes for edges.parquet.

BIFO (Biological Information Flow Ontology, Taylor lab) says propagation over a biomedical KG should only
follow edges that carry biological signal, in the direction the signal flows along the backbone
    exposure / drug -> gene/protein -> molecular function / process / pathway -> mechanism -> phenotype -> disease
Every directed edge gets:
  bifo_class      propagating   mechanistic step on the backbone (e.g. gene -> pathway, drug -> target)
                  observational real association that should not carry state on its own (PPI, gene-disease
                                association, expression context, therapeutic use). Kept, may inform weighting.
                  dropped       structure, not biology: ontology hierarchies, similarity, administrative metadata
  bifo_forward    True if a propagating edge points WITH the flow (e.g. gene -> pathway); its stored reverse
                  (pathway -> gene) is False. Always False for observational/dropped edges.

This is a LOCAL mapping of PrimeKG + dismech relations following BIFO's principles. BIFO's published
mapping targets the Data Distillery KG's predicates; the paper allows documented local extensions.

Adds boolean flag columns to edges.parquet so the existing GRAPH_VARIANTS mechanism can drop them:
  bifo_dropped        bifo_class == dropped
  bifo_observational  bifo_class == observational
  bifo_against_flow   propagating edge pointing against the flow
Writes graph/bifo_edge_classes.csv (one row per directed edge type, with the reason: commit this).

Run from the repo root, after graph/build_kg.py (rerun it whenever edges.parquet is rebuilt):
  python -m graph.bifo
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

PROC = Path("data/processed")
EDGES = PROC / "edges.parquet"
TABLE = Path("graph/bifo_edge_classes.csv")
CCRCC_ID = "MONDO:0005005"
CCRCC_GENES = {"VHL": "NCBIGene:7428", "EPAS1": "NCBIGene:2034", "VEGFA": "NCBIGene:7422",
               "PBRM1": "NCBIGene:55193", "BAP1": "NCBIGene:8314"}

P, O, D = "propagating", "observational", "dropped"

# PrimeKG relations are stored in both directions under one name; the forward direction is a (src, dst) pair.
PRIMEKG = {
    "bioprocess_protein":    (P, {("gene", "biological_process")}, "gene/protein -> process it takes part in"),
    "molfunc_protein":       (P, {("gene", "molecular_function")}, "gene/protein -> its molecular function"),
    "pathway_protein":       (P, {("gene", "pathway")}, "gene/protein -> pathway"),
    "phenotype_protein":     (P, {("gene", "phenotype")}, "gene -> phenotype (HPO gene annotations, mostly Mendelian)"),
    "disease_phenotype_positive": (P, {("phenotype", "disease")},
                                   "phenotype -> disease: evidence accumulation on the backbone"),
    "drug_protein":          (P, {("drug", "gene")}, "drug -> target/enzyme/transporter: perturbation enters at the protein"),
    "exposure_protein":      (P, {("exposure", "gene")}, "exposure -> gene/protein it perturbs (CTD)"),
    "exposure_bioprocess":   (P, {("exposure", "biological_process")}, "exposure -> affected process (CTD)"),
    "exposure_molfunc":      (P, {("exposure", "molecular_function")}, "exposure -> affected function (CTD)"),
    "protein_protein":       (O, None, "undirected interaction; BIFO keeps PPI observational unless direction is known"),
    "disease_protein":       (O, None, "gene-disease association (mixed curated/statistical); BIFO routes gene -> disease "
                                       "through processes and phenotypes"),
    "disease_phenotype_negative": (O, None, "phenotype explicitly ABSENT in the disease; must never carry signal"),
    "cellcomp_protein":      (O, None, "subcellular location: context, not flow"),
    "anatomy_protein_present": (O, None, "where a protein is expressed: context"),
    "anatomy_protein_absent":  (O, None, "where a protein is not expressed: context"),
    "drug_effect":           (O, None, "reported clinical/side effect, no mechanism given"),
    "drug_drug":             (O, None, "drug-drug interaction: pharmacological, not a molecular flow"),
    "indication":            (O, None, "therapeutic use: clinical, not a molecular flow"),
    "off_label_use":         (O, None, "therapeutic use: clinical"),
    "contraindication":      (O, None, "prescribing rule: clinical"),
    "exposure_disease":      (O, None, "exposure-disease association (epidemiological)"),
    "exposure_cellcomp":     (O, None, "location context"),
    "disease_disease":       (D, None, "MONDO hierarchy"),
    "phenotype_phenotype":   (D, None, "HPO hierarchy"),
    "anatomy_anatomy":       (D, None, "UBERON hierarchy"),
    "bioprocess_bioprocess": (D, None, "GO hierarchy"),
    "molfunc_molfunc":       (D, None, "GO hierarchy"),
    "cellcomp_cellcomp":     (D, None, "GO hierarchy"),
    "pathway_pathway":       (D, None, "Reactome hierarchy"),
    "exposure_exposure":     (D, None, "exposure hierarchy"),
}

# dismech edges are dm_<predicate> (subject -> object) plus an explicit dm_<predicate>_rev.
# Forward is "fwd" (the predicate's own direction) or "rev" (the _rev edge is the one with the flow).
DISMECH = {
    "dm_causes":               (P, "fwd", "curated causal step: subject causes object"),
    "dm_contributes_to":       (P, "fwd", "curated gene -> disease contribution"),
    "dm_disease_has_basis_in": (P, "rev", "mechanism -> the disease it underlies"),
    "dm_has_phenotype":        (P, "rev", "phenotype -> disease (evidence accumulation), as for PrimeKG"),
    "dm_affects":              (P, "rev", "disease affects process; oriented process -> disease along the backbone"),
    "dm_has_participant":      (O, None, "cell/component/drug involved: participation, not flow"),
    "dm_treats_or_applied_or_studied_to_treat": (O, None, "therapeutic use"),
    "dm_disease_has_location": (O, None, "anatomical/cellular location"),
    "dm_has_biomarker":        (O, None, "diagnostic marker"),
    "dm_associated_with":      (O, None, "association"),
    "dm_similar_to":           (D, None, "similarity, not biology"),
    "dm_has_mode_of_inheritance": (D, None, "inheritance-pattern metadata"),
    "dm_is_diagnosed_by":      (D, None, "diagnostic procedure: administrative"),
}


def classify(src_type, rel, dst_type, origin):
    if origin == "dismech":
        is_rev = rel.endswith("_rev")
        base = rel[:-4] if is_rev else rel
        if base not in DISMECH:
            return None
        cls, fwd, why = DISMECH[base]
        forward = cls == P and ((fwd == "fwd" and not is_rev) or (fwd == "rev" and is_rev))
    else:
        if rel not in PRIMEKG:
            return None
        cls, pairs, why = PRIMEKG[rel]
        forward = cls == P and (src_type, dst_type) in pairs
    return cls, bool(forward), why


def hops(src, dst, start, targets, max_hops=3):
    """Shortest hop count from start to each target, following src -> dst (edges stored both ways = undirected)."""
    seen, frontier, out = {start}, np.array([start]), {}
    for h in range(1, max_hops + 1):
        nxt = np.unique(dst[np.isin(src, frontier)])
        nxt = nxt[~np.isin(nxt, list(seen))]
        for t, g in targets.items():
            if t not in out and g in set(nxt.tolist()):
                out[t] = h
        seen.update(nxt.tolist())
        frontier = nxt
        if len(out) == len(targets) or len(frontier) == 0:
            break
    return {t: out.get(t, f">{max_hops}") for t in targets}


def main():
    cols = ["src", "dst", "src_type", "rel", "dst_type", "origin", "hub"]
    e = pd.read_parquet(EDGES)
    types = e.groupby(["src_type", "rel", "dst_type", "origin"]).size().reset_index(name="n")
    res = [classify(*r) for r in types[["src_type", "rel", "dst_type", "origin"]].itertuples(index=False)]
    missing = types[[r is None for r in res]]
    if len(missing):
        raise SystemExit("Unmapped edge types; add them to PRIMEKG or DISMECH in graph/bifo.py:\n"
                         + missing.to_string(index=False))
    types[["bifo_class", "bifo_forward", "reason"]] = pd.DataFrame(res, index=types.index)

    # sanity: every propagating relation must have exactly one forward direction per type pair
    prop = types[types["bifo_class"] == P].copy()
    prop["base"] = np.where(prop["origin"] == "dismech", prop["rel"].str.replace(r"_rev$", "", regex=True), prop["rel"])
    bad = prop.groupby(["base", "origin"])["bifo_forward"].agg(["sum", "size"])
    bad = bad[(bad["sum"] == 0) | (bad["sum"] == bad["size"])]
    if len(bad):
        raise SystemExit(f"Propagating relations with no forward (or no reverse) direction:\n{bad}")

    TABLE.parent.mkdir(parents=True, exist_ok=True)
    types.sort_values(["bifo_class", "origin", "rel", "src_type"]).to_csv(TABLE, index=False)
    print(f"wrote {TABLE} ({len(types)} directed edge types)")

    e = e.drop(columns=[c for c in e.columns if c.startswith("bifo_")])
    e = e.merge(types[["src_type", "rel", "dst_type", "origin", "bifo_class", "bifo_forward"]],
                on=["src_type", "rel", "dst_type", "origin"], how="left", validate="many_to_one")
    e["bifo_dropped"] = e["bifo_class"] == D
    e["bifo_observational"] = e["bifo_class"] == O
    e["bifo_against_flow"] = (e["bifo_class"] == P) & ~e["bifo_forward"]
    e["bifo_class"] = e["bifo_class"].astype("category")
    e = e.sort_values("edge_row") if "edge_row" in e.columns else e
    e.to_parquet(EDGES, index=False)
    print(f"added bifo_class, bifo_forward, bifo_dropped, bifo_observational, bifo_against_flow to {EDGES}")

    # summary
    n = len(e)
    by = e["bifo_class"].value_counts()
    slim = e[~e["hub"]]
    by_slim = slim["bifo_class"].value_counts()
    summary = {
        "directed_edges": n,
        "class_counts_full": {k: int(v) for k, v in by.items()},
        "class_share_full": {k: round(v / n, 3) for k, v in by.items()},
        "class_counts_slim (hub dropped)": {k: int(v) for k, v in by_slim.items()},
        "kept_share_slim (propagating + observational)": round(1 - by_slim.get(D, 0) / len(slim), 3),
        "paper_reference": "BIFO paper on the Data Distillery KG: ~70.7% of edges kept",
    }

    # ccRCC reachability under each variant (undirected hops), and with the flow (gene -> ... -> ccRCC)
    nodes = pd.read_parquet(PROC / "nodes.parquet", columns=["gidx", "curie"])
    g = dict(zip(nodes["curie"], nodes["gidx"]))
    if CCRCC_ID in g:
        targets = {s: g[c] for s, c in CCRCC_GENES.items() if c in g}
        variants = {
            "slim": ~e["hub"],
            "bifo (drop hierarchies)": ~e["hub"] & ~e["bifo_dropped"],
            "bifo_strict (propagating only)": ~e["hub"] & (e["bifo_class"] == P),
        }
        summary["ccrcc_hops_undirected"] = {name: hops(e.loc[m, "src"].to_numpy(), e.loc[m, "dst"].to_numpy(),
                                                       g[CCRCC_ID], targets) for name, m in variants.items()}
        flow = ~e["hub"] & (e["bifo_class"] == P) & e["bifo_forward"]
        s_, d_ = e.loc[flow, "src"].to_numpy(), e.loc[flow, "dst"].to_numpy()
        summary["gene_to_ccrcc_hops_with_flow (bifo_flow)"] = {
            sym: hops(s_, d_, gi, {"ccRCC": g[CCRCC_ID]})["ccRCC"] for sym, gi in targets.items()}
    PROC.mkdir(parents=True, exist_ok=True)
    (PROC / "bifo_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))
    print("\nNext: add the bifo variants to GRAPH_VARIANTS in common/contracts.py, then python -m common.validate all")


if __name__ == "__main__":
    main()
