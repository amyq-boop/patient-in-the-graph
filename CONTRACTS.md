# Data contracts — Patient-in-the-Graph (v1)

These are the handoffs between workstreams. **Sections 1–4 describe what's already built on `main`** (Charis's WS1 graph build in `graph/build_kg.py` and GNN-A in `models/gnn_a.py` / `models/gnn_a_api.py`); that code is the source of truth, and this file just writes it down. **Sections 5–8 cover things that don't exist yet.** Build them to these shapes, and code against the mocks (`agent/mock_tools.py`) until the real versions land.

`common/contracts.py` holds the shared paths, constants and helpers. `python -m common.validate all` checks every file that exists so far against this document.

**Changing a contract:** post it in Slack first. Adding a column or key is fine on your own. Renaming, removing or changing a meaning needs a thumbs-up from the people who consume it, plus an edit here.

---

## 0. Global rules

1. **Entities are identified by CURIE** (`nodes.curie`, for example `NCBIGene:7428`, `MONDO:0005005`, `HP:0000790`). Tool arguments, tool outputs, patient tables and citations all use CURIEs. `gidx` and `lidx` are internal tensor indices; don't pass them between workstreams. To look up a CURIE, check `nodes.curie` first and then the `|`-separated `nodes.aliases` (`GnnA.cur2g` already does this).
2. **Tools never raise.** Every agent tool returns a JSON-serializable dict, or `{"error": "<message>"}` on failure.
3. **Labels never become features or edges.** Stage, grade, TNM, survival, vital status and recurrence stay in `labels.parquet` and nowhere else. The validator checks for this.
4. **Cache every external API call** under `cache/` (WS1 already caches NodeNorm there).
5. **Everyone uses the same splits:** `e1_split.json` (WS1, `seed=0`) and `folds.parquet` (WS2). Once published, they're frozen.
6. Run code from the repo root, and take paths from `common/contracts.py`.

---

## 1. Nodes — `data/processed/nodes.parquet` (built by WS1)

| column | meaning |
|---|---|
| `gidx` | global node index, 0..N-1 |
| `curie` | canonical ID. PrimeKG nodes get `NCBIGene`, `MONDO`, `HP`, `GO`, `DRUGBANK`, `REACT`, `UBERON` or `MESH`, with MONDO/HP/GO/UBERON zero-padded to 7 digits. Nodes that exist only in dismech keep their own ID (for example `HGNC:…`, `CHEBI:…`, `dismech:…`). |
| `type` | `gene`, `disease`, `phenotype`, `drug`, `biological_process`, `molecular_function`, `cellular_component`, `pathway`, `anatomy`, `exposure`, `mechanism`, `cell`, `treatment` |
| `lidx` | index inside its type (the row in `data[type]` / `h[type]`) |
| `name`, `alt_names` | display label, plus `|`-separated alternate names |
| `origin` | `primekg`, `dismech` or `both` |
| `aliases` | `|`-separated extra CURIEs. Grouped MONDO diseases list every member here. |
| `dm_ids` | the original dismech IDs merged into this node |

- **Patients are not graph nodes.** GNN-A attaches a patient inductively, as the signed, weighted mean of its neighbours' embeddings (see `GnnA` in `gnn_a_api.py`). A patient is therefore just a list of neighbour CURIEs with weights (section 5). Its ID is `PATIENT:<cptac_case_id>`.
- ccRCC is `CCRCC_ID = "MONDO:0005005"`. PrimeKG has **no phenotype edges** for ccRCC; its phenotypes come only from dismech.

## 2. Edges — `data/processed/edges.parquet` (built by WS1)

Each row is one **directed** edge. Both directions are stored: PrimeKG lists each edge twice natively, and each dismech edge `dm_<pred>` gets an explicit reverse `dm_<pred>_rev`.

| column | meaning |
|---|---|
| `edge_row` | row number, used to cite the edge. The citable form is **`edge_id = "E<edge_row>"`**, via `contracts.edge_id()` / `contracts.edge_row()`. |
| `src`, `dst` | `gidx` of the endpoints |
| `src_type`, `rel`, `dst_type` | the PyG edge type. `rel` is PrimeKG's relation name in snake_case (`disease_protein`, `off_label_use`, …), or `dm_<biolink predicate>` / `dm_<pred>_rev` for dismech. |
| `origin` | `primekg` or `dismech` |
| `dm_row` | the row in `evidence.parquet` for dismech edges, and -1 for PrimeKG |
| `hub` | `anatomy_protein_*` and `drug_drug` edges, which the slim graphs drop |
| `e1_holdout` | disease–gene and disease–GO edges (from both sources) for E1 *test* diseases, plus every edge of their dismech mechanism nodes. Both directions are flagged. |
| `e4_dismech` | ccRCC's dismech edges and mechanism edges |
| `e4_full` | `e4_dismech` plus PrimeKG's ccRCC gene and drug edges |

**Graph variants.** These are the only named graphs. Each one is a set of flags to drop, listed in `contracts.GRAPH_VARIANTS`.

| variant | drops | use |
|---|---|---|
| `full` | nothing | agent and demo answers |
| `slim` | `hub` | default for message passing |
| `e1` | `hub` + `e1_holdout` | E1 retrieval eval |
| `e4` | `hub` + `e4_full` | E4 live demo; GNN-A `main` was trained with this mask plus `e1_holdout` |
| `e4_dismech` | `hub` + `e4_dismech` | softer E4 (PrimeKG's ccRCC genes stay visible) |
| `no_dismech` | `origin == dismech` | E2 ablation; GNN-A tag `no_dismech` |

The PyG exports are `graph_full.pt`, `graph_slim.pt` and `graph_slim_e1.pt`. Each edge store carries `edge_row`, so any retrieved tensor edge maps back to its `edge_id`.

## 3. Evidence and E1 split (built by WS1)

- **`evidence.parquet`** holds every dismech edge row, keyed by `dm_row`, with dismech's own columns (publications, supporting text, …). `get_mechanism` turns these into `pmids: ["PMID:12345678", …]` plus quotes.
- **`e1_split.json`** has the shape `{"seed", "test_frac", "train": [...], "test": [...]}`. Each entry is `{"disease", "name", "targets_gene": [CURIE], "targets_go": [CURIE], "seeds_phenotype": [CURIE]}`. The E1 query seeds are `[disease] + seeds_phenotype`, and the hidden answers are `targets_gene` plus `targets_go`.

## 4. Features and GNN-A (built)

- **`feat_text.pt`** is `{"model", "x": [N, d] in gidx order, "text"}`, currently made with `sentence-transformers/all-MiniLM-L6-v2`. **GNN-B must embed questions with the same `model`.** If anyone switches to an NIM embedding model, regenerate `feat_text.pt` and retrain.
- **`emb_<tag>.pt`** is `{"h": {type: [n_type, 128]}, "gidx_by_type", "rel": {"src__rel__dst": vector}}`. The tags are `main` (masks `hub,e1_holdout,e4_full`) and `no_dismech`.
- **`models/gnn_a_api.GnnA(processed, tag)`** is how everyone else uses GNN-A:
  - `rank_for_patient(neighbour_curies, node_type, k, weights)`: ranking by cosine similarity
  - `rank_for_patient_rel(...)`: ranking through the DistMult relations
  - `rank_relation(src_curie, edge_type, k)`
  - `similarity_to_seeds(seed_curies, node_type, weights)`: GNN-B's β term

  The ranking methods return a DataFrame with columns `curie, name, score`.

---

## 5. Patient tables — `data/processed/patients/` (WS2, to build)

| file | columns |
|---|---|
| `patients.parquet` | `patient_id` (CPTAC case ID) · `curie` (`PATIENT:<id>`) · `age` · `sex` · `clinical_summary` (one readable sentence, **no label information**) · any other non-label clinical fields |
| `patient_phenotypes.parquet` | `patient_id` · `curie` (a phenotype or disease node in `nodes.parquet`) · `source_field` (usually `medical_condition`) · `source_text` (the raw free-text term it came from) |
| `patient_proteins.parquet` | `patient_id` · `curie` (a `NCBIGene` gene node) · `symbol` · `z` (signed) · `direction` (`up`/`down`) · `rank` (1–50 within each direction) · `z_ref` (`normal`/`cohort`) |
| `labels.parquet` | `patient_id` · `y_stage` (0 = stage I–II, 1 = stage III–IV) · `y_grade` · `y_recur` (nullable) · `os_days` (nullable) · `os_event` (nullable) |
| `folds.parquet` | `patient_id` · `fold` (0–4), stratified on `PRIMARY_LABEL = "y_stage"`. **Frozen once published.** |

**Also in `patients/` (final, Nov 7):** `patient_mutations.parquet` (somatic tumor mutations: `curie` = gene, `name` = symbol, `source_text` = protein change) and `patient_drugs.parquet` (baseline medications mapped to DrugBank), same columns: `patient_id, curie, name, source_field, source_text, weight`. GNN-A doesn't use them. `get_patient` returns them as `mutations` and `medications`, citable as `pt:<pid>:somatic_mutation:<curie>` and `pt:<pid>:takes_medication:<curie>`.

**Feeding GNN-A:** a patient's neighbours are the `curie`s from `patient_phenotypes` and `patient_proteins`. Their weights are 1.0 for phenotypes and `z` for proteins, so down-regulated proteins get negative weights, as `GnnA` expects. `contracts.patient_neighbours(pid)` builds both lists.

**Patient edges don't go in `edges.parquet`.** When an answer cites one, the citable ID is `pt:<patient_id>:<has_phenotype|protein_up|protein_down>:<curie>` (from `contracts.patient_edge_id()`).

**Known facts about the cohort** (Amy's check, Nov 7):
- 110 tumors and 84 matched normals, with about 12k proteins.
- Stage splits 65 / 45. There are 22 recurrences and 21 deaths. The follow-up and medical_history tables are empty, so use the clinical table.
- Phenotypes come from `medical_condition`, which is pipe-separated free text. The median is 3 per patient, and 15 patients have none. Zero phenotypes is allowed.
- **Leaky terms are dropped.** Some conditions describe the tumor itself or its spread (renal mass, lytic lesion of bone, pulmonary nodules), so they would leak stage. They're listed in `contracts.LEAKY_PHENOTYPE_TERMS`; extend that list when you find more. The validator checks `source_text` against it.

## 6. GNN outputs still to build (GNN)

- **`data/processed/gnn_a/predictions.parquet`** has columns `patient_id` · `model` (`clinical_lr`, `prot_pca_lr`, `concat_lr`, `gnn`, `gnn_no_dismech`) · `fold` · `risk` (0–1). Every prediction is **out of fold**, and `predict_patient` reads from this file. E2 also comes from it.
- **`data/processed/gnn_a/contributions.parquet`** *(optional)* has columns `patient_id` · `curie` · `score` · `rank`, the top neighbours behind each patient's `gnn` risk. `predict_patient` fills `top_contributing` from this file and returns an empty list until it exists.
- **GNN-B lives in `models/retriever.py`:**
  ```python
  retrieve_subgraph(question: str, seeds: list[str], budget: int = 50,
                    retriever: str = "gnn_pcst",   # "bfs" | "text_pcst" | "gnn_pcst"
                    variant: str = "full",
                    target_types=None) -> dict      # SubgraphResult, section 7
  ```
  **Adaptive size (optional, Nov 7):** if `models/retriever.py` sets a module constant `ADAPTIVE_BUDGET = True`, then `budget=None` means the retriever sizes the subgraph itself (PCST's own prize/cost trade-off, with its own ceiling). The agent passes `budget=None` by default (`BIOHACK_BUDGET=auto`; `fixed` restores 50) plus `anchor` seed edges (`AGENT_ANCHOR`, default 20); with `ADAPTIVE_MAX_NODES = 500` the size is set by PCST's K = 150 prized terminals (about 150-185 nodes), not by a cap. E1 always passes fixed budgets.
  `target_types` (added Nov 7) picks which node types get prizes. `None` keeps the default (genes + GO terms, the E1 setting); the agent passes a wider set via `BIOHACK_TARGET_TYPES` in `agent/config.py`.
  `seeds` are CURIEs; patient seeds are expanded through `patient_neighbours`. All three retrievers share the signature, so E1 just loops over them with `variant="e1"`.

## 7. Agent tool interface — `agent/tools.py` (Agent), mirrored by `agent/mock_tools.py`

Both modules expose `TOOLS` (OpenAI tool schemas) and `dispatch(name, args) -> dict`. Set `BIOHACK_MOCK=1` to use the mocks.

| Tool | Returns |
|---|---|
| `resolve_terms(texts)` | `{"results": [{"text", "matches": [{"curie", "name", "type", "score"}]}]}`, where matches are limited to CURIEs present in `nodes.parquet` |
| `get_patient(patient_id)` | `{"patient_id", "curie", "clinical_summary", "phenotypes": [{"curie", "name", "edge_id"}], "proteins_up": [{"curie", "symbol", "z", "edge_id"}], "proteins_down": [...]}`. Each `edge_id` is the patient's `pt:…` ID, so the agent can cite it. **No labels.** |
| `rank_for_patient` note (Nov 7) | By default it ranks from the patient's **tumor proteins only** (`"basis": "tumor_proteins"`); with phenotypes included, comorbidities dominated the targets. `BIOHACK_RANK_BASIS=all` restores phenotypes + proteins. |
| `rank_for_patient` drugs (Nov 7) | Drugs are ranked **cohort-relative** by default (`"scoring": "cohort_relative"`): only drugs with an `indication` edge in the current variant (our stand-in for approved), ordered by the patient's standardized GNN score minus the mean of the other patients' (leave-one-out). `score` is that lift in SD units and `model_score` the raw GNN score. With raw scores, hub compounds (iron, arachidonic acid, copper, ATP, heparin) topped most patients' lists. Variants with no indication edges (`bifo_strict`, `bifo_flow`, `e1_bifo_strict`) skip the filter and say so in `filter`. `BIOHACK_RANK_DRUGS=raw` restores the raw ranking. Genes and pathways still use the raw score (`"scoring": "model_score"`). |
| `get_mechanism` note (Nov 7) | Also returns `drug_evidence: [{"curie", "name", "relation", "edge_id", "pmids", "quotes"}]`, the curated drug–disease edges; their edge IDs and PMIDs count as evidence. |
| `predict_patient(patient_id)` | `{"patient_id", "label": "y_stage", "risk", "model": "gnn", "fold", "evidence_id": "pred:<pid>:gnn", "top_contributing": [{"curie", "name", "type", "score", "evidence_id": "contrib:<pid>:<curie>", "driver": "tumor"\|"clinical"}], "tumor_drivers": [...], "clinical_drivers": [...], "driver_split": {"tumor_share", "clinical_share", "basis"}}` — `tumor_drivers` are the tumor's proteins, `clinical_drivers` are recorded phenotypes (HP terms: symptoms or comorbidities); the shares are over the listed top contributors only |
| `rank_for_patient(patient_id, node_type, k=10)` | `{"patient_id", "node_type", "basis", "scoring", "items": [{"curie", "name", "score", "rank", "evidence_id": "rank:<pid>:<node_type>:<curie>"}]}`, plus `filter`, `note` and per-item `model_score` for cohort-relative drugs. This wraps `GnnA.rank_for_patient_rel` with `patient_neighbours`, and `node_type` is one of `gene`, `pathway`, `drug`. |
| `retrieve_subgraph(question, seeds, budget=50)` | **SubgraphResult**: `{"nodes": [{"curie", "name", "type", "prize"}], "edges": [{"edge_id", "src_curie", "rel", "dst_curie", "origin"}], "edge_ids": [...], "triples_text": str, "meta": {"retriever", "variant", "budget", "n_candidates", "alpha", "beta"}}` |
| `get_mechanism(disease_curie)` | `{"disease", "name", "chains": [{"chain_id", "steps": [{"mechanism", "label", "edge_id", "pmids": [...], "quotes": [...]}]}], "genes": [CURIE], "drugs": [CURIE]}` |

**`triples_text`** has one line per edge, built with `contracts.triple_line()`:
```
[E1834221] VHL (gene) --disease_protein--> clear cell renal carcinoma (disease)
```

## 8. Traces and eval files

**Trace:** each run writes `eval/traces/<run_id>/<qid>.json`. Cached demo traces use the same shape at `cache/demo/<patient_id>__<qid>.json`, and those are committed.
```json
{"contract_version": "1", "run_id": "...", "qid": "P01", "mode": "agent",
 "patient_id": "C3L-00004", "question": "...", "model": "<NIM model id>",
 "tool_calls": [{"name": "...", "args": {}, "result": {}, "ms": 0}],
 "evidence": {"edge_ids": ["E..."], "pmids": ["PMID:..."]},
 "draft": "...",
 "claims": [{"claim_id": "c1", "text": "...", "support": {"edge_ids": [], "pmids": []}, "status": "supported"}],
 "answer": "...",
 "verifier": {"n_claims": 0, "n_supported": 0, "n_hypothesis": 0, "n_removed": 0}}
```
- `mode` is `agent` or `llm_only`, and `status` is `supported`, `hypothesis` or `removed`.
- **A claim counts as supported only if** at least one of its cited `edge_ids` appears in `evidence.edge_ids` (everything `retrieve_subgraph` and `get_patient` returned in this trace), or at least one of its `pmids` appears in `evidence.pmids` (everything `get_mechanism` returned). The model's word that a claim is supported doesn't count.
- **Model outputs are citable too** (added Nov 7): `pred:<pid>:<model>` and `contrib:<pid>:<curie>` from `predict_patient`, and `rank:<pid>:<node_type>:<curie>` from `rank_for_patient` (helpers in `common/contracts.py`). They go in `evidence.edge_ids`. The verifier reports claims backed only by these as `verifier.n_supported_model`, so E3 can separate "the graph says" from "the model says".
- **Not every sentence is a claim** (added Nov 7): section headers are skipped, and caveats, statements that something cannot be determined, and proposed experiments are kept in the answer but not scored. They're listed in `verifier.non_claims` (`text`, `reason`) and counted in `verifier.n_non_claims`, so the hand-check can catch biology hiding in a caveat.

| file | owner | shape |
|---|---|---|
| `eval/questions.jsonl` | Demo | `{"qid", "kind": "patient"/"disease", "patient_id", "disease", "question", "expected": {"curies": [...], "notes"}}`. 10 patient questions and 10 disease questions; see `questions.example.jsonl`. |
| `eval/results/e1.csv` | GNN | the same columns as Charis's `models/results/gnn_a_*_e1_preview.csv`: `disease, kind (gene/go), method, n_targets, recall50, recall500, mrr`. `method` is `bfs`, `text_pcst`, `gnn_pcst` or `llm_only`. |
| `eval/results/e2.csv` | GNN | `model, fold, auroc` |
| `eval/results/e3.csv` | Agent | `mode, qid, n_claims, n_supported, n_hypothesis, n_removed` |
| `eval/results/e4.json` | Agent | `{"patient_id", "qid", "recovered_steps", "expected_steps", "drugs_surfaced"}` |
