# GNNome: Patient-in-the-Graph
Nucleate Pittsburgh BioHack 2026 · NVIDIA challenge track · Charis Cui, Kaitlin Moore, Aniket Naik, Amy Qin

**One patient, read through everything biology already knows.** We place a real cancer patient's tumour data inside a
biomedical knowledge graph, let a graph neural network pick the evidence that matters for a question, and have an LLM
agent answer from that evidence only. A verifier then removes any claim whose citation was not actually retrieved, so
every statement that survives points back to a graph edge or a PubMed ID.

Built in 48 hours (Nov 6–8, 2026). The repository is now kept as a post-hackathon reference.

## How it works

| Step | What happens |
|---|---|
| 1. Data | PrimeKG (20 biomedical resources), DisMech curated disease mechanisms with PubMed IDs, and one patient's tumour proteins, mutations and clinical findings (CPTAC). Every ID is unified through NCATS Translator. |
| 2. One graph | 160,748 entities and 8.56M directed relationships. The patient joins as a new node linked to the genes and phenotypes in their data, so no retraining is needed per patient. |
| 3. Two GNNs (PyG on NVIDIA GPUs) | **GNN-A** learns the graph's structure by link prediction. **GNN-B** scores nodes for each question and keeps a connected subgraph (prize-collecting Steiner tree). The E1 benchmark uses fixed sizes (50 nodes for the headline, 25 to 200 for the curve). For the agent, the tree sizes itself: about 150 high-scoring nodes plus the nodes that connect them, 172 to 273 nodes in the saved demo runs counting the patient's own nodes. |
| 4. Agent (Nemotron on NVIDIA NIM) | Plans, calls 7 tools (term lookup, patient record, stage prediction, GNN-A rankings, drug targets, subgraph retrieval, curated mechanisms) and drafts an answer citing edge IDs and PMIDs. It never sees the full graph, only a ranked, length-limited view of what GNN-B returns. |
| 5. Verifier (code, not a model) | Keeps a claim only if the ID it cites was returned by a tool in that run. Everything else is removed or labelled a hypothesis. |

## Results

All patient results are CPTAC clear-cell renal cell carcinoma (ccRCC): 110 tumours, 84 with matched normal kidney.
Each number links to the committed file it comes from.

| Test | Result | File |
|---|---|---|
| **E1: does the GNN improve retrieval?** 196 held-out diseases; their direct gene and GO links hidden | GNN-B recovers 27.4% of hidden targets in 50 nodes vs 22.0% for the best non-GNN retriever (text + PageRank PCST); MRR 0.289 vs 0.128; better on 103 diseases, worse on 51 (Wilcoxon p = 7e-5). The lead holds at every size from 25 to 200 nodes: 1.5 to 5 points on genes, 8 to 13 on GO terms. | [`e1_summary.json`](eval/results/e1_summary.json), [`e1_budget_curve.csv`](eval/results/e1_budget_curve.csv) |
| **E2: does putting the patient in the graph help prediction?** Stage III-IV vs I-II, 5 frozen folds, out-of-fold | No. Proteomics alone 0.677 AUROC; our best GNN (with mutations) 0.644; difference within noise (95% CI -0.13 to 0.06). At this cohort size the graph adds explanations, not accuracy. | [`e2_summary.json`](eval/results/e2_summary.json) |
| **E3: are the agent's answers grounded?** 20 questions, same LLM with and without our tools | Agent covers 67% of expected answer points vs 39%; says "can't tell" on 2 of 3 trick questions vs 0 of 3; 270 of 354 claims cite a retrieved edge or PMID (19 more labelled hypothesis, 65 removed). | [`e3_summary.json`](eval/results/e3_summary.json) |
| **E4: does it rediscover ccRCC biology?** Pre-registered, 20 patients, VHL → HIF-2α → VEGF chain | 4.6 of 5 steps recovered with the curated mechanism in the graph; 0.4 of 5 with it hidden (0.95 in a second, independent draw; 0.83 when re-scored with the current checker, 18 of 20 traces valid). Answers follow the evidence, not the model's memory. The key proteins (KDR, EPAS1, PDGFRB) never appear among patients' most-changed proteins. | [`e4_cohort_full_rv.json`](eval/results/e4_cohort_full_rv.json), [`e4_cohort_rv.json`](eval/results/e4_cohort_rv.json), [`e4_cohort_b_rv.json`](eval/results/e4_cohort_b_rv.json), [`e4_hif_weighted.json`](eval/results/e4_hif_weighted.json) |
| **Portability** | The same pipeline ran on CPTAC breast cancer with no retraining. Of 15 pre-registered expectations across 5 patients: 5 met, 4 not met, 1 partial, 5 pending (the drug and agent checks). | [`brca_portability.json`](eval/brca_portability.json), [`brca_v2_answers.md`](eval/results/brca_v2_answers.md) |

**Follow-up analyses (Nov 8):** hand-review sheets for three E4 patients ([`e4_review_sheets/`](eval/results/e4_review_sheets/));
an exploratory re-score that gives partial credit for HIF-1α ([`e4_hif_weighted.json`](eval/results/e4_hif_weighted.json));
and what each principal component tracks, in the GNN patient embeddings and in proteomics alone, for ccRCC and BRCA ([`cohort_pca.json`](eval/results/cohort_pca.json), [`pca/`](eval/results/pca/)).

**Rigour:** held-out diseases with cross-fitted retriever settings; E4 cohorts and answer key pre-registered with fixed
seeds before running; outcome labels never used as features or edges (enforced by `common/validate.py`); every
prediction out-of-fold; negative results reported.

**Limits:** small cohort; the clinical phenotypes are mostly comorbidities; CPTAC clinical data stands in for an EHR;
the LLM is non-deterministic, so demo answers replay saved, verified runs. A research hypothesis generator, not a
diagnostic.

## Run the demos

Both demos replay saved, verified runs. They need no API key, GPU or graph data, only Python 3.11 and a few light
packages. Run every command from the repo root.

    uv venv --python 3.11
    source .venv/bin/activate              # Windows: .venv\Scripts\activate
    uv pip install -r app/requirements.txt

### Kidney cancer case study (Streamlit)

    streamlit run app/streamlit_app.py     # opens http://localhost:8501

- Pick a cancer (Kidney or Breast) and a patient, then press **Analyze patient**. The replay takes about 15 seconds:
  patient record, GNN retrieval, evidence graph, the agent's draft, then the verifier striking unsupported claims.
- Add `?view=demo` to the URL for the compact view used in the pitch, or `?q=P01` … `?q=P05` to open one question.
- The sidebar has three more pages built on Alexander Young's public colorectal cancer case (self-published records,
  youngcrc.com, CC BY 4.0), including a clinician-facing case review ([`docs/CLINICIAN_WORKBENCH.md`](docs/CLINICIAN_WORKBENCH.md)).
  They fetch his records live, so they need internet access.
- To run the agent live instead of replaying, do the full setup under *For contributors*, then set `BIOHACK_LIVE=1`.

### Guided tour (pitch walkthrough)

    python -m app.tour --open                       # http://localhost:8600; 20 to 60 s to start with the full graph
    python -m app.tour --graph executed --no-live   # instant start, fully offline

Six chapters over one network, from the knowledge graph through GNN-A and GNN-B to Alexander Young's case, then a
patient workspace. The kidney-cancer model is never applied to his case; the tour only places his published
measurements in the graph. Flags, keys and the live GNN-B option are in [`app/tour/README.md`](app/tour/README.md).

> **Windows:** the tour checks its bundled snapshot (`app/tour/snapshot/youngcrc/`) byte-for-byte against the release
> manifest. `.gitattributes` stops Git converting those files' line endings, so new clones work. A clone made before
> Nov 10 may still have CRLF copies, which make `--no-live` stop with *"Release mismatch"*. To fix it once, delete the
> `app/tour/snapshot` folder, then run `git checkout -- app/tour/snapshot`.

### Deploy or share

- **Streamlit Community Cloud:** create an app from this repo, branch `main`, main file `app/streamlit_app.py`, Python
  3.11. Community Cloud prefers the `requirements.txt` next to the entrypoint, so it installs the light
  `app/requirements.txt`, not the full pipeline. No secrets are needed for replay.
- **GitHub Codespaces:** `.devcontainer/` installs the root `requirements.txt` and starts the Streamlit app on port 8501
  automatically. The first build is slow because the full requirements include PyTorch.
- **The tour on a server (e.g. Brev):** `python -m app.tour --host 0.0.0.0 --port 8600`, then forward port 8600.

## Built this weekend vs pre-existing

**Built at the event:** the merged graph (ID normalisation, DisMech merge, mechanism layer, evidence table, graph
variants); the patient layer (protein z-scores, phenotype mapping, leaky-term filter, labels, folds); GNN-A and GNN-B;
the agent, its tools and the claim verifier; all evaluations; the Streamlit app and the guided tour; the data contracts
and validator.

**Used, not built:** [PrimeKG](https://github.com/mims-harvard/PrimeKG),
[DisMech](https://github.com/monarch-initiative/dismech), CPTAC via the [`cptac`](https://github.com/PayneLab/cptac)
package, NCATS Translator Node Normalization and Name Resolution, PyTorch Geometric, `pcst_fast`, the MiniLM
sentence-transformer, NVIDIA NIM (Nemotron), and the G-Retriever / GraphRAG pattern. Each source keeps its own licence
and terms of use.

## For contributors

> Before you write any output file or tool, read [`CONTRACTS.md`](CONTRACTS.md): section 0 plus the sections for what
> you produce and what you consume. [`CLAUDE.md`](CLAUDE.md) has the rules for AI coding sessions working in this repo.

### Setup (full pipeline)

    uv venv --python 3.11
    source .venv/bin/activate              # Windows: .venv\Scripts\activate
    uv pip install -r requirements.txt
    uv pip install --reinstall --no-binary pcst_fast "pcst_fast==1.0.10"   # the PyPI wheel returns wrong trees with NumPy 2.x
    cp .env.example .env                   # then add your NVIDIA_API_KEY
    ./scripts/download_data.sh             # raw PrimeKG, DisMech and CPTAC; needs wget

`scripts/setup_brev.sh` does the same on a fresh Brev GPU instance. The processed graph and GNN outputs
(`data/processed/*.parquet`, `*.pt`) are too large for git: they live on the Brev instance and the team's shared Drive
as an archive to extract at the repo root. Without them, the default `BIOHACK_MOCK=auto` falls back to the mocks for
anything missing; `BIOHACK_MOCK=1` forces the mocks everywhere.

    python -m common.validate all                                 # check every output that exists against the contract
    BIOHACK_MOCK=1 python -m agent.mock_tools                     # print every agent tool's return shape
    BIOHACK_MOCK=1 BIOHACK_FAKE_LLM=1 python -m pytest tests -q   # offline tests: no key, no data

Run code from the repo root as `python -m package.module` so `common` imports resolve.

| Environment variable | Effect |
|---|---|
| `NVIDIA_API_KEY` (in `.env`) | NIM access for the agent and the tour's live draft |
| `BIOHACK_MOCK` = `auto` (default) / `1` / `0` | Real tools where their data exists, else mocks / always mocks / never mocks |
| `BIOHACK_FAKE_LLM=1` | Replace the LLM with a deterministic stub (tests) |
| `BIOHACK_LIVE=1` | Let the Streamlit app run the agent instead of replaying `cache/demo/` |
| `BIOHACK_BUDGET=auto` / `fixed` | Agent retrieval size: self-sizing (default) or a fixed 50 nodes |
| `AGENT_ANCHOR` | Seed-to-subgraph anchor edges added to each retrieval (default 20) |

### Layout
- `common/` shared contracts: `contracts.py` (paths, constants, graph variants, ID helpers), `validate.py`
- `graph/` KG build (`build_kg.py`, build report in `ws1_report.json`) and the patient layer
- `models/` GNN-A (`gnn_a.py`, `gnn_a_api.py`), E2 patient models, the GNN-B retriever (`retriever.py`), `results/`
- `agent/` tools, agent loop, verifier and mocks; see [`agent/README.md`](agent/README.md)
- `eval/` E1-E4, BRCA portability and PCA scripts, the question set; committed numbers and charts in `results/` and `figures/`
- `app/` the Streamlit app (`streamlit_app.py`, `pages/`) and the guided tour (`tour/`); see [`app/README.md`](app/README.md)
- `docs/` notes on Alexander Young's case and the clinician workbench
- `scripts/` data download and Brev setup
- `cache/` API responses (not committed); `cache/demo/` holds the replayed demo traces and **is** committed
- `data/` raw and processed data (not committed)

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
- **Line endings:** the team works on Windows, macOS and Linux. A file that shows as modified but has no output from
  `git diff --ignore-cr-at-eol <file>` hasn't really changed; don't commit it.
