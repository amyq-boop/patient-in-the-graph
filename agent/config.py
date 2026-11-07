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
_TT = os.environ.get("BIOHACK_TARGET_TYPES", "agent")
TARGET_TYPES = (None if _TT == "e1" else
                ("gene", "biological_process", "molecular_function", "cellular_component",
                 "drug", "phenotype", "pathway", "mechanism") if _TT == "agent" else
                tuple(t.strip() for t in _TT.split(",") if t.strip()))
MAX_TOOL_CALLS = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "12"))
MAX_RETRIEVAL_RETRIES = 3
TOOL_RESULT_CHARS = int(os.environ.get("AGENT_TOOL_RESULT_CHARS", "6000"))  # what the LLM sees per tool result
