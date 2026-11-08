# Alexander Young: live graph-retrieval case-study patch

This patch extends the previously installed public-case page. Its added tab is **Live GNN + verifier**. It does **not** modify the original CPTAC model, E1–E4 evaluation, agent interfaces, or main Streamlit entry point.

## Apply in the Codespace

Extract this ZIP at the repository root, allowing it to overwrite `app/pages/1_Alexander_Young.py`. It adds `app/external_reasoner.py` and `tests/test_external_reasoner.py`.

```bash
python -m pytest tests/test_external_case.py tests/test_external_reasoner.py -q
streamlit run app/pages/1_Alexander_Young.py
```

Open port **8501**, and select **Live GNN + verifier**.

## Modes and what they actually do

1. **Run GNN → synthesis → verifier** uses real `models.retriever.retrieve_subgraph` with `retriever=gnn_pcst`, `variant=full`, a source-linked gene marker as seed, and a 50-node retrieval budget. It never calls `agent.tools.dispatch`, so `BIOHACK_MOCK=auto` cannot silently substitute mock evidence. It builds a trace containing the original source observation, actual returned edge IDs, retrieved connections, the draft, each verifier claim, and the final answer.
2. **Use NVIDIA LLM for evidence-cited synthesis** optionally passes the observation and retrieved graph edges to the existing `agent.llm.get_llm()` client, then passes its draft to `agent.verifier.verify(strict=True)`. Requires `NVIDIA_API_KEY` in the repo `.env`. With this checkbox off, the program forms a deterministic evidence-cited draft from the graph edges, then runs the **same real verifier**. The splitter uses the repo's deterministic rules for a fast, auditable verification pass.
3. **Run source-only verifier (no GNN)** is always available and provides a meaningful, truthful fallback when large graph model files have not been provisioned. It verifies an observed marker and removes an intentionally unsupported efficacy claim, with no GNN or LLM claim.

Only four *publicly reported* historical immunohistochemistry marker measurements are eligible graph seeds (CEACAM5, MET, TROP2/TACSTD2, HER2/ERBB2); each is linked to its source, specimen and assay. The app's original JSON loader checks source file SHA-256 against `https://youngcrc.com/manifest.json`. A marker's IHC score does **not** become a signed tumor-protein z-score. The app never generates a kidney-cancer risk score for this colorectal patient.

## Real GNN prerequisites

Graph assets are **not checked into Git** in this repo. For the actual `gnn_pcst` mode, the Codespace needs the same files that the original GNN-B retriever expects:

```bash
ls -lh data/processed/nodes.parquet data/processed/edges.parquet \
       data/processed/evidence.parquet data/processed/feat_text.pt \
       data/processed/emb_main.pt
```

If any are missing, provision the project's existing processed graph/model data kit (using your team's shared artifact source) in these paths. **Do not copy CPTAC patient risk predictions into the colorectal case.** The UI displays missing paths and disables the GNN button until they are present. This patch does not download gigabytes of assets or fabricate a retrieval result.

## Truthfulness and scientific caveats

* These are historical assay results associated with 2024 specimens, not verified current metastatic-node measurements.
* Source-hash checking shows that files match a publication manifest; source-join checking shows that identifiers resolve. Neither guarantees a laboratory or clinical conclusion.
* The KG edge-ID verifier checks whether a claim cites an actually returned edge and names a matching entity. It does **not** prove biological entailment, a causal link, treatment efficacy, or survival prediction; independent scientific review remains necessary.
* The injected unsupported statement is **deliberately fabricated for verifier testing**, not an LLM-generated medical prediction.
* No held-out quantitative colorectal prediction, drug-response accuracy or validated outcome metric is claimed. The CPTAC E1–E4 statistics remain distinct.

## Demonstration flow (about 60–75 seconds once models are warm)

1. Select the published **CEACAM5 IHC H-score** and show its 2024 liver-metastasis source record.
2. Click the real GNN button, then show one retrieved directed graph edge and its ID.
3. If an NVIDIA key is configured, toggle LLM synthesis to show the agent explain the retrieved association (never a recommendation).
4. Show the final verifier answer, including the count of removed unsupported claims.
5. Open the verifier audit and download the trace. If model assets are missing, use the source-only verifier and **say explicitly** that GNN retrieval was not run.
