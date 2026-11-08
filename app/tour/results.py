"""Model and evaluation evidence for chapter 2, read from committed result files only.

Every block carries `source` (the file it came from) so the UI can show provenance. All of these results are
CPTAC clear-cell renal carcinoma (ccRCC) or graph-level link prediction; none is a colorectal evaluation.
"""
from __future__ import annotations

import json
from pathlib import Path

from common.contracts import ROOT

R = ROOT / "models" / "results"
E = ROOT / "eval" / "results"


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def graph_stats() -> dict | None:
    p = ROOT / "graph" / "ws1_report.json"
    d = _load(p)
    if not d:
        return None
    return {"source": _rel(p), "nodes_by_type": d.get("nodes_by_type"), "nodes_by_origin": d.get("nodes_by_origin"),
            "n_nodes": sum((d.get("nodes_by_type") or {}).values()), "edges_directed_total": d.get("edges_directed_total"),
            "edges_by_origin": d.get("edges_by_origin"), "edge_types": d.get("edge_types_full"),
            "dismech_vs_primekg_overlap": d.get("dismech_vs_primekg_overlap"),
            "dismech_edges_kept": d.get("dismech_edges_kept")}


def gnn() -> dict | None:
    p, q = R / "gnn_a_main.json", R / "gnn_a_no_dismech.json"
    d, nd = _load(p), _load(q)
    if not d:
        return None
    a = d.get("args", {})
    key = d.get("test_linkpred_key_relations") or {}
    return {
        "source": _rel(p),
        "architecture": {"encoder": f"{a.get('layers')}-layer heterogeneous GraphSAGE (HeteroConv, mean over edge "
                                    f"types), per-type skip + LayerNorm", "hidden": a.get("hidden"),
                         "decoder": "DistMult (one diagonal relation vector per edge type)",
                         "node_input": "text embeddings of node names (all-MiniLM-L6-v2) -> per-type Linear",
                         "parameters": d.get("parameters"), "message_edges": d.get("message_edges"),
                         "edge_types": d.get("edge_types_message_passing"), "steps": a.get("steps"),
                         "mask": a.get("mask"), "split": "90/5/5 over undirected pairs; val/test pairs removed "
                                                         "from message passing", "described_in": "models/gnn_a.py"},
        "test_linkpred": d.get("test_linkpred_macro"),
        "key_relations": [{"relation": k, **v} for k, v in key.items()],
        "train_curve": [{"step": r["step"], "val_mrr": r.get("val_mrr"), "loss": r.get("loss")}
                        for r in d.get("train_curve", [])],
        "no_dismech": {"source": _rel(q), "test_linkpred": (nd or {}).get("test_linkpred_macro"),
                       "edge_types": (nd or {}).get("edge_types_message_passing")} if nd else None,
        "runtime_s": d.get("runtime_s"),
    }


def e1() -> dict | None:
    p = E / "e1_summary.json"
    d = _load(p)
    if not d:
        return None
    s = d.get("summary", {})
    keep = [("bfs", "BFS (nearest by degree)"), ("text_pcst", "Text-prize PCST (G-Retriever style)"),
            ("ppr_pcst", "PageRank-prize PCST"), ("text_ppr_pcst", "Text + PageRank PCST"),
            ("gnn_pcst", "GNN-B: PageRank + GNN-A prizes (ours)")]
    return {"source": _rel(p), "n_test_diseases": d.get("n_test_diseases"), "budget": d.get("budget"),
            "variant": d.get("variant"), "ceiling_within_2hop": d.get("ceiling_targets_within_2hop"),
            "methods": [{"id": k, "label": lab, **{m: s[k].get(m) for m in ("recall_pooled", "sem", "recall50_gene",
                                                                           "recall50_go", "mrr")}}
                        for k, lab in keep if k in s],
            "task": ("Held-out diseases: hide their gene and GO edges, seed with the disease and its phenotypes, "
                     "retrieve a 50-node subgraph, and count how many hidden targets come back."),
            "note": "E1 numbers use cross-fitted retriever weights (models/results/gnn_b_best_weights_typed.json is "
                    "for the demo API only)."}


def e2() -> dict | None:
    p = E / "e2_summary.json"
    d = _load(p)
    if not d or "y_stage" not in d:
        return None
    y = d["y_stage"]
    names = {"clinical_lr": "Clinical features (LR)", "prot_pca_lr": "Proteomics PCA (LR)",
             "concat_lr": "Clinical + proteomics (LR)", "gnn": "GNN-A patient embedding",
             "gnn_no_dismech": "GNN-A without DisMech", "gnn_mut": "GNN-A + mutations"}
    models = [{"id": k, "label": names.get(k, k), **v} for k, v in y.get("models", {}).items()]
    pb = (y.get("paired_bootstrap_vs_best_baseline") or {}).get("gnn_mut") or {}
    return {"source": _rel(p), "label": "Stage III-IV vs I-II (y_stage)", "n": y.get("n"),
            "positives": y.get("positives"), "models": models, "best_baseline": y.get("best_baseline"),
            "gnn_mut_vs_best": pb,
            "honest_readout": ("No GNN variant beats the proteomics-PCA baseline on stage prediction; the paired "
                               "bootstrap difference for the best GNN (with mutations) is "
                               f"{pb.get('mean_diff')} AUROC, 95% CI {pb.get('ci95')}."),
            "cohort": "CPTAC ccRCC, 110 tumours, 5-fold out-of-fold"}


def e3() -> dict | None:
    p = E / "e3_summary.json"
    d = _load(p)
    if not d:
        return None
    return {"source": _rel(p), "by_mode": d.get("by_mode"), "created": d.get("created"),
            "task": "20 questions (10 patient, 10 disease) answered by the grounded agent and by the same LLM "
                    "without tools; claims checked by the strict verifier."}


def e4() -> dict | None:
    out = {}
    for key, name in (("masked", "e4_cohort_rv.json"), ("ceiling", "e4_cohort_full_rv.json")):
        p = E / name
        d = _load(p)
        if d and d.get("overall"):
            out[key] = {"source": _rel(p), "eval": d.get("eval"), "n": d["overall"].get("n"),
                        "step_recovery": d["overall"].get("step_recovery_kg") or d["overall"].get("step_recovery"),
                        "mean_steps_recovered": d["overall"].get("mean_steps_recovered"),
                        "n_leaky": d.get("n_leaky"), "plan_rule": d.get("plan_rule"),
                        "model": (d.get("run_manifest") or {}).get("model")}
    if not out:
        return None
    out["task"] = ("Pre-registered cohort of 20 CPTAC ccRCC patients: with ccRCC's own mechanism edges masked from "
                   "the graph, does the agent recover the five DisMech mechanism steps from patient data?")
    return out


def bundle() -> dict:
    return {"graph": graph_stats(), "gnn": gnn(), "e1": e1(), "e2": e2(), "e3": e3(), "e4": e4(),
            "scope": ("All model evaluations on this page were run on CPTAC clear-cell renal carcinoma (kidney) or on "
                      "graph link prediction. They are not colorectal-cancer evaluations, and the kidney-cancer "
                      "stage model is never applied to Alexander Young.")}
