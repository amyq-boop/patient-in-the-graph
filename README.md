# GNNome: Patient-in-the-Graph
Nucleate Pittsburgh BioHack 2026 · NVIDIA challenge track · Charis Cui, Kaitlin Moore, Aniket Naik, Amy Qin

**One patient, read through everything biology already knows.** We place a real cancer patient's tumour data inside a
biomedical knowledge graph, let a graph neural network pick the evidence that matters for a question, and have an LLM
agent answer from that evidence only. A verifier then removes any claim whose citation was not actually retrieved, so
every statement that survives points back to a graph edge or a PubMed ID.

## How it works

| Step | What happens |
|---|---|
| 1. Data | PrimeKG (20 biomedical resources), DisMech curated disease mechanisms with PubMed IDs, and one patient's tumour proteins, mutations and clinical findings (CPTAC). Every ID is unified through NCATS Translator. |
| 2. One graph | 160,748 entities and 8.56M directed relationships. The patient joins as a new node linked to the genes and phenotypes in their data, so no retraining is needed per patient. |
| 3. Two GNNs (PyG on NVIDIA GPUs) | **GNN-A** learns the graph's structure by link prediction. **GNN-B** scores nodes for each question and keeps a connected subgraph of 50 nodes or fewer (prize-collecting Steiner tree). |
| 4. Agent (Nemotron on NVIDIA NIM) | Plans, calls 7 graph tools and drafts an answer citing edge IDs and PMIDs. It never sees the full graph, only what the GNN hands it. |
| 5. Verifier (code, not a model) | Keeps a claim only if the ID it cites was returned by a tool in that run. Everything else is removed or labelled a hypothesis. |

## Results

All patient results are CPTAC clear-cell renal cell carcinoma (ccRCC): 110 tumours, 84 with matched normal kidney.
Each number links to the committed file it comes from.

| Test | Result | File |
|---|---|---|
| **E1: does the GNN improve retrieval?** 196 held-out diseases; their direct gene and GO links hidden | GNN-B recovers 27.4% of hidden targets in 50 nodes vs 22.0% for the best non-GNN retriever (text + PageRank PCST); MRR 0.289 vs 0.128; better on 103 diseases, worse on 51 (Wilcoxon p = 7e-5). The lead holds at every size from 25 to 200 nodes: 1.5 to 5 points on genes, 8 to 13 on GO terms. | [`e1_summary.json`](eval/results/e1_summary.json), [`e1_budget_curve.csv`](eval/results/e1_budget_curve.csv) |
| **E2: does putting the patient in the graph help prediction?** Stage III-IV vs I-II, 5 frozen folds, out-of-fold | No. Proteomics alone 0.677 AUROC; our best GNN (with mutations) 0.644; difference within noise (95% CI -0.13 to 0.06). At this cohort size the graph adds explanations, not accuracy. | [`e2_summary.json`](eval/results/e2_summary.json) |
| **E3: are the agent's answers grounded?** 20 questions, same LLM with and without our tools | Agent covers 67% of expected answer points vs 39%; says "can't tell" on 2 of 3 trick questions vs 0 of 3; 270 of 354 claims cite a retrieved edge or PMID (19 more labelled hypothesis, 65 removed). | [`e3_summary.json`](eval/results/e3_summary.json) |
| **E4: does it rediscover ccRCC biology?** Pre-registered, 20 patients, VHL → HIF-2α → VEGF chain | 4.6 of 5 steps recovered with the curated mechanism in the graph; 0.4 of 5 with it hidden (0.95 in a second, independent draw). Answers follow the evidence, not the model's memory. The key proteins (KDR, EPAS1, PDGFRB) never appear among patients' most-changed proteins. | [`e4_cohort_full_rv.json`](eval/results/e4_cohort_full_rv.json), [`e4_cohort_rv.json`](eval/results/e4_cohort_rv.json), [`e4_cohort_b_rv.json`](eval/results/e4_cohort_b_rv.json) |
| **Portability** | The same pipeline ran on CPTAC breast cancer with no retraining. | [`brca_v2_answers.md`](eval/results/brca_v2_answers.md) |

**Rigour:** held-out diseases with cross-fitted retriever settings; E4 cohorts and answer key pre-registered with fixed
seeds before running; outcome labels never used as features or edges (enforced by `common/validate.py`); every
prediction out-of-fold; negative results reported.

**Limits:** small cohort; the clinical phenotypes are mostly comorbidities; CPTAC clinical data stands in for an EHR;
the LLM is non-deterministic, so demo answers replay saved, verified runs. A research hypothesis generator, not a
diagnostic.

## Run the demo

Both demos replay saved, verified runs, so they work offline. Set up first (see *For contributors* below), then:

    streamlit run app/streamlit_app.py     # kidney cancer case study; add ?view=demo to the URL for the compact view

The guided tour of a public patient case (Alexander Young's self-published records, youngcrc.com, CC BY 4.0) is on the
[`ws4/guided-tour`](https://github.com/amyq-boop/patient-in-the-graph/tree/ws4/guided-tour) branch until it is merged:

    git checkout ws4/guided-tour
    python -m app.tour --no-live           # open http://localhost:8600 once the terminal prints the URL

The kidney-cancer model is never applied to the public case; the tour only places his published measurements in the graph.

## Built this weekend vs pre-existing

**Built at the event:** the merged graph (ID normalisation, DisMech merge, mechanism layer, evidence table, graph
variants); the patient layer (protein z-scores, phenotype mapping, leaky-term filter, labels, folds); GNN-A and GNN-B;
the agent, its tools and the claim verifier; all evaluations; both demo apps; the data contracts and validator.

**Used, not built:** [PrimeKG](https://github.com/mims-harvard/PrimeKG),
[DisMech](https://github.com/monarch-initiative/dismech), CPTAC via the [`cptac`](https://github.com/PayneLab/cptac)
package, NCATS Translator Node Normalization and Name Resolution, PyTorch Geometric, `pcst_fast`, the MiniLM
sentence-transformer, NVIDIA NIM (Nemotron), and the G-Retriever / GraphRAG pattern. Each source keeps its own licence
and terms of use.

## For contributors

> Before you write any output file or tool, read [`CONTRACTS.md`](CONTRACTS.md): section 0 plus the sections for what
> you produce and what you consume.

### Setup
    conda create -n biohack python=3.11
    conda activate biohack
    pip install -r requirements.txt
    cp .env.example .env          # then add your NVIDIA key
    ./scripts/download_data.sh    # needs wget (brew install wget)

    python -m common.validate all              # check every output that exists so far
    BIOHACK_MOCK=1 python -m agent.mock_tools  # print every agent tool's return shape

Run code from the repo root as `python -m package.module` so `common` imports resolve.

### Layout
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

### Data notes (CPTAC ccRCC, checked Nov 7)
- 110 tumors, 84 matched normals, ~12k proteins (umich)
- Labels: stage I-II vs III-IV (65/45), 21 deaths, 22 recurrences
- Phenotypes come from `medical_condition` (pipe-separated free text); 15 patients have none, median 3 per patient
- Filter leaky terms (e.g. renal mass, lytic lesion of bone, pulmonary nodules); the list lives in `common/contracts.py` as `LEAKY_PHENOTYPE_TERMS`
- follow-up and medical_history tables are empty; use clinical

### Working rules
- **Mocks first.** Agent and Demo don't wait on Graph or GNN; when a real piece is ready, swap it in behind the same function.
- **Run `python -m common.validate <area>` before you announce an output.** A file that fails validation hasn't shipped yet.
- **Contract changes go in the Slack `#contracts` thread** (see the top of CONTRACTS.md).
- **No labels in features or edges.** The validator checks for this, but it can't catch everything.
