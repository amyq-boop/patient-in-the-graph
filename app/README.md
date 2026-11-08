# Demo app (WS4)

**Guided tour (pitch demo):** `python -m app.tour --open` opens a six-chapter walkthrough from the knowledge graph, through GNN-A/GNN-B, to Alexander Young's public case, then a patient workspace. See [`app/tour/README.md`](tour/README.md).

    pip install -r requirements.txt
    streamlit run app/streamlit_app.py            # from the repo root

- **Ask tab:** patient picker, the 5 demo questions (`demo: true` in `eval/questions.jsonl`), answer, claim check, evidence graph.
- **Results tab:** reads `eval/results/e1.csv`, `e2.csv`, `e3.csv`, `e4.json` and `eval/e4_answer_key.json` as they appear.

Where traces come from (CONTRACTS.md section 8 shape):
1. `cache/demo/<pid>__<qid>.json` real cached traces: `python -m agent.run --questions eval/questions.jsonl --demo-patients <pid> ...`
2. `app/mock_traces/` offline traces from the agent on mock tools + fake LLM: `python -m app.make_mock_traces`. All IDs are fake (E9000xx, PMID:0000000x) and the UI labels them as mock.

Once any real (non-MOCK) patient has cached traces, mock patients are hidden.
Live questions run the agent only with `BIOHACK_LIVE=1`; the demo itself replays the cache so it can't break.

Evidence graph: built from every `retrieve_subgraph`, `get_patient` and `get_mechanism` result in the trace. Red edges are cited by a supported claim; dashed edges are patient measurements; node size is the retrieval prize. Optional extra keys are shown if present: node `score` ({"text", "gnn"}) and edge `bifo`.
