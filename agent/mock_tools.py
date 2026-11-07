"""Fake tools with the contract's return shapes (CONTRACTS.md section 7), for building the agent and the demo
before the real backends exist.

    BIOHACK_MOCK=1 python -m agent.mock_tools      # print one call to every tool

Gene/phenotype/disease CURIEs are real. Edge IDs (E9000xx), mechanism IDs and PMIDs (PMID:0000000x) are FAKE on
purpose, so they can never be mistaken for real citations in a demo. agent/tools.py must expose the same
TOOLS and dispatch().
"""
from __future__ import annotations

import json

from common.contracts import (
    CCRCC_ID, CONTRACT_VERSION, DEFAULT_BUDGET, PRIMARY_LABEL, error, patient_curie, patient_edge_id, triple_line,
)

PID = "C3L-MOCK1"
PATIENTS = [PID, "C3L-MOCK2"]
_N = {  # curie -> (name, type)
    CCRCC_ID: ("clear cell renal carcinoma", "disease"),
    "NCBIGene:7428": ("VHL", "gene"),
    "NCBIGene:2034": ("EPAS1", "gene"),
    "NCBIGene:7422": ("VEGFA", "gene"),
    "NCBIGene:3791": ("KDR", "gene"),
    "NCBIGene:2023": ("ENO1", "gene"),
    "HP:0000822": ("Hypertension", "phenotype"),
    "HP:0000790": ("Hematuria", "phenotype"),
    "HP:0001903": ("Anemia", "phenotype"),
    "DRUGBANK:DB01268": ("Sunitinib", "drug"),
    "DRUGBANK:MOCK_BELZ": ("Belzutifan", "drug"),
    "REACT:MOCK_HIF": ("Cellular response to hypoxia", "pathway"),
    patient_curie(PID): (f"patient {PID}", "patient"),
}
_E = [  # edge_id, src, rel, dst, origin
    ("E900001", "NCBIGene:7428", "disease_protein", CCRCC_ID, "primekg"),
    ("E900002", "NCBIGene:2034", "dm_contributes_to", CCRCC_ID, "dismech"),
    ("E900003", "NCBIGene:7428", "protein_protein", "NCBIGene:2034", "primekg"),
    ("E900004", "NCBIGene:2034", "protein_protein", "NCBIGene:7422", "primekg"),
    ("E900005", "NCBIGene:7422", "protein_protein", "NCBIGene:3791", "primekg"),
    ("E900006", "DRUGBANK:DB01268", "drug_protein", "NCBIGene:3791", "primekg"),
    ("E900007", "DRUGBANK:MOCK_BELZ", "drug_protein", "NCBIGene:2034", "primekg"),
    ("E900008", "NCBIGene:2034", "pathway_protein", "REACT:MOCK_HIF", "primekg"),
]


def _ref(c, **kw):
    name, t = _N.get(c, (c, "unknown"))
    return {"curie": c, "name": name, "type": t, **kw}


def _pt(rel, c, **kw):
    return {"curie": c, "name": _N[c][0], "edge_id": patient_edge_id(PID, rel, c), **kw}


# ------------------------------------------------------------------ tools
def resolve_terms(texts: list[str]) -> dict:
    out = []
    for t in texts:
        m = [{"curie": c, "name": n, "type": ty, "score": 1.0}
             for c, (n, ty) in _N.items() if ty != "patient" and (t.lower() in n.lower() or n.lower() in t.lower())]
        out.append({"text": t, "matches": m[:5]})
    return {"results": out}


def get_patient(patient_id: str) -> dict:
    if patient_id not in PATIENTS:
        return error(f"unknown patient_id {patient_id!r}", known=PATIENTS)
    return {
        "patient_id": patient_id,
        "curie": patient_curie(patient_id),
        "clinical_summary": "62-year-old male; history of hypertension; anemia at presentation.",
        "phenotypes": [_pt("has_phenotype", "HP:0000822"), _pt("has_phenotype", "HP:0001903")],
        "proteins_up": [_pt("protein_up", "NCBIGene:7422", symbol="VEGFA", z=3.4),
                        _pt("protein_up", "NCBIGene:2023", symbol="ENO1", z=2.9)],
        "proteins_down": [_pt("protein_down", "NCBIGene:7428", symbol="VHL", z=-2.7)],
    }


def predict_patient(patient_id: str) -> dict:
    if patient_id not in PATIENTS:
        return error(f"unknown patient_id {patient_id!r}", known=PATIENTS)
    return {"patient_id": patient_id, "label": PRIMARY_LABEL, "risk": 0.78, "model": "gnn", "fold": 2,
            "evidence_id": f"pred:{patient_id}:gnn",
            "top_contributing": [_ref(c, score=s, evidence_id=f"contrib:{patient_id}:{c}")
                                 for c, s in (("NCBIGene:7422", 0.41), ("HP:0000822", 0.22))]}


def rank_for_patient(patient_id: str, node_type: str, k: int = 10) -> dict:
    if patient_id not in PATIENTS:
        return error(f"unknown patient_id {patient_id!r}", known=PATIENTS)
    pool = [c for c, (_, t) in _N.items() if t == node_type]
    if node_type not in ("gene", "pathway", "drug"):
        return error(f"node_type must be gene, pathway or drug; got {node_type!r}")
    items = [{"curie": c, "name": _N[c][0], "score": round(0.9 - 0.1 * i, 2), "rank": i + 1,
              "evidence_id": f"rank:{patient_id}:{node_type}:{c}"}
             for i, c in enumerate(pool[:k])]
    return {"patient_id": patient_id, "node_type": node_type, "items": items}


def retrieve_subgraph(question: str, seeds: list[str], budget: int = DEFAULT_BUDGET,
                      retriever: str = "gnn_pcst", variant: str = "full") -> dict:
    edges = _E[:budget]
    curies = sorted({e[1] for e in edges} | {e[3] for e in edges})
    return {
        "nodes": [_ref(c, prize=1.0 if c in seeds else 0.5) for c in curies],
        "edges": [{"edge_id": e, "src_curie": s, "rel": r, "dst_curie": d, "origin": o} for e, s, r, d, o in edges],
        "edge_ids": [e[0] for e in edges],
        "triples_text": "\n".join(triple_line(e, _N[s][0], _N[s][1], r, _N[d][0], _N[d][1]) for e, s, r, d, _ in edges),
        "meta": {"retriever": retriever, "variant": variant, "budget": budget, "n_candidates": len(_N),
                 "alpha": 0.5, "beta": 0.5, "mock": True},
    }


def get_mechanism(disease_curie: str) -> dict:
    if disease_curie != CCRCC_ID:
        return error(f"no mechanism for {disease_curie!r} in mock (try {CCRCC_ID})")
    steps = [("dismech:MOCK-1", "VHL loss of function"), ("dismech:MOCK-2", "HIF-2alpha stabilization"),
             ("dismech:MOCK-3", "VEGF-driven angiogenesis")]
    return {
        "disease": CCRCC_ID, "name": _N[CCRCC_ID][0],
        "chains": [{"chain_id": "chain-1", "steps": [
            {"mechanism": m, "label": lab, "edge_id": f"E90010{i}", "pmids": [f"PMID:0000000{i}"],
             "quotes": [f"(mock quote for step {i})"]} for i, (m, lab) in enumerate(steps, 1)]}],
        "genes": ["NCBIGene:7428", "NCBIGene:2034", "NCBIGene:7422"],
        "drugs": ["DRUGBANK:DB01268", "DRUGBANK:MOCK_BELZ"],
    }


# ------------------------------------------------------------------ registry
def _fn(name, desc, props, required):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": required}}}


_S = {"type": "string"}
TOOLS = [
    _fn("resolve_terms", "Map free-text biomedical terms to graph CURIEs.",
        {"texts": {"type": "array", "items": _S}}, ["texts"]),
    _fn("get_patient", "A patient's phenotypes, top up/down proteins (each with a citable edge_id) and clinical summary.",
        {"patient_id": _S}, ["patient_id"]),
    _fn("predict_patient", "GNN out-of-fold risk estimate for the patient, with the nodes that contributed most.",
        {"patient_id": _S}, ["patient_id"]),
    _fn("rank_for_patient", "GNN-ranked genes, pathways or drugs for a patient.",
        {"patient_id": _S, "node_type": {"type": "string", "enum": ["gene", "pathway", "drug"]},
         "k": {"type": "integer"}}, ["patient_id", "node_type"]),
    _fn("retrieve_subgraph", "Compact evidence subgraph (<= budget nodes) for a question. Cite its edge_ids.",
        {"question": _S, "seeds": {"type": "array", "items": _S}, "budget": {"type": "integer"}},
        ["question", "seeds"]),
    _fn("get_mechanism", "Curated dismech causal chains for a disease, with PMIDs.",
        {"disease_curie": _S}, ["disease_curie"]),
]
_IMPL = {"resolve_terms": resolve_terms, "get_patient": get_patient, "predict_patient": predict_patient,
         "rank_for_patient": rank_for_patient, "retrieve_subgraph": retrieve_subgraph, "get_mechanism": get_mechanism}


def dispatch(name: str, args: dict) -> dict:
    """Never raises: unknown tools and bad arguments come back as {"error": ...}."""
    fn = _IMPL.get(name)
    if fn is None:
        return error(f"unknown tool {name!r}", known=list(_IMPL))
    try:
        return fn(**args)
    except TypeError as e:
        return error(f"bad arguments for {name}: {e}")
    except Exception as e:  # noqa: BLE001 - tools must not raise
        return error(f"{type(e).__name__}: {e}")


def example_trace() -> dict:
    """A complete trace in the contract shape (CONTRACTS.md section 8); the demo can render this today."""
    pat = get_patient(PID)
    sub = retrieve_subgraph("why high risk", [patient_curie(PID)])
    mech = get_mechanism(CCRCC_ID)
    pt_edges = [x["edge_id"] for k in ("phenotypes", "proteins_up", "proteins_down") for x in pat[k]]
    pmids = [p for c in mech["chains"] for s in c["steps"] for p in s["pmids"]]
    vegfa_up = patient_edge_id(PID, "protein_up", "NCBIGene:7422")
    return {
        "contract_version": CONTRACT_VERSION, "run_id": "mock", "qid": "P01", "mode": "agent",
        "patient_id": PID, "question": "Why is this patient high-risk, and what would you target?", "model": "mock",
        "tool_calls": [{"name": "get_patient", "args": {"patient_id": PID}, "result": pat, "ms": 0},
                       {"name": "retrieve_subgraph", "args": {}, "result": sub, "ms": 0},
                       {"name": "get_mechanism", "args": {"disease_curie": CCRCC_ID}, "result": mech, "ms": 0}],
        "evidence": {"edge_ids": pt_edges + sub["edge_ids"], "pmids": pmids},
        "draft": "...",
        "claims": [
            {"claim_id": "c1", "text": "The patient's tumour overexpresses VEGFA.",
             "support": {"edge_ids": [vegfa_up], "pmids": []}, "status": "supported"},
            {"claim_id": "c2", "text": "VHL loss stabilizes HIF-2alpha.",
             "support": {"edge_ids": [], "pmids": ["PMID:00000002"]}, "status": "supported"},
            {"claim_id": "c3", "text": "Sunitinib targets KDR, downstream of VEGFA.",
             "support": {"edge_ids": ["E900006"], "pmids": []}, "status": "supported"},
            {"claim_id": "c4", "text": "ENO1 upregulation may reflect a glycolytic shift.",
             "support": {"edge_ids": [], "pmids": []}, "status": "hypothesis"},
        ],
        "answer": "...",
        "verifier": {"n_claims": 4, "n_supported": 3, "n_hypothesis": 1, "n_removed": 0},
    }


if __name__ == "__main__":
    demo_args = {"resolve_terms": {"texts": ["hematuria"]}, "get_patient": {"patient_id": PID},
                 "predict_patient": {"patient_id": PID}, "rank_for_patient": {"patient_id": PID, "node_type": "drug"},
                 "retrieve_subgraph": {"question": "q", "seeds": [patient_curie(PID)]},
                 "get_mechanism": {"disease_curie": CCRCC_ID}}
    for t in TOOLS:
        name = t["function"]["name"]
        print(f"== {name}\n{json.dumps(dispatch(name, demo_args[name]), indent=1)[:500]}\n")
