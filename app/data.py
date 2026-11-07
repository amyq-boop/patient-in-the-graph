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


def patients() -> list[str]:
    pids = sorted({f.name.split("__")[0] for f in _trace_files()})
    real = [p for p in pids if "MOCK" not in p]
    return real or pids or ["C3L-MOCK1"]


def patient(pid: str) -> dict:
    return {"info": _dispatch("get_patient", {"patient_id": pid}),
            "pred": _dispatch("predict_patient", {"patient_id": pid})}


def trace(pid: str, qid: str) -> tuple[dict | None, str]:
    """Returns (trace, source): source is 'cached', 'mock' or 'missing'."""
    for folder, source in ((P.demo_cache, "cached"), (MOCK_DIR, "mock")):
        f = folder / f"{pid}__{qid}.json"
        if f.exists():
            t = json.loads(f.read_text())
            if _usable(t):
                return t, source
    return None, "missing"


def _words(s: str) -> set[str]:
    stop = {"the", "a", "an", "is", "this", "of", "and", "or", "to", "what", "which", "why", "how", "does",
            "do", "are", "in", "for", "would", "you", "patient", "patient's", "it", "its"}
    return {w for w in re.findall(r"[a-z0-9\-']+", s.lower()) if w not in stop}


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
