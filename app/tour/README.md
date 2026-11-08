# Guided tour: from biological knowledge to Alexander Young

A full-screen, six-chapter walkthrough over one persistent network, then a patient workspace.
Standard library server plus static HTML/JS (d3 and fonts vendored), so it runs offline at the venue.

## Launch

```bash
pip install -r requirements.txt          # nothing new is needed for the tour itself
python -m app.tour --open                # http://localhost:8600
```

| Flag | Use |
|---|---|
| `--no-live` | Skip youngcrc.com and use the hash-verified snapshot (flaky Wi-Fi) |
| `--host 0.0.0.0 --port 8600` | Serve from Brev or share on the local network |
| `#ch=3`, `#workspace` | Deep links (append to the URL) |

Keys: left/right arrows move between chapters, **N** toggles presenter notes, Esc closes panels.

### Turn on the live GNN-B run (chapter 5 and the workspace Agent tab)
1. Extract the team data kit into `data/processed/`: `nodes.parquet`, `edges.parquet`, `evidence.parquet`, `feat_text.pt`, `emb_main.pt`.
2. Install `torch`, `torch_geometric`, `sentence-transformers` and `pcst_fast` from source (see `models/retriever.py`).
3. Optional: `NVIDIA_API_KEY=...` in `.env` enables the NIM-written draft.
4. Restart. The server prints `live GNN-B: ready`.

**No assets on the demo laptop?** Run `python -m app.tour.precompute` once on Brev and commit
`cache/demo/youngcrc/*.json`. The tour then offers "Replay saved run", labelled with its timestamp.

## The six chapters

| # | Chapter | What is on screen | Source |
|---|---|---|---|
| 1 | Knowledge graph | 720 real entities, 995 real relations (PrimeKG + DisMech), type filters, a DisMech chain with PMIDs | `cache/demo`, `eval/traces`, `data/primekg_extracts`, `graph/ws1_report.json` |
| 2 | Our GNN | Animated two-layer message passing on real edges; link prediction, E1, E2, E3, E4 with honest readouts | `models/results/*.json`, `eval/results/*` |
| 3 | The patient | 43-event timeline, 12 specimens, 13 source-linked findings, proteomics QC | youngcrc.com release 1.53 |
| 4 | Into the graph | Patient node joins the same network; 86 gene-level measurements as dashed edges | patient data + graph index |
| 5 | Investigation | Real GNN-B retrieval, draft, strict verifier; citation chips light the exact edge | `app/external_reasoner.run_case` |
| 6 | Clinical insight | Observed / graph-derived / not concluded; generated case review (Markdown) | verifier output |

## What is real, and what is never claimed

- **Graph.** With `data/processed/` present, the full merged graph is used. Without it, the index holds every node and edge returned by real `retrieve_subgraph` runs, plus PrimeKG extract rows. Mock IDs (`E9000xx`) are excluded. Extract rows have no edge ID, so they are shown as `kg.csv` rows and never offered as citations.
- **Patient data.** The tour tries the live site and checks every file against the release manifest (byte count and SHA-256). If that fails, it uses `snapshot/youngcrc/`, which is release 1.53 from Alexander Young's public GitHub. The same check is applied, and the UI says which source was used.
- **Model results.** These come from CPTAC ccRCC (kidney) and graph link prediction only, and every chart names its file. E2 states plainly that no GNN variant beats the proteomics baseline. E4 shows the masked result next to the ceiling.
- **No kidney model on this patient.** The kidney-cancer stage model is never run on Alexander Young.
- **No faked runs.** A live run either runs the real retriever and verifier, or refuses and explains why. Saved runs replay only if they are real `gnn_pcst` runs on the full graph, and they are labelled as replays.
- **Specimen dates travel with findings.** Every finding keeps its specimen date, and current-node status stays "not assessed".

## Two-minute pitch path
1. **Intro, then 1.** "Everything medicine knows, as a graph." Click a pink DisMech edge to show its PMID and quote.
2. **2.** Click *Watch message passing*. Show the E1 and E3 bars, then the E2 honesty line, then the scope banner.
3. **3.** Click a specimen diamond. Make the point that his data is real, every file is hash-checked, and specimen dates matter.
4. **4.** The patient node docks into the graph. Click MET.
5. **5.** Run on MET, then click a citation chip so the exact edge lights up. The planted false claim is struck out.
6. **6.** Walk the three columns, then *Generate the case review*. Close on why a clinician would use this, then open the workspace.

## Files
`server.py` (API), `case_data.py` (verified patient data), `graph_index.py` (real graph rows), `results.py`
(committed evaluations), `investigate.py` (wraps `app/external_reasoner.py` unchanged, saves and replays traces,
writes the review), `precompute.py`, `static/` (UI), `snapshot/youngcrc/` (verified fallback data, CC BY 4.0).
Tests: `tests/test_tour.py`.
