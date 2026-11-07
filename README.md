# Patient-in-the-Graph
Nucleate Pittsburgh BioHack 2026 · Bioinformatics track (NVIDIA)

An LLM agent that uses GNNs to reason over a phenotype-genotype knowledge graph (PrimeKG + dismech), one real ccRCC patient (CPTAC) at a time.

> **Before you write any output file or tool, read [`CONTRACTS.md`](CONTRACTS.md)**: section 0 plus the sections for what you produce and what you consume. It fixes IDs, file columns, tool return shapes and the trace format so the workstreams fit together.

## Setup
    conda create -n biohack python=3.11
    conda activate biohack
    pip install -r requirements.txt
    cp .env.example .env          # then add your NVIDIA key
    ./scripts/download_data.sh    # needs wget (brew install wget)

    python -m common.validate all              # check every output that exists so far
    BIOHACK_MOCK=1 python -m agent.mock_tools  # print every agent tool's return shape

Run code from the repo root as `python -m package.module` so `common` imports resolve.

## Layout
- `common/` shared contracts: `contracts.py` (paths, constants, graph variants, ID helpers), `validate.py`
- `data/` raw + processed data (not committed, use the download script)
- `cache/` API responses (not committed); `cache/demo/` holds cached demo traces and **is** committed
- `scripts/` data download
- `notebooks/` exploration
- `graph/` KG build (`build_kg.py`, done), patient layer
- `models/` GNN-A (`gnn_a.py` + `gnn_a_api.py`, done), baselines, GNN-B retriever
- `agent/` tools, agent loop, verifier; `mock_tools.py` is available to build against now
- `eval/` E1-E4 + eval questions (`questions.example.jsonl` shows the format)
- `app/` Streamlit demo

Large files like `graph.pt` go in the shared Drive, not git.

## Data notes (CPTAC ccRCC, checked Nov 7)
- 110 tumors, 84 matched normals, ~12k proteins (umich)
- Labels: stage I-II vs III-IV (65/45), 21 deaths, 22 recurrences
- Phenotypes come from `medical_condition` (pipe-separated free text); 15 patients have none, median 3 per patient
- Filter leaky terms (e.g. renal mass, lytic lesion of bone, pulmonary nodules); the list lives in `common/contracts.py` as `LEAKY_PHENOTYPE_TERMS`
- follow-up and medical_history tables are empty; use clinical

## Roles
| Workstream | Owner | Produces | Needs first |
|---|---|---|---|
| **Graph** (WS1) | Charis (done) | `nodes/edges/evidence.parquet`, `graph_*.pt`, `e1_split.json`, mask flags | nothing |
| **Patients** (WS2) | _name_ | patient tables, `labels.parquet`, `folds.parquet` | `nodes.parquet` (for CURIEs) |
| **GNN** (WS3 + WS4) | Charis (GNN-A done) / _name_ | `emb_<tag>.pt` + `GnnA` API (done), `predictions.parquet`, `models/retriever.py`, `e1.csv`, `e2.csv` | patient tables + folds (outcome head), graph (retriever) |
| **Agent** (WS5) | _name_ | `agent/tools.py`, agent loop, verifier, traces, `e3.csv`, `e4.json` | nothing (build on `agent/mock_tools.py`) |
| **Demo / Pitch** (WS6) | _name_ | `eval/questions.jsonl`, `app/streamlit_app.py`, slides, `cache/demo/` traces | nothing (build on `example_trace()` + fake CSVs) |

Shared constants are set **once** in `common/contracts.py`, and any change is announced in Slack. Already settled: `CCRCC_ID = MONDO:0005005` and `PRIMARY_LABEL = y_stage`; the text-embedding model is whatever `feat_text.pt` used (MiniLM today). Still open: `LLM_MODEL`.

## Timeline (Sat 11/7 to Sun 11/8)
These checkpoints are suggestions; adjust them to fit sleep.

| When | Checkpoint |
|---|---|
| **Sat 1:00 PM** | Everyone has read CONTRACTS.md; roles filled in |
| **Sat 3:00 PM** | WS2 publishes patient tables + `folds.parquet` (frozen after this); eval questions written |
| Sat 4-6 PM | Mentor office hours / pitch practice |
| **Sat 7:00 PM** | **One question runs end to end** (the agent calls at least one real tool, and the trace validates) |
| **Sun 1:00 AM** | **Feature freeze.** Cut in this order: STaRK, Translator, MIMIC, learned edge scorer |
| **Sun 9:00 AM** | E1-E4 numbers final; demo traces cached (3 patients x 5 questions); backup video recorded |
| **Sun 12:00 PM** | **Deliverable submission deadline** |
| Sun 1:15 PM | Pitches |

## Working rules
- **Mocks first.** Agent and Demo don't wait on Graph or GNN; when a real piece is ready, swap it in behind the same function.
- **Run `python -m common.validate <area>` before you announce an output.** A file that fails validation hasn't shipped yet.
- **Contract changes go in the Slack `#contracts` thread** (see the top of CONTRACTS.md).
- **No labels in features or edges.** The validator checks for this, but it can't catch everything.
