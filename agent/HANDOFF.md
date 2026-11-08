# Agent workstream (WS5) handoff — Nov 7, ~20:15

State of the agent (`agent/`, `eval/e3_grounded.py`, `eval/questions.jsonl`) after the desktop-app session, for whoever picks it up next (a Claude Code CLI session or a teammate). Two chats fed this: agent fixes ("WS5" chat) and the overnight-plan review ("Proposed features and workload impact" chat).

**Local main is 2 commits ahead of origin (`5caedfb`, `2f5a58c`): push before anyone starts the cancer-parameter edits in `agent/tools.py` / `CONTRACTS.md`.** Line endings in those commits are clean (LF in git, small diffs).

## Working rules

- **Python env:** uv. Install with `uv pip install ...`, never bare `pip`. Run from the repo root in the `.venv`.
- **Line endings:** files are CRLF on this Windows checkout. Use `git -c core.autocrlf=true status/diff` or every file looks modified.
- **Don't commit:**
  - `.env`, the data-kit `.tar`, `MANIFEST_*.sha256` and `README_HANDOFF.md` (all in `.gitignore`).
  - Parquet files: this is why `ws2-patient-layer` can't be merged.
- **Other people's files:**
  - `models/retriever.py` was Charis's; Kaitlin may edit it now (Charis left).
  - `models/gnn_a_api.py` (GNN-A), Amy's app, the eval README, Anikat's stratification and Aniket's BRCA files: check with the owner first.
- **Untracked, from another Claude Code session:** `eval/e3_batch.py`, `eval/e3_score.py` and `tests/test_e3_*.py` are the E3 runner and scorer. Commit them deliberately, in their own commit, **before** E3 runs, so each run's manifest points at a real commit.
- **Freeze: about 1 AM was proposed to the team; the team decides.** After the freeze, no prompt or tool changes without telling the team: E3, the demo traces and E4 run once on the final setup.

## What changed today (and why)

| Change | Why | Where |
|---|---|---|
| Citable model outputs `pred:` / `contrib:` / `rank:` | Model claims are scored separately from graph claims (`n_supported_model`) | `common/contracts.py`, verifier, CONTRACTS §8 |
| `get_patient` returns somatic mutations + baseline medications; compact view (mutations first, top 25 up / 15 down proteins) | The old view truncated before mutations, so VHL p.N131I was invisible | `agent/tools.py`, `agent/graph_agent.py` |
| Verifier: non-claims, `mismatched_ids`, strict mode, rules splitter, empty list stubs dropped | Fewer false removals; strict mode for E3 | `agent/verifier.py` |
| Retriever: `target_types`, hub penalty 0.25 for agent calls, 20 anchor edges, adaptive budget, ceiling 500 | No retries needed; P01 went from 267 s to about 56 s | `agent/config.py`, `models/retriever.py` |
| `predict_patient` tumor vs clinical driver split + prompt rule 4 | Hypertension was leading P01's risk summary though it's 17% of the top-10 weight. Across the cohort a clinical phenotype is the #1 contributor for 61/110 patients. | `5caedfb` |
| `rank_for_patient` drugs ranked cohort-relative over approved drugs (indication edge; respects the variant mask) | The raw ranking put generic compounds (iron, ATP, arachidonic acid, copper, heparin) on top for most patients. `BIOHACK_RANK_DRUGS=raw` restores it. | `2f5a58c` |

## Overnight agent work (proposed in the team thread; priority order)

1. **Drug–target tool (top priority).** Return PrimeKG `drug_protein` edge IDs (`E…`) linking drugs to the patient's dysregulated proteins, so a drug is shown and cited because its targets overlap.
   - Two `brca-portability` expectations (11BR060-3 and 18BR003-2, the alpelisib–PIK3CA edge) need these cited edges in traces.
   - It also answers "ranking drugs per patient reads like drug-response prediction", and it is the reliable alternative to the weak GNN drug ranking (see limitations).
2. **Cancer parameter (BRCA).** ccRCC is hard-coded in several places:
   - the `patients/` dir in `common/contracts.py` (the path change needs team agreement);
   - `AGENT_SYSTEM` and `LLM_ONLY_SYSTEM`;
   - `get_mechanism(__CCRCC_ID__)`;
   - "not ccRCC treatment" in `get_patient`'s note;
   - `LEAKY_PHENOTYPE_TERMS`, which has renal terms only.

   BRCA patient tables landed in `data/processed/brca/patients/` (Aniket, `705a1b0`); the ccRCC tables in `patients/` are unchanged. Two traps:
   - `_cohort_scores` / `rank_for_patient` must compare a patient **only against the same cancer's cohort**. Today it reads `P.patient_proteins`, so it's ccRCC-only. When the cancer switch repoints those paths, add the cancer to the `lru_cache` key of `_cohort_scores` and `_patient_tables`.
   - Make the ccRCC prompts come out **byte-identical** (hash the built prompt), so the LLM-only baseline stays valid.
3. **`predict_patient` framing.** The proposal is to make it optional and drop the risk-first framing (no leakage, but "high-risk" next to label-free clusters invites "did stage go into your subgroups?"). This is pending objections.
   - If adopted, it touches prompt rule 4, the "For a patient question" step order, and P01's wording in `eval/questions.jsonl`.
   - Changing a question changes E3, so decide before the final E3 run.
4. **New trace fields:** `cancer`, `data_origin`, `execution` and evidence gaps. These don't affect E3.
5. **Subgroup tool plus `cl:<cancer>:<cluster>:<feature>` citation IDs** backed by `clusters.json`, with enrichment wording required to cite one. That's a CONTRACTS §8 change and needs a thumbs-up from Anikat (shape) and Amy (UI).
   - **Unblocked:** `data/processed/clusters/clusters_ccrcc.json` and `clusters_brca.json` landed in `705a1b0`, and the per-patient briefs are in `data/processed/briefs/`. Their keys include `cancer`, `data_origin`, `execution`, `clusters`, `assignments`, `stability` and `comparison_proteomics_vs_gnn`. It's still the first thing to cut if time runs short.
   - Note from `cohort-pca`: the GNN patient map is mostly comorbidities (PC1 vs hypertension r = −0.75), so clusters may be weak. The fallback is exploratory signatures plus reported instability.
6. **Deterministic verifier-rejection example:** a fixed draft with `VERIFIER_SPLITTER=rules`, kept as an offline test, plus a real trace for the demo.

## E3 plan (revised)

E3 does **not** have to wait for everything:

| Run | When | Command |
|---|---|---|
| LLM-only baseline | now (it doesn't depend on the agent) | `BIOHACK_MOCK=0 python -m eval.e3_batch --mode llm_only --run-id e3` |
| Agent, as insurance | now, on the committed code | `BIOHACK_MOCK=0 VERIFIER_STRICT=1 VERIFIER_SPLITTER=rules python -m eval.e3_batch --mode agent --run-id e3_v1` |
| Agent, final | at the freeze, if the drug–target or subgroup tool landed | same with `--run-id e3_final` |

- **Never mix runs from two setups.** If `e3_final` doesn't fit, report `e3_v1`, labelled "before the drug–target/subgroup tools".
- **Verifier-only changes:** use `python -m eval.e3_batch --reverify`, which re-scores saved drafts without new agent runs.
- **Rate limits:** use `--workers 2` while also testing by hand, since both share the NIM limits.
- **Hand-check:** the 50-claim hand-check can start on `e3_v1` tonight, with two volunteers. It stays valid as long as the verifier logic doesn't change. Read whole bullets, not just IDs, and spot-check about 10 of the judge's verdicts in `eval/results/e3_quality_detail.jsonl`.

Scoring, after the runs (swap in `e3_final` / `_rv` dirs as appropriate):
```bash
python -m eval.e3_grounded eval/traces/e3_v1_agent eval/traces/e3_llm_only --sample 50
python -m eval.e3_score    eval/traces/e3_v1_agent eval/traces/e3_llm_only
```

## Other remaining steps (after the freeze)

```bash
python -m pytest -q tests/                                  # retriever tests need torch, i.e. the .venv
BIOHACK_MOCK=0 VERIFIER_STRICT=1 VERIFIER_SPLITTER=rules python -m agent.run --questions eval/questions.jsonl --only P01   # smoke: ~1 min, 0 mismatches
BIOHACK_VARIANT=e4 BIOHACK_MOCK=0 python -m eval.e4_case_study --patient C3N-01649    # E4 on current code, then review -> e4.json
BIOHACK_MOCK=0 python -m agent.run --questions eval/questions.jsonl --demo-patients <pid1> <pid2> <pid3>
```

- **Demo patients: align first.** `demo-traces-3x5` used C3L-00416, C3N-00244 and C3L-00790; testing used C3N-01649.
- **Demo regeneration time:** about 15 min for 3 traces, about 1 h for all 15.
- **E4:** a VHL-mutated patient is suggested, because the old C3L-00416 run didn't rediscover VHL/HIF/VEGF. Or reuse a demo patient so E4 doubles as a demo case.
- **Optional:** an LLM-only E1 baseline, and the Translator tool.

## Branches (as of ~19:40; re-check)

| Branch | Status |
|---|---|
| `brca-portability` (Aniket) | Good: patients chosen without proteins, expectations committed before runs. Needs the drug–target tool and same-cancer drug ranking. |
| `cohort-pca` (Charis) | Fine to merge as analysis, but read the comorbidity finding before building `clusters.json` |
| `charts-e1-e2` | The E1 chart needs redrawing with `text_ppr_pcst` as the main baseline (pooled Recall@50 0.274 vs 0.220; lead with MRR 0.289 vs 0.128 plus a paired per-disease test) |
| `demo-traces-3x5` | Stale (no mutations, non-strict verifier, risk-first P01); regenerate after the freeze |
| `e4-case-study-c3l-00416` | Older code, unscored; rerun |
| `ws1-04-bifo` | Superseded by main; merging would undo newer work. Close it. |
| `ws2-patient-layer` | Commits a 13 MB parquet file to git. Don't merge. |

## Known limitations (put in the writeup)

- **GNN-A learns drug classes, not specific drugs.** For VEGFR2 its top hits are kinase inhibitors, but sunitinib ranks 147th and axitinib 66th of 8,652. Everolimus ranks 279th for mTOR, and these edges are in the training graph.
- **Most ccRCC drug targets never appear in the patients' 100-protein lists:** KDR, PDGFRB, KIT, AXL and EPAS1 are absent. FLT1 appears in 25 patients, MET in 13.
- **What follows:**
  - The model-ranked drug list is weak. It stays in traces for analysis; answers lead with curated drug evidence.
  - Retraining GNN-A was considered and not recommended before the deadline: it would mean new embeddings, then rerunning E2, probably E1, then E3, E4 and the demo traces.
- **Patient phenotypes in CPTAC are all comorbidities:** 17 terms, led by obesity (51 patients) and hypertension (50). 21 patients have none; CONTRACTS says 15, which needs checking.
- **Belzutifan is newer than the PrimeKG drug data:** it's in DisMech drug evidence only.
- **BRCA isn't held out:** breast carcinoma and ER+ breast cancer are in the E1 train split. Frame BRCA as portability plus patient-level checks.
- **A semantic support gap only the hand-check catches:** a cited edge can exist but not support the sentence. Example: "TP53-mediated genomic instability [E137702]".

## Question set

`eval/questions.jsonl` has 20 questions:
- **P01–P10:** patient questions with real CPTAC IDs.
- **D01–D10:** disease questions; D07–D10 test generalization beyond ccRCC.
- **Traps:** P02, P09 and P10, where the correct answer is that the evidence can't settle it.

There's a spreadsheet copy in the chat thread (`E3_question_set.xlsx`).

## Knobs (env vars, defaults)

- **Data and model:**
  - `BIOHACK_MOCK=auto`; use 0 for real runs.
  - `BIOHACK_VARIANT=full`.
  - `NIM_MAX_TOKENS=8192` and `NIM_MAX_TOKENS_CLAIMS=16384`; `NIM_TIMEOUT=300`; `LLM_CACHE=1`.
  - The model is `nvidia/nemotron-3-ultra-550b-a55b` (`common/contracts.py`). Lightning was slower because it truncated, and llama-3.3-70b is retired.
- **Ranking:** `BIOHACK_RANK_BASIS=tumor`, `BIOHACK_RANK_DRUGS=cohort`.
- **Retrieval:** `BIOHACK_TARGET_TYPES=agent`, `BIOHACK_BUDGET=auto`, `AGENT_ANCHOR=20`, `AGENT_RETRIEVE_VIEW_CHARS=25000`.
- **Agent loop:** `AGENT_MAX_TOOL_CALLS=12`, `AGENT_REVISE_ALWAYS=0`.
- **Verifier:** `VERIFIER_STRICT=0` and `VERIFIER_SPLITTER=llm` by default. Use `1` and `rules` for E3.
