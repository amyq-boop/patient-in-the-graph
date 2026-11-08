"""Data layer for the demo. The UI only calls the functions here.

Traces (CONTRACTS.md section 8), in order:
  1. cache/demo/<patient_id>__<qid>.json   real cached agent traces
  2. app/mock_traces/<patient_id>__<qid>.json   curated mock traces (python -m app.make_mock_traces)
Patient panels call agent.tools.dispatch (follows BIOHACK_MOCK); mock patients always use the mocks.
Free-text chat runs the live agent only when BIOHACK_LIVE=1; otherwise it answers from the closest cached run.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.contracts import P  # noqa: E402

MOCK_DIR = Path(__file__).parent / "mock_traces"
LIVE = os.environ.get("BIOHACK_LIVE", "0").lower() in ("1", "true", "yes")


def _dispatch(name: str, args: dict) -> dict:
    from agent import mock_tools
    if "MOCK" in str(args.get("patient_id", "")):
        return mock_tools.dispatch(name, args)
    try:
        from agent.tools import dispatch
        res = dispatch(name, args)
    except Exception as e:  # noqa: BLE001 - a broken backend must not break the demo
        res = {"error": str(e)}
    return res


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def questions() -> list[dict]:
    for path in (P.questions, ROOT / "eval" / "questions.example.jsonl"):
        if path.exists():
            return [q for q in _jsonl(path) if q.get("kind") == "patient"]
    return []


def demo_questions() -> list[dict]:
    qs = questions()
    return ([q for q in qs if q.get("demo")] or qs)[:5]


def _usable(t: dict) -> bool:  # skip placeholder traces such as the contract example (answer "...")
    return bool(t.get("answer", "").strip(" .\n")) and bool(t.get("claims"))


def _trace_files() -> list[Path]:
    return [f for d in (P.demo_cache, MOCK_DIR) if d.exists() for f in sorted(d.glob("*__*.json"))]


CANCERS = {"ccrcc": "Kidney cancer (ccRCC)", "brca": "Breast cancer (BRCA)"}
PRODUCT_MOCK = Path(__file__).parent / "mock_product"
PRODUCT_REAL = ROOT / "cache" / "product"


def _trace_index() -> list[tuple[str, str, str]]:
    """(patient_id, qid, cancer) for every usable trace file; traces without a `cancer` key are ccRCC."""
    out = []
    for f in _trace_files():
        pid, qid = f.stem.split("__", 1)
        try:
            cancer = json.loads(f.read_text()).get("cancer") or "ccrcc"
        except ValueError:
            continue
        out.append((pid, qid, cancer))
    return out


REAL_CLUSTERS = ROOT / "data" / "processed" / "clusters"  # Anikat: clusters_<cancer>.json (models/stratify.py)
REAL_BRIEFS = ROOT / "data" / "processed" / "briefs"  # Anikat: <patient_id>.json (graph/patients/make_briefs.py)
DEMO_FIRST = ["C3L-00448", "C3L-00416", "C3N-01649", "11BR060", "11BR028"]  # demo patients, listed first


def _json(f: Path):
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return None


def _brief_cancers() -> dict[str, str]:
    idx = _json(REAL_BRIEFS / "index.json") or {}
    return {b["patient_id"]: b["cancer"] for b in idx.get("briefs", [])}


def patients(cancer: str = "ccrcc") -> list[str]:
    """Real patients first: anyone with a real trace or a real brief, demo patients at the top; mocks only if no real."""
    traced = {p for p, _, c in _trace_index() if c == cancer}
    briefed = {p for p, c in _brief_cancers().items() if c == cancer}
    real = sorted(p for p in traced | briefed if "MOCK" not in p)
    real = [p for p in DEMO_FIRST if p in real] + [p for p in real if p not in DEMO_FIRST]
    return real or sorted(traced)


def qids(pid: str) -> list[str]:
    """The questions that have a verified run for this patient, in qid order."""
    return sorted({q for p, q, _ in _trace_index() if p == pid})


def stage_score(cancer: str, pid: str) -> dict | None:
    """The GNN's out-of-fold stage score for a kidney patient, read from the committed E2 predictions (read-only).
    The stage model is kidney-only, so breast cancer gets None."""
    if cancer != "ccrcc":
        return None
    f = ROOT / "models" / "results" / "predictions_y_stage.csv"
    try:
        import pandas as pd
        d = pd.read_csv(f)
    except Exception:  # noqa: BLE001 - a missing file just hides the tile
        return None
    g = d[d.model == "gnn"].sort_values("risk").reset_index(drop=True)
    row = g[g.patient_id == pid]
    if row.empty:
        return None
    return {"risk": float(row.risk.iloc[0]), "rank": int(row.index[0]) + 1, "n": len(g), "fold": int(row.fold.iloc[0])}


def _norm_clusters(d: dict) -> dict:
    """Anikat's clusters_<cancer>.json -> the shape the panels read. Cluster numbers shown from 1."""
    k_sel = {x["k"]: x.get("silhouette") for x in d.get("k_selection", [])}
    meth = d.get("method") or {}
    cl = []
    for c in d.get("clusters", []):
        sig = [{"symbol": s["symbol"], "curie": s.get("curie"), "direction": "up", "effect": s.get("mean_diff"),
                "q": s.get("q")} for s in c.get("signature_proteins_up", [])[:4]]
        sig += [{"symbol": s["symbol"], "curie": s.get("curie"), "direction": "down", "effect": s.get("mean_diff"),
                 "q": s.get("q")} for s in c.get("signature_proteins_down", [])[:4]]
        pw = [{"name": p["name"], "curie": p.get("curie"), "edge_ids": p.get("edge_ids", []), "q": p.get("q"),
               "overlap": p.get("overlap"), "pathway_size": p.get("pathway_size")} for p in c.get("signature_pathways", [])]
        cl.append({"cluster": c["cluster"] + 1, "name": c.get("name") or f"Subgroup {c['cluster'] + 1}", "n": c.get("size"),
                   "jaccard": c.get("jaccard_stability"), "signature_proteins": sig, "pathways": pw})
    stab = d.get("stability") or {}
    comp = d.get("comparison_proteomics_vs_gnn") or {}
    return {"cancer": d.get("cancer"), "data_origin": d.get("data_origin", "real"), "execution": d.get("execution"),
            "method": meth.get("pipeline", meth) if isinstance(meth, dict) else meth,
            "features": meth.get("representation", "") if isinstance(meth, dict) else "", "k": d.get("k"),
            "stability_ari": stab.get("ari_mean"), "stability_sd": stab.get("ari_sd"), "silhouette": k_sel.get(d.get("k")),
            "patients": [{"patient_id": a["patient_id"], "cluster": a["cluster"] + 1, "x": a["x"], "y": a["y"],
                          "co_assignment": a.get("co_assignment_rate")}
                         for a in d.get("assignments", [])],
            "clusters": cl, "comparison": comp if "status" not in comp else {"status": comp["status"]},
            "post_hoc": d.get("descriptive_tests_after_clustering"), "k_selection": d.get("k_selection", []), "coordinates": meth.get("coordinates", "")
            if isinstance(meth, dict) else ""}


def _norm_brief(b: dict) -> dict:
    """Anikat's briefs/<pid>.json -> the shape the panels read."""
    pr = b.get("proteins") or {}
    tag = lambda xs, key="source_id": [{**x, "id": x.get(key)} for x in xs]  # noqa: E731
    return {"cancer": b.get("cancer"), "patient_id": b.get("patient_id"), "data_origin": b.get("data_origin", "real"),
            "summary": b.get("clinical_summary", ""), "reference": pr.get("reference"),
            "proteins_up": tag(pr.get("top_up", [])[:10]), "proteins_down": tag(pr.get("top_down", [])[:4]),
            "mutations": tag(b.get("mutations", [])), "findings": tag(b.get("phenotypes", [])),
            "missing": b.get("missing_information", []), "medications": b.get("medications", []),
            "demographics": b.get("demographics", {})}


def product(cancer: str, name: str, mock_only: bool = False) -> tuple[dict | None, str]:
    """A product file: 'clusters.json' or 'briefs/<pid>.json'. Anikat's real files first, then cache/product/, then the
    mocks in app/mock_product/. Returns (data in the panels' shape, origin 'real' | 'mock' | 'missing')."""
    if mock_only:  # a mock patient is shown with mock cohort files only, never mixed with real ones
        d = _json(PRODUCT_MOCK / cancer / name)
        return (d, "mock") if d else (None, "missing")
    if name == "clusters.json" and (d := _json(REAL_CLUSTERS / f"clusters_{cancer}.json")):
        return _norm_clusters(d), d.get("data_origin", "real")
    if name.startswith("briefs/") and (d := _json(REAL_BRIEFS / name.split("/", 1)[1])) and d.get("cancer") == cancer:
        return _norm_brief(d), d.get("data_origin", "real")
    for root, origin in ((PRODUCT_REAL, "real"), (PRODUCT_MOCK, "mock")):
        if (d := _json(root / cancer / name)):
            return d, d.get("data_origin", origin)
    return None, "missing"


def _from_traces(pid: str, tool: str) -> dict | None:
    """Reuse a tool result saved in a cached trace when the live backend can't answer (e.g. no data on this machine)."""
    for f in _trace_files():
        if f.name.startswith(f"{pid}__"):
            for tc in json.loads(f.read_text()).get("tool_calls", []):
                r = tc.get("result")
                if tc.get("name") == tool and isinstance(r, dict) and "error" not in r:
                    return r
    return None


def patient(pid: str) -> dict:
    out = {}
    for key, tool in (("info", "get_patient"), ("pred", "predict_patient")):
        res = _dispatch(tool, {"patient_id": pid})
        out[key] = res if "error" not in res else (_from_traces(pid, tool) or res)
    return out


MOCK_ID = re.compile(r"\bE9000\d\d\b|PMID:0{6,}\d")


def _is_mock(t: dict) -> bool:
    """Mock by content, not by folder: declared mock, a mock or fake model, mock tool results, or mock-range IDs."""
    if t.get("data_origin") == "mock" or str(t.get("model", "")).lower() in ("mock", "fake-llm"):
        return True
    if any(isinstance(tc.get("result"), dict) and (tc["result"].get("meta") or {}).get("mock") for tc in t.get("tool_calls", [])):
        return True
    return bool(MOCK_ID.search(json.dumps(t.get("evidence", {}))))


def _belongs_to(t: dict, pid: str) -> bool:
    """Every patient-level tool result in the trace must be about this patient."""
    for tc in t.get("tool_calls", []):
        r = tc.get("result")
        if tc.get("name") in ("get_patient", "predict_patient", "rank_for_patient") and isinstance(r, dict) \
                and r.get("patient_id") not in (None, pid):
            return False
    return t.get("patient_id") in (None, pid)


def trace(pid: str, qid: str) -> tuple[dict | None, str]:
    """Returns (trace, source): source is 'cached', 'mock' or 'missing'. A trace whose tool results are about a
    different patient is never shown."""
    for folder in (P.demo_cache, MOCK_DIR):
        f = folder / f"{pid}__{qid}.json"
        if f.exists():
            t = json.loads(f.read_text())
            if _usable(t) and _belongs_to(t, pid):
                return t, "mock" if _is_mock(t) or folder == MOCK_DIR else "cached"
    return None, "missing"


def _words(s: str) -> set[str]:
    stop = {"the", "a", "an", "is", "this", "of", "and", "or", "to", "what", "which", "why", "how", "does",
            "do", "are", "in", "for", "would", "you", "patient", "patient's", "it", "its"}
    return {w for w in re.findall(r"[a-z0-9\-']+", s.lower()) if w not in stop}


def cached_questions(pid: str) -> list[dict]:
    """[{qid, question}] for every verified run of this patient, worded as the run itself records it."""
    out = []
    for q in qids(pid):
        t, _ = trace(pid, q)
        if t and t.get("question"):
            out.append({"qid": q, "question": t["question"]})
    return out


def closest_cached(pid: str, text: str, threshold: float = 0.25) -> dict | None:
    """The verified run for this patient whose question best matches `text` (word-overlap Jaccard, hyphenated words
    split). Only cached runs are candidates, so a match can always be replayed. None if nothing is close enough."""
    # split hyphenated words ("drug-driven" -> drug, driven) and compare 5-letter stems (drives / drivers, target / targets)
    split = lambda ws: {p.replace("'s", "")[:5] for w in ws for p in w.split("-") if p}  # noqa: E731
    a, best, score = split(_words(text)), None, 0.0
    for q in cached_questions(pid):
        b = split(_words(q["question"]))
        s = len(a & b) / max(len(a | b), 1)
        if s > score:
            best, score = q, s
    return {**best, "score": round(score, 2)} if best and score >= threshold else None


def chat(pid: str, text: str) -> dict:
    """Reply for the side chat: {'mode': live|cached|none, 'trace', 'qid'}."""
    if LIVE:
        from agent.graph_agent import run
        return {"mode": "live", "trace": run(text, patient_id=pid, qid="chat"), "qid": None}
    best, score = None, 0.0
    for q in questions():
        a, b = _words(text), _words(q["question"])
        s = len(a & b) / max(len(a | b), 1)
        if s > score:
            best, score = q, s
    if best and score >= 0.25:
        t, _ = trace(pid, best["qid"])
        if t:
            return {"mode": "cached", "trace": t, "qid": best["qid"]}
    return {"mode": "none", "trace": None, "qid": None}


def results() -> dict:
    import pandas as pd
    out = {}
    for name in ("e1", "e2", "e3"):
        f = P.results / f"{name}.csv"
        out[name] = pd.read_csv(f) if f.exists() else None
    for name, f in (("e4", P.results / "e4.json"), ("e4_key", ROOT / "eval" / "e4_answer_key.json")):
        out[name] = json.loads(f.read_text()) if f.exists() else None
    return out
