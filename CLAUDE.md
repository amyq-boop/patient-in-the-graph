# CLAUDE.md — Patient-in-the-Graph

An LLM agent answers questions about a real ccRCC patient from CPTAC, using a PrimeKG + dismech knowledge graph and GNNs. Every claim must cite a graph edge or a dismech PMID.

Several teammates run their own Claude sessions against this repo at the same time, sometimes in the same clone. Most of the rules below exist so those sessions don't break each other's work.

## Read first
- **`CONTRACTS.md`** defines every interface between workstreams: IDs, file columns, tool return shapes, the trace format and the eval files. Sections 1–4 describe code that already exists, and sections 5–8 are specs for what's still to build.
- **`README.md`** has setup, roles and the data notes.
- **`common/contracts.py`** holds the paths, constants, graph variants and ID helpers. Import from it instead of hard-coding paths or strings.

## Rules

### 1. The contract wins
- Use the field names and shapes in CONTRACTS.md exactly. If something you need isn't there, stop and tell the user what you'd add. The team agrees contract changes in Slack.
- **When your code disagrees with `agent/mock_tools.py` or CONTRACTS.md, change your code.** Don't rewrite the mocks or the contract to fit it.
- Adding a new key or column is fine. Renaming, removing or changing the meaning of one needs the user's go-ahead, plus edits to CONTRACTS.md, `common/contracts.py`, the mocks and the validator in the same commit.
- If you change what `graph/build_kg.py` or `models/gnn_a.py` writes, update CONTRACTS.md sections 1–4 in the same commit.

### 2. Stay in your lane
- Work only on the files of the workstream the user owns (see Roles in the README). Ask before editing another workstream's files, and suggest the change instead.
- **Before editing, run `git status`.** Leave alone any uncommitted file you didn't write in this session, and any file modified in the last few minutes, because another session may be working on it. Tell the user instead.
- Never run `git checkout -- .`, `git stash`, `git clean`, `git reset --hard` or a force push. Any of them can destroy a teammate's uncommitted work.

### 3. No label leakage
- Stage, grade, TNM, survival, vital status and recurrence never become features, graph edges, prompt text or `clinical_summary` content. They live only in `labels.parquet`.
- Drop phenotype terms that describe the tumor or its spread. The list is `LEAKY_PHENOTYPE_TERMS`; add to it when you find new ones.
- Report patient predictions **out of fold**, using the shared `folds.parquet`. Never re-split.
- E1 and E4 use the masked graph variants (`e1`, `e4`, `e4_dismech`). The answer key must never be visible to the retriever or the GNN.

### 4. Grounding is the product
- Agent claims cite `E<edge_row>` edge IDs, `pt:…` patient-edge IDs or `PMID:…`, and they count as supported **only** if that ID appeared in this trace's tool results. Never fabricate IDs or PMIDs, not even in examples. Mocks use obviously fake ones (`E9000xx`, `PMID:0000000x`).
- Report results honestly. If the GNN doesn't beat a baseline, say so; don't tune on the test split.

### 5. Data and compute
- Data isn't in git. `data/raw/` comes from `scripts/download_data.sh`; `data/processed/` and `*.pt` files live on the Brev instance and the shared Drive. Locally they may not exist, so build against the mocks (`BIOHACK_MOCK=1`, or `auto`).
- Cache every external API call (NodeNorm, Name Resolution, NIM) under `cache/`. Never call an API inside a training loop.
- Secrets stay in `.env`, which is gitignored. Never print, log or commit API keys.
- If you add a dependency, add it to `requirements.txt`.

## ID quick reference
- Nodes: CURIEs (`NCBIGene:7428`, `MONDO:0005005`, `HP:0000790`). Look one up in `nodes.curie` first, then `nodes.aliases`.
- Node types: `gene`, `disease`, `phenotype`, `drug`, `pathway`, GO types, `mechanism`, and others (see CONTRACTS.md section 1). The type is `gene`, not `gene_protein`.
- Edges: `edge_id = "E<edge_row>"` (`contracts.edge_id()`). Patient edges: `pt:<patient_id>:<has_phenotype|protein_up|protein_down>:<curie>`.
- Graph variants: `full`, `slim`, `e1`, `e4`, `e4_dismech`, `no_dismech` (`contracts.GRAPH_VARIANTS`).
- ccRCC is `MONDO:0005005`. The primary label is `y_stage`.

## Commands
```bash
python -m common.validate all                                     # check every output that exists against the contract
BIOHACK_MOCK=1 python -m agent.mock_tools                         # print every tool's return shape
BIOHACK_MOCK=1 BIOHACK_FAKE_LLM=1 python -m pytest tests -q       # offline tests (no key, no data)
```
Run everything from the repo root as `python -m package.module`. Python 3.11 is the target.

**Before you tell the user something is done:** run `python -m common.validate <area>` for anything you produced and the offline tests for code you changed, then report the actual result.

## Git
- Run `git pull` before starting. Commit or push only when the user asks. Keep commits small and scoped to one workstream.
- **Line endings:** teammates are on Windows, macOS and Linux VMs. A file that shows as modified but has no output from `git diff --ignore-cr-at-eol <file>` hasn't really changed, so don't commit it. In a Linux shell working on a Windows checkout, use `git -c core.autocrlf=true …` so status matches what the user sees.
- Large outputs (`*.pt`, parquet) never go in git. Small results (CSVs and JSON under `eval/results/`, `models/results/`) and cached demo traces (`cache/demo/`) do.
