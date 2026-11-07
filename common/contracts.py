"""Shared contracts: paths, constants and helpers. See CONTRACTS.md.

    from common.contracts import P, GRAPH_VARIANTS, edge_id, patient_neighbours

Sections 1-4 of CONTRACTS.md mirror what graph/build_kg.py and models/gnn_a.py already write;
if those scripts change their outputs, update this file in the same PR.
"""
from __future__ import annotations

import re
from pathlib import Path

CONTRACT_VERSION = "1"
N_FOLDS = 5

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[1]


class P:
    raw = ROOT / "data" / "raw"
    processed = ROOT / "data" / "processed"
    cache = ROOT / "cache"

    # built by graph/build_kg.py (WS1)
    nodes = processed / "nodes.parquet"
    edges = processed / "edges.parquet"
    evidence = processed / "evidence.parquet"
    e1_split = processed / "e1_split.json"
    graph_full = processed / "graph_full.pt"
    graph_slim = processed / "graph_slim.pt"
    graph_slim_e1 = processed / "graph_slim_e1.pt"
    ws1_report = processed / "ws1_report.json"

    # built by models/text_features.py and models/gnn_a.py
    feat_text = processed / "feat_text.pt"

    @staticmethod
    def emb(tag: str = "main") -> Path:
        return P.processed / f"emb_{tag}.pt"

    # WS2 (to build)
    patients_dir = processed / "patients"
    patients = patients_dir / "patients.parquet"
    patient_phenotypes = patients_dir / "patient_phenotypes.parquet"
    patient_proteins = patients_dir / "patient_proteins.parquet"
    patient_mutations = patients_dir / "patient_mutations.parquet"   # somatic (tumor) mutations, CPTAC
    patient_drugs = patients_dir / "patient_drugs.parquet"           # baseline medications -> DrugBank
    labels = patients_dir / "labels.parquet"
    folds = patients_dir / "folds.parquet"

    # GNN (to build)
    predictions = processed / "gnn_a" / "predictions.parquet"

    # eval
    questions = ROOT / "eval" / "questions.jsonl"
    traces = ROOT / "eval" / "traces"
    results = ROOT / "eval" / "results"
    demo_cache = cache / "demo"


# ---------------------------------------------------------------- nodes / edges (as built)
NODE_TYPES = ("gene", "disease", "phenotype", "drug", "biological_process", "molecular_function",
              "cellular_component", "pathway", "anatomy", "exposure", "mechanism", "cell", "treatment")
RANKABLE_TYPES = ("gene", "pathway", "drug")
EDGE_ORIGINS = ("primekg", "dismech")
EDGE_FLAGS = ("hub", "e1_holdout", "e4_dismech", "e4_full")

# variant -> flags whose edges are dropped (no_dismech drops by origin instead)
GRAPH_VARIANTS: dict[str, tuple[str, ...]] = {
    "full": (),
    "slim": ("hub",),
    "e1": ("hub", "e1_holdout"),
    "e4": ("hub", "e4_full"),
    "e4_dismech": ("hub", "e4_dismech"),
    "no_dismech": ("hub",),
    "bifo": ("hub", "bifo_dropped"),
    "bifo_strict": ("hub", "bifo_dropped", "bifo_observational"),
    "bifo_flow": ("hub", "bifo_dropped", "bifo_observational", "bifo_against_flow"),
    "e1_bifo": ("hub", "e1_holdout", "bifo_dropped"),
    "e1_bifo_strict": ("hub", "e1_holdout", "bifo_dropped", "bifo_observational"),
}
RETRIEVERS = ("bfs", "text_pcst", "gnn_pcst")
DEFAULT_BUDGET = 50


def edge_id(edge_row: int) -> str:
    """Citable ID of a row in edges.parquet."""
    return f"E{int(edge_row)}"


def edge_row(eid: str) -> int:
    if not eid.startswith("E"):
        raise ValueError(f"not a graph edge_id: {eid!r}")
    return int(eid[1:])


def variant_mask(edges, variant: str):
    """Boolean Series over edges.parquet rows that are KEPT in `variant`."""
    if variant not in GRAPH_VARIANTS:
        raise ValueError(f"unknown variant {variant!r}; expected one of {list(GRAPH_VARIANTS)}")
    import pandas as pd
    flags = list(GRAPH_VARIANTS[variant])
    keep = ~edges[flags].any(axis=1) if flags else pd.Series(True, index=edges.index)
    if variant == "no_dismech":
        keep &= edges["origin"] != "dismech"
    return keep


# ---------------------------------------------------------------- ccRCC
CCRCC_ID = "MONDO:0005005"
CCRCC_GENES = {"VHL": "NCBIGene:7428", "EPAS1": "NCBIGene:2034", "VEGFA": "NCBIGene:7422",
               "PBRM1": "NCBIGene:55193", "BAP1": "NCBIGene:8314"}

# ---------------------------------------------------------------- patients (WS2)
PATIENT_RELATIONS = ("has_phenotype", "protein_up", "protein_down")
LABEL_COLUMNS = ("y_stage", "y_grade", "y_recur", "os_days", "os_event")
PRIMARY_LABEL = "y_stage"   # 65 I-II / 45 III-IV (Amy's CPTAC check, Nov 7)

# Column names that must never appear in patient feature/edge tables.
LEAKY_COLUMN_RE = re.compile(
    r"(stage|grade|tnm|surviv|vital|recur|follow|death|deceased|days_to|(^|_)(os|pfs|rfs|dfs)(_|$))",
    re.IGNORECASE,
)
# medical_condition terms that describe the tumour or its spread -> leak stage. Lowercase substring match.
# WS2: extend as you find more.
LEAKY_PHENOTYPE_TERMS = ("renal mass", "lytic lesion of bone", "pulmonary nodule")


def patient_curie(patient_id: str) -> str:
    return f"PATIENT:{patient_id}"


def pred_id(patient_id: str, model: str = "gnn") -> str:
    """Citable ID for a predict_patient risk (a model output, not a graph fact)."""
    return f"pred:{patient_id}:{model}"


def contrib_id(patient_id: str, curie: str) -> str:
    """Citable ID for one top_contributing neighbour behind a patient's gnn risk."""
    return f"contrib:{patient_id}:{curie}"


def rank_id(patient_id: str, node_type: str, curie: str) -> str:
    """Citable ID for one rank_for_patient item."""
    return f"rank:{patient_id}:{node_type}:{curie}"


MODEL_OUTPUT_PREFIXES = ("pred:", "contrib:", "rank:")


def patient_edge_id(patient_id: str, relation: str, curie: str) -> str:
    return f"pt:{patient_id}:{relation}:{curie}"


def patient_neighbours(patient_id: str, phenotypes=None, proteins=None) -> tuple[list[str], list[float]]:
    """(neighbour CURIEs, weights) to pass to GnnA: phenotypes weight 1.0, proteins weight z (signed)."""
    import pandas as pd
    ph = phenotypes if phenotypes is not None else pd.read_parquet(P.patient_phenotypes)
    pr = proteins if proteins is not None else pd.read_parquet(P.patient_proteins)
    ph = ph[ph.patient_id == patient_id]
    pr = pr[pr.patient_id == patient_id]
    return ph.curie.tolist() + pr.curie.tolist(), [1.0] * len(ph) + pr.z.astype(float).tolist()


# ---------------------------------------------------------------- models / LLM
TEXT_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"   # what feat_text.pt uses today; GNN-B must match
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
LLM_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"   # Agent owner: probed Nov 7 with python -m agent.models --probe
GNN_A_TAGS = ("main", "no_dismech")


# ---------------------------------------------------------------- tool helpers
def triple_line(eid: str, src_name: str, src_type: str, rel: str, dst_name: str, dst_type: str) -> str:
    """The exact line format of SubgraphResult.triples_text."""
    return f"[{eid}] {src_name} ({src_type}) --{rel}--> {dst_name} ({dst_type})"


def error(msg: str, **extra) -> dict:
    """Tools never raise; they return this."""
    return {"error": msg, **extra}
