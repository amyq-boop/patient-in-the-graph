"""Data layer for the demo. The UI only calls the functions here.

Sources, in order:
  1. cache/demo/<patient_id>__<qid>.json   real cached agent traces (committed, CONTRACTS.md section 8)
  2. app/mock_traces/<patient_id>__<qid>.json   offline traces from the agent in fake mode (python -m app.make_mock_traces)
Patient panels call the agent's tools through agent.tools.dispatch, so they follow BIOHACK_MOCK like everything else.
Live questions run the real agent only when BIOHACK_LIVE=1.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.contracts import P  # noqa: E402

MOCK_DIR = Path(__file__).parent / "mock_traces"
LIVE = os.environ.get("BIOHACK_LIVE", "0") in ("1", "true", "yes")


def _dispatch(name: str, args: dict) -> dict:
    try:
        from agent.tools import dispatch
    except Exception:  # noqa: BLE001 - fall back to mocks if the real stack can't import here
        from agent.mock_tools import dispatch
    return dispatch(name, args)


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def demo_questions() -> list[dict]:
    """The 5 demo questions (P01-P05 in eval/questions.jsonl)."""
    for path in (P.questions, ROOT / "eval" / "questions.example.jsonl"):
        if path.exists():
            qs = [q for q in _jsonl(path) if q.get("kind") == "patient"]
            demo = [q for q in qs if q.get("demo")]
            return (demo or qs)[:5]
    return []


def _trace_files() -> list[Path]:
    return [f for d in (P.demo_cache, MOCK_DIR) if d.exists() for f in sorted(d.glob("*__*.json"))]


def patients() -> list[str]:
    pids = sorted({f.name.split("__")[0] for f in _trace_files()})
    real = [p for p in pids if "MOCK" not in p]
    if real:  # once real cached traces exist, hide the mock patients
        return real
    if pids:
        return pids
    from agent.mock_tools import PATIENTS
    return list(PATIENTS)


def patient(pid: str) -> dict:
    return {"info": _dispatch("get_patient", {"patient_id": pid}),
            "pred": _dispatch("predict_patient", {"patient_id": pid})}


def trace(pid: str, qid: str) -> tuple[dict | None, str]:
    """Returns (trace, source) where source is 'cached', 'mock' or 'missing'."""
    for folder, source in ((P.demo_cache, "cached"), (MOCK_DIR, "mock")):
        f = folder / f"{pid}__{qid}.json"
        if f.exists():
            return json.loads(f.read_text()), source
    return None, "missing"


def ask_live(pid: str, question: str) -> dict | None:
    if not LIVE:
        return None
    from agent.graph_agent import run
    return run(question, patient_id=pid, qid="live")


def results() -> dict:
    """Whatever eval results exist; missing ones are None."""
    import pandas as pd
    out = {}
    for name in ("e1", "e2", "e3"):
        f = P.results / f"{name}.csv"
        out[name] = pd.read_csv(f) if f.exists() else None
    f = P.results / "e4.json"
    out["e4"] = json.loads(f.read_text()) if f.exists() else None
    key = ROOT / "eval" / "e4_answer_key.json"
    out["e4_key"] = json.loads(key.read_text()) if key.exists() else None
    return out
