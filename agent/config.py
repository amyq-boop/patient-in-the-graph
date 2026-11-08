"""Agent settings. Shared paths and constants come from common/contracts.py; the rest can be
overridden by environment variables or a .env file in the repo root."""
import os

from common.contracts import CCRCC_ID, CONTRACT_VERSION, DEFAULT_BUDGET, GRAPH_VARIANTS, LLM_MODEL, NIM_BASE_URL, P, PRIMARY_LABEL, ROOT  # noqa: F401


def _load_dotenv():
    p = ROOT / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

PROCESSED = P.processed
CACHE = P.cache
TRACES = P.traces
DEMO_CACHE = P.demo_cache
RESULTS = P.results
MECHANISMS = P.processed / "mechanisms.jsonl"          # optional precomputed get_mechanism output
CONTRIBUTIONS = P.processed / "gnn_a" / "contributions.parquet"  # optional; columns patient_id, curie, score, rank

# --- LLM (NVIDIA NIM, OpenAI-compatible) ---
NIM_BASE_URL = os.environ.get("NIM_BASE_URL", NIM_BASE_URL)
NIM_MODEL = os.environ.get("NIM_MODEL", LLM_MODEL)
NIM_API_KEY = os.environ.get("NVIDIA_API_KEY", "")
TEMPERATURE = float(os.environ.get("NIM_TEMPERATURE", "0"))

# --- Agent behaviour ---
# BIOHACK_MOCK: "1" = all mocks, "0" = real only, "auto" = real where the backing file exists, else mock
MOCK_MODE = os.environ.get("BIOHACK_MOCK", "auto").lower()
# Graph variant for every retrieve_subgraph call: full (answers/demo), e4 or e4_dismech (E4 live demo), ...
VARIANT = os.environ.get("BIOHACK_VARIANT", "full")
if VARIANT not in GRAPH_VARIANTS:
    raise ValueError(f"BIOHACK_VARIANT={VARIANT!r}; expected one of {list(GRAPH_VARIANTS)}")
GNN_A_TAG = "no_dismech" if VARIANT == "no_dismech" else "main"

# Node types the retriever gives prizes to for agent answers. The retriever's own default (genes + GO terms) is
# tuned for E1 and returned no drug or phenotype edges for patient questions, so the agent widens it.
# BIOHACK_TARGET_TYPES=e1 restores the E1 default; or give a comma list of node types.
# What rank_for_patient ranks from. "tumor" = the patient's dysregulated proteins only: with the clinical
# phenotypes included, comorbidities (hypertension, obesity) dominated and the top "targets" were leptin and heparin.
# "all" = phenotypes + proteins (contracts.patient_neighbours, as GNN-A E2 uses).
RANK_BASIS = os.environ.get("BIOHACK_RANK_BASIS", "tumor")
if RANK_BASIS not in ("tumor", "all"):
    raise ValueError(f"BIOHACK_RANK_BASIS={RANK_BASIS!r}; expected tumor or all")

_TT = os.environ.get("BIOHACK_TARGET_TYPES", "agent")
TARGET_TYPES = (None if _TT == "e1" else
                ("gene", "biological_process", "molecular_function", "cellular_component",
                 "drug", "phenotype", "pathway") if _TT == "agent" else  # not "mechanism": comorbidity seeds
                # (obesity) pulled in mechanisms of unrelated diseases; ccRCC's own come from get_mechanism
                tuple(t.strip() for t in _TT.split(",") if t.strip()))
MAX_TOOL_CALLS = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "12"))
MAX_RETRIEVAL_RETRIES = 3
# What the LLM sees of one retrieve_subgraph result: edges ranked by relevance, as many as fit, then a note
# saying how many were left out (the old 6000-char cut sliced the text mid-line and said nothing).
RETRIEVE_VIEW_CHARS = int(os.environ.get("AGENT_RETRIEVE_VIEW_CHARS", "25000"))  # uncapped PCST: ~150-185 nodes
# Retries add new seeds and grow the budget by a fixed step (was x1.5 each time: 50 -> 75 -> 112 -> 168).
RETRY_BUDGET_STEP = int(os.environ.get("AGENT_RETRY_BUDGET_STEP", "25"))
RETRY_BUDGET_MAX = int(os.environ.get("AGENT_RETRY_BUDGET_MAX", "100"))
# "fixed" (default): pass the budget. "auto": let the retriever size the subgraph itself, if it supports that
# (models/retriever.py sets ADAPTIVE_BUDGET = True when budget=None means self-sizing); otherwise falls back.
BUDGET_MODE = os.environ.get("BIOHACK_BUDGET", "auto").lower()  # auto = PCST sizes it (K prized terminals)
# Seed -> retrieved-node edges the retriever adds (no extra nodes) so the evidence visibly links back to the patient
# (Retriever.anchor_edges). 0 turns it off.
ANCHOR_EDGES = int(os.environ.get("AGENT_ANCHOR", "20"))
# Rewrite the answer after retrieval retries only if they found the protein -> phenotype link (1 = always rewrite).
REVISE_ALWAYS = os.environ.get("AGENT_REVISE_ALWAYS", "0") == "1"
TOOL_RESULT_CHARS = int(os.environ.get("AGENT_TOOL_RESULT_CHARS", "6000"))  # what the LLM sees per tool result
