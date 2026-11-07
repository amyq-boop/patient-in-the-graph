# Patient-in-the-Graph
Nucleate Pittsburgh BioHack 2026 · Bioinformatics track (NVIDIA)

An LLM agent that uses GNNs to reason over a phenotype-genotype knowledge graph (PrimeKG + dismech), one real ccRCC patient (CPTAC) at a time.

## Setup
    conda create -n biohack python=3.11
    conda activate biohack
    pip install -r requirements.txt
    cp .env.example .env          # then add your NVIDIA key
    ./scripts/download_data.sh    # needs wget (brew install wget)

## Layout
- `data/` raw + processed data (not committed, use the download script)
- `cache/` API responses + demo traces (not committed)
- `scripts/` data download
- `notebooks/` exploration
- `graph/` KG build, ID normalization, patient layer
- `models/` baselines, GNN-A, GNN-B retriever
- `agent/` tools, agent loop, verifier
- `eval/` E1-E4 + eval questions
- `app/` Streamlit demo

Large files like `graph.pt` go in the shared Drive, not git.

## Data notes (CPTAC ccRCC, checked Nov 7)
- 110 tumors, 84 matched normals, ~12k proteins (umich)
- Labels: stage I-II vs III-IV (65/45), 21 deaths, 22 recurrences
- Phenotypes come from `medical_condition` (pipe-separated free text); 15 patients have none, median 3 per patient
- Filter leaky terms (e.g. renal mass, lytic lesion of bone, pulmonary nodules)
- follow-up and medical_history tables are empty; use clinical

## Roles
TBD
EOF