# Agent (WS5)

Tool-calling agent on NVIDIA NIM, plus the claim verifier and traces for E3/E4 and the demo.
Shapes follow `CONTRACTS.md` §7–§8 (the repo version). Mocks are `agent/mock_tools.py`.

## Run

```bash
pip install pandas pyarrow requests openai pytest
# .env in the repo root: NVIDIA_API_KEY=nvapi-...   optional: NIM_MODEL=... (default from common/contracts.py LLM_MODEL)

# offline, no key needed (mock tools + scripted fake LLM)
BIOHACK_MOCK=1 BIOHACK_FAKE_LLM=1 python -m agent.run -q "Why is this patient high-risk, and what would you target?" -p C3L-MOCK1
python -m pytest tests/test_agent_offline.py -q

# real LLM on mock tools (before the graph and GNN files exist)
BIOHACK_MOCK=1 python -m agent.run -q "Why is this patient high-risk, and what would you target?" -p C3L-MOCK1

# E3: the 20 questions through the agent and through the bare LLM, then aggregate
python -m agent.run --questions eval/questions.jsonl --mode both --run-id e3
python -m eval.e3_grounded eval/traces/e3_agent eval/traces/e3_llm_only --sample 50

# demo cache: 3 patients x 5 patient questions -> cache/demo/<pid>__<qid>.json
python -m agent.run --questions eval/questions.jsonl --demo-patients C3L-00004 C3L-00010 C3L-00026

# E4 live demo: retrieve from the graph with ccRCC's dismech edges hidden
BIOHACK_VARIANT=e4 python -m agent.run -q "Explain why this patient is high-risk." -p <high-risk pid>
```

## Switches

| env | values | effect |
|---|---|---|
| `BIOHACK_MOCK` | `auto` (default), `1`, `0` | `auto` uses real files where they exist and mocks otherwise; mock output is tagged in the trace `notes` |
| `BIOHACK_FAKE_LLM` | `1` | scripted LLM for tests |
| `BIOHACK_VARIANT` | `full` (default), `e4`, `e4_dismech`, `no_dismech`, ... | graph variant for every `retrieve_subgraph` call (the LLM can't change it) |
| `NIM_MODEL` | any tool-calling model on build.nvidia.com | default `nvidia/nemotron-3-ultra-550b-a55b` (`contracts.LLM_MODEL`); re-check with `python -m agent.models --probe` |
| `VERIFIER_STRICT` | `0` (default), `1` | `1` also requires the cited edge to mention something in the claim |
| `BIOHACK_CANCER` | `ccrcc` (default), `brca` | cohort for questions with no patient; a patient ID always selects its own cohort (`agent.tools.cancer_of`) |
| `AGENT_DRUG_TARGETS` | `0` (default), `1` | offer `get_drug_targets` to ccRCC runs too (always offered for BRCA). E3/E4/demo were run without it; turning it on changes the ccRCC tool list, so re-run them |
| `LLM_CACHE` | `1` (default), `0` | NIM responses cached in `cache/llm/` |

## How a run works (`agent/graph_agent.py`)

1. The LLM calls tools (max 12 calls). Tool results go back to it; `retrieve_subgraph` is shown as `triples_text` + `edge_ids`.
2. **Retrieval guarantee** (patient questions): if no retrieved path links one of the patient's proteins to one of their phenotypes (not via the patient node), the controller re-runs `retrieve_subgraph` with 1.5x the budget and more seeds, up to 3 times, then asks the LLM to revise.
3. **Verifier** (`agent/verifier.py`): the LLM splits the draft into claims (deterministic splitter as fallback). Support is checked in code: only IDs written in the claim's own sentence count, and they must be in `evidence` (edges from `retrieve_subgraph`, PMIDs from `get_mechanism`). Evidence is the edge IDs from `retrieve_subgraph` and the `pt:` edge IDs from `get_patient`, plus PMIDs from `get_mechanism`. Unsupported → `hypothesis` if the draft framed it so, else `removed` and cut from the answer.
4. The trace is written to `eval/traces/<run_id>/<qid>.json` and checked by `agent/trace_check.py`.

Extra trace keys beyond §7: `variant`, `notes` (mock use, tool errors), `elapsed_s`, `verifier_splitter`, per-tool-call `by` (`llm`/`controller`), per-claim `quote`, `unmatched_ids` (cited IDs not in evidence, i.e. fabricated) and `entity_match`, and `verifier.n_supported_entity_match`.

## Integration notes

- **GNN-B:** `models/retriever.py` must expose `retrieve_subgraph(question, seeds, budget, retriever, variant)` returning the §7 SubgraphResult (dict, dataclass or pydantic all work). If `triples_text` is missing, `tools.py` builds it with `contracts.triple_line()`.
- **GNN-A:** `rank_for_patient` calls `GnnA(tag).rank_for_patient_rel(...)` on the patient's **tumor proteins only** by default (`BIOHACK_RANK_BASIS=all` adds the phenotypes back, i.e. `patient_neighbours(pid)`), with tag `no_dismech` when `BIOHACK_VARIANT=no_dismech` and `main` otherwise. `predict_patient` reads `gnn_a/predictions.parquet`; `top_contributing` stays empty until someone writes `gnn_a/contributions.parquet` (`patient_id, curie, score, rank`). Each contributor is tagged `driver: tumor|clinical` and also listed under `tumor_drivers` / `clinical_drivers`, with `driver_split` giving each group's share of the top-10 weight; the LLM sees the two groups, not the combined list. Drugs from `rank_for_patient` are cohort-relative by default: approved drugs (an `indication` edge in the variant) ranked by how far above the rest of the cohort they score for this patient (`BIOHACK_RANK_DRUGS=raw` restores the raw GNN ranking).
- **get_mechanism** is built in `tools.py` from `edges.parquet` + `evidence.parquet`: the disease's dismech mechanism nodes (plus anything reachable through mechanism→mechanism edges), ordered into chains along those edges, with PMIDs regex-parsed from the evidence row and quotes from any text/support column. If WS1 later writes `data/processed/mechanisms.jsonl` in the §7 shape, that file wins. **Check the ccRCC output by eye once the real files exist.**
- `LLM_MODEL` in `common/contracts.py` is `nvidia/nemotron-3-ultra-550b-a55b`, which passed the tool-call probe in about 1 s on Nov 7. `NIM_MODEL` in `.env` overrides it.
