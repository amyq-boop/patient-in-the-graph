"""Write mock product files to app/mock_product/ and a mock breast cancer trace (python -m app.make_mock_product).

They follow the shared JSON formats posted to the team on Nov 7 (clusters.json, briefs/<patient_id>.json) so the app
can be built before Anikat's and Kaitlin's real files land in cache/product/ and cache/demo/. Everything here is
fake on purpose: patient IDs contain MOCK, edge IDs are E9000xx, PMIDs are PMID:0000000x, numbers are placeholders.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.contracts import CONTRACT_VERSION, patient_curie, patient_edge_id  # noqa: E402

OUT = Path(__file__).parent / "mock_product"
TRACES = Path(__file__).parent / "mock_traces"
MOCK = {"data_origin": "mock", "execution": "cached"}


def clusters(cancer, ids, centers, names, sigs, pways, seed):
    rng = np.random.default_rng(seed)
    pts = []
    for i, pid in enumerate(ids):
        c = i % len(centers) if i else 0  # the first patient (the demo patient) sits in subgroup 1
        x, y = np.array(centers[c]) + rng.normal(0, .55, 2)
        pts.append({"patient_id": pid, "cluster": c + 1, "x": round(float(x), 3), "y": round(float(y), 3)})
    n = {c + 1: sum(p["cluster"] == c + 1 for p in pts) for c in range(len(centers))}
    return {"cancer": cancer, "patient_id": None, **MOCK, "method": "consensus k-means (mock)", "features": "proteomics",
            "k": len(centers), "stability_ari": 0.71, "silhouette": 0.18, "patients": pts,
            "clusters": [{"cluster": c + 1, "name": names[c], "n": n[c + 1], "signature_proteins": sigs[c],
                          "pathways": pways[c]} for c in range(len(centers))],
            "comparison": {"proteomics": {"k": len(centers), "stability_ari": 0.71},
                           "gnn_embedding": {"k": len(centers), "stability_ari": 0.64}, "ari_between": 0.52},
            "post_hoc": [{"cluster": c + 1, "note": "descriptive only, mock"} for c in range(len(centers))]}


def sp(symbol, curie, direction, effect):
    return {"symbol": symbol, "curie": curie, "direction": direction, "effect": effect, "q": 0.001}


def pw(name, curie, *edges):
    return {"name": name, "curie": curie, "edge_ids": list(edges), "q": 0.004}


def ccrcc():
    ids = ["C3L-MOCK1"] + [f"C3L-MOCK{i:03d}" for i in range(2, 111)]
    return clusters(
        "ccrcc", ids, [(-1.6, .8), (1.4, 1.1), (.2, -1.5)],
        ["Mock subgroup 1: VEGF-high", "Mock subgroup 2: glycolysis-high", "Mock subgroup 3: quiet"],
        [[sp("VEGFA", "NCBIGene:7422", "up", 1.9), sp("VHL", "NCBIGene:7428", "down", -1.4)],
         [sp("ENO1", "NCBIGene:2023", "up", 1.6), sp("EPAS1", "NCBIGene:2034", "up", 0.9)],
         [sp("KDR", "NCBIGene:3791", "down", -0.8)]],
        [[pw("Cellular response to hypoxia (mock)", "REACT:MOCK_HIF", "E900005")],
         [pw("Glycolysis (mock)", "REACT:MOCK_GLY", "E900008")],
         [pw("Angiogenesis (mock)", "REACT:MOCK_ANG", "E900009")]], seed=7)


def brca():
    ids = ["BRCA-MOCK1"] + [f"BRCA-MOCK{i:03d}" for i in range(2, 61)]
    return clusters(
        "brca", ids, [(1.5, .9), (-1.4, .6), (0, -1.6)],
        ["Mock subgroup 1: ERBB2-high", "Mock subgroup 2: ESR1-high", "Mock subgroup 3: proliferative"],
        [[sp("ERBB2", "NCBIGene:2064", "up", 2.2)], [sp("ESR1", "NCBIGene:2099", "up", 1.8)],
         [sp("MKI67", "NCBIGene:4288", "up", 1.3)]],
        [[pw("ERBB2 signaling (mock)", "REACT:MOCK_ERBB2", "E900201")],
         [pw("Estrogen signaling (mock)", "REACT:MOCK_ESR", "E900202")],
         [pw("Cell cycle (mock)", "REACT:MOCK_CC", "E900203")]], seed=11)


def brief(cancer, pid, summary, cluster, up, down, muts, finds, missing):
    def item(rel, sym, curie, z=None):
        d = {"symbol": sym, "curie": curie, "id": patient_edge_id(pid, rel, curie)}
        return {**d, "z": z} if z is not None else d
    return {"cancer": cancer, "patient_id": pid, **MOCK, "summary": summary, "cluster": cluster,
            "proteins_up": [item("protein_up", *x) for x in up], "proteins_down": [item("protein_down", *x) for x in down],
            "mutations": [item("somatic_mutation", *x) for x in muts],
            "findings": [{"name": n, "curie": c, "id": patient_edge_id(pid, "has_phenotype", c)} for n, c in finds],
            "missing": missing}


def brca_trace():
    """A hand-built mock BRCA trace in the contract shape. Tool results are written here, not by agent/mock_tools.py,
    because the mock tools only know the ccRCC mock patient. The ccRCC stage model is not used on breast cancer."""
    pid, pc = "BRCA-MOCK1", patient_curie("BRCA-MOCK1")
    up = patient_edge_id(pid, "protein_up", "NCBIGene:2064")
    pat = {"patient_id": pid, "curie": pc, "clinical_summary": "58-year-old female (mock).",
           "phenotypes": [], "proteins_up": [{"curie": "NCBIGene:2064", "name": "ERBB2", "symbol": "ERBB2", "z": 3.1,
                                              "edge_id": up}],
           "proteins_down": [{"curie": "NCBIGene:2099", "name": "ESR1", "symbol": "ESR1", "z": -1.9,
                              "edge_id": patient_edge_id(pid, "protein_down", "NCBIGene:2099")}]}
    nodes = [{"curie": "NCBIGene:2064", "name": "ERBB2", "type": "gene", "prize": .5},
             {"curie": "MONDO:MOCK_BRCA", "name": "breast carcinoma", "type": "disease", "prize": .5},
             {"curie": "DRUGBANK:MOCK_TRAS", "name": "Trastuzumab", "type": "drug", "prize": .5},
             {"curie": "DRUGBANK:MOCK_LAPA", "name": "Lapatinib", "type": "drug", "prize": .5},
             {"curie": "REACT:MOCK_ERBB2", "name": "ERBB2 signaling", "type": "pathway", "prize": .5}]
    edges = [{"edge_id": "E900201", "src_curie": "NCBIGene:2064", "rel": "pathway_protein", "dst_curie": "REACT:MOCK_ERBB2"},
             {"edge_id": "E900204", "src_curie": "NCBIGene:2064", "rel": "disease_protein", "dst_curie": "MONDO:MOCK_BRCA"},
             {"edge_id": "E900205", "src_curie": "DRUGBANK:MOCK_TRAS", "rel": "drug_protein", "dst_curie": "NCBIGene:2064"},
             {"edge_id": "E900206", "src_curie": "DRUGBANK:MOCK_LAPA", "rel": "drug_protein", "dst_curie": "NCBIGene:2064"}]
    for e in edges:
        e["origin"] = "mock"
    sub = {"nodes": nodes, "edges": edges, "edge_ids": [e["edge_id"] for e in edges], "triples_text": "",
           "meta": {"retriever": "gnn_pcst", "variant": "full", "budget": None, "n_candidates": 9, "mock": True}}
    rank = {"patient_id": pid, "node_type": "drug", "items": [
        {"curie": "DRUGBANK:MOCK_TRAS", "name": "Trastuzumab", "score": .9, "rank": 1,
         "evidence_id": f"rank:{pid}:drug:DRUGBANK:MOCK_TRAS"},
        {"curie": "DRUGBANK:MOCK_LAPA", "name": "Lapatinib", "score": .7, "rank": 2,
         "evidence_id": f"rank:{pid}:drug:DRUGBANK:MOCK_LAPA"}]}
    q = "What drives this breast tumor, and which targets does it expose?"
    head = (f"This tumor looks ERBB2-driven: ERBB2 protein is strongly up [{up}], and ERBB2 sits in the ERBB2 "
            "signaling pathway in the graph [E900201]. Two drugs in the graph act on ERBB2: trastuzumab [E900205] and "
            "lapatinib [E900206].")
    removed = "This patient will respond to trastuzumab."
    tail = "A drug-target edge is a reason to test, not proof of benefit."
    claims = [{"claim_id": "c1", "text": "ERBB2 protein is strongly up in this tumor.", "support": {"edge_ids": [up], "pmids": []}, "status": "supported"},
              {"claim_id": "c2", "text": "ERBB2 is part of ERBB2 signaling.", "support": {"edge_ids": ["E900201"], "pmids": []}, "status": "supported"},
              {"claim_id": "c3", "text": "Trastuzumab targets ERBB2.", "support": {"edge_ids": ["E900205"], "pmids": []}, "status": "supported"},
              {"claim_id": "c4", "text": "Lapatinib targets ERBB2.", "support": {"edge_ids": ["E900206"], "pmids": []}, "status": "supported"},
              {"claim_id": "c5", "text": removed, "quote": removed, "support": {"edge_ids": [], "pmids": []}, "status": "removed",
               "reason": "A drug-target edge shows trastuzumab acts on ERBB2. Nothing the tools returned measures response in this patient."}]
    calls = [("get_patient", {"patient_id": pid}, pat, 40), ("rank_for_patient", {"patient_id": pid, "node_type": "drug"}, rank, 150),
             ("retrieve_subgraph", {"question": q, "seeds": [pc]}, sub, 1100)]
    ev = [x["edge_id"] for x in pat["proteins_up"] + pat["proteins_down"]] + sub["edge_ids"] + [x["evidence_id"] for x in rank["items"]]
    return {"contract_version": CONTRACT_VERSION, "run_id": "mock_curated", "qid": "B01", "mode": "agent", "cancer": "brca",
            **MOCK, "patient_id": pid, "question": q, "model": "mock", "variant": "full",
            "tool_calls": [{"name": n, "args": a, "result": r, "ms": ms} for n, a, r, ms in calls],
            "evidence": {"edge_ids": ev, "pmids": []}, "draft": f"{head} {removed} {tail}", "claims": claims,
            "answer": f"{head} {tail}",
            "verifier": {"n_claims": 5, "n_supported": 4, "n_hypothesis": 0, "n_removed": 1,
                         "non_claims": [{"text": tail, "reason": "caveat"}], "n_non_claims": 1},
            "notes": ["MOCK DATA: curated demo trace"]}


def main():
    for cancer, cl in (("ccrcc", ccrcc()), ("brca", brca())):
        d = OUT / cancer / "briefs"
        d.mkdir(parents=True, exist_ok=True)
        (OUT / cancer / "clusters.json").write_text(json.dumps(cl, indent=1))
    b1 = brief("ccrcc", "C3L-MOCK1", "62-year-old male; history of hypertension; anemia at presentation.", 1,
               [("VEGFA", "NCBIGene:7422", 3.4), ("ENO1", "NCBIGene:2023", 2.9)], [("VHL", "NCBIGene:7428", -2.7)],
               [("VHL", "NCBIGene:7428")], [("Hypertension", "HP:0000822"), ("Anemia", "HP:0001903")],
               ["No treatment history or blood-pressure timeline.", "Mutation calls are somatic only; no germline data."])
    b2 = brief("brca", "BRCA-MOCK1", "58-year-old female (mock).", 1, [("ERBB2", "NCBIGene:2064", 3.1)],
               [("ESR1", "NCBIGene:2099", -1.9)], [], [],
               ["No matched normal sample; z-scores use the cohort reference.", "No mutation calls loaded for this cohort yet."])
    (OUT / "ccrcc" / "briefs" / "C3L-MOCK1.json").write_text(json.dumps(b1, indent=1))
    (OUT / "brca" / "briefs" / "BRCA-MOCK1.json").write_text(json.dumps(b2, indent=1))
    (TRACES / "BRCA-MOCK1__B01.json").write_text(json.dumps(brca_trace(), indent=1, ensure_ascii=False))
    print("wrote", sorted(str(p.relative_to(OUT.parent)) for p in OUT.rglob("*.json")), "+ mock_traces/BRCA-MOCK1__B01.json")


if __name__ == "__main__":
    main()
