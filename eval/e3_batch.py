"""E3 batch runner: every question through the agent and through the same LLM with no tools.

    python -m eval.e3_batch                         # 20 questions x {agent, llm_only}, 3 at a time
    python -m eval.e3_batch --mode llm_only         # just the baseline (doesn't depend on the agent prompt)
    python -m eval.e3_batch --only P01 D02 --workers 2
    python -m eval.e3_batch --reverify              # re-score saved drafts with the current verifier
    python -m eval.e3_grounded eval/traces/e3_agent eval/traces/e3_llm_only --sample 50

Traces go to eval/traces/<run_id>_<mode>/<qid>.json (run_id defaults to "e3"), the shape
agent.run writes, so eval/e3_grounded.py and common.validate read them unchanged.

Resumable: a question whose trace already exists is skipped (use --force to redo it), so an
interrupted run picks up where it stopped. Failures go to <dir>/_errors.jsonl, not the trace dir.

--reverify re-runs only the last step (claim split + support check) on each saved draft and
writes <run_id>_<mode>_rv/. The agent's tool calls and draft are reused untouched, so a verifier
change costs one claims call per question (often a cache hit) instead of re-running the agent.

Each output dir gets _manifest.jsonl (one JSON line, git commit, model, variant, file hashes) so agent and
baseline can be checked to come from the same frozen setup (eval/README.md, scoring rule 4).
"""
import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from agent import config as C
from agent import graph_agent
from agent.llm import get_llm
from agent.trace_check import check
from agent.verifier import verify

QUESTIONS = C.ROOT / "eval" / "questions.jsonl"
HASHED = ["agent/prompts.py", "agent/verifier.py", "agent/graph_agent.py", "agent/tools.py", "agent/schemas.py"]
_print_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def sha(path):
    p = C.ROOT / path if not Path(path).is_absolute() else Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12] if p.exists() else None


def git(*args):
    try:
        env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")  # read-only: never leave .git/index.lock behind
        return subprocess.run(["git", *args], cwd=C.ROOT, capture_output=True, text=True, env=env,
                              timeout=20).stdout.strip()
    except Exception:
        return None


def manifest(questions_path, extra=None):
    return {
        "created": datetime.now().isoformat(timespec="seconds"),
        "git_commit": git("rev-parse", "--short", "HEAD"),
        "git_dirty_files": [l[3:] for l in (git("status", "--porcelain", "--untracked-files=no") or "").splitlines()],
        "model": C.NIM_MODEL if os.environ.get("BIOHACK_FAKE_LLM") != "1" else "fake-llm",
        "variant": C.VARIANT,
        "mock_mode": C.MOCK_MODE,
        "questions": str(Path(questions_path).resolve().relative_to(C.ROOT)) if C.ROOT in Path(questions_path).resolve().parents else str(questions_path),
        "questions_sha": sha(questions_path),
        "file_sha": {f: sha(f) for f in HASHED},
        **(extra or {}),
    }


def write_json(path, obj, indent=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, indent=indent, default=str, ensure_ascii=False) + ("\n" if indent is None else ""),
                   encoding="utf-8")
    os.replace(tmp, path)


def write_manifest(d, obj):
    # .jsonl, not .json: common.validate and e3_grounded read every *.json in a trace dir as a trace
    write_json(d / "_manifest.jsonl", obj, indent=None)


def append_error(d, rec):
    d.mkdir(parents=True, exist_ok=True)
    with _print_lock, open(d / "_errors.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def done(path):
    """A finished trace: parses, has verifier stats, passes the agent's trace check."""
    try:
        t = json.loads(path.read_text(encoding="utf-8"))
        return "verifier" in t and not check(t)
    except Exception:
        return False


def load_questions(path, only=None):
    qs = [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]
    if only:
        want = set(only)
        missing = want - {q["qid"] for q in qs}
        if missing:
            sys.exit(f"unknown qids: {sorted(missing)}")
        qs = [q for q in qs if q["qid"] in want]
    return qs


def warm_up(qs):
    """Load the lru-cached tables / GNN once in the main thread, before the workers race to."""
    from agent.tools import dispatch
    pid = next((q["patient_id"] for q in qs if q.get("patient_id")), None)
    calls = [("get_mechanism", {"disease_curie": C.CCRCC_ID}), ("resolve_terms", {"texts": ["hematuria"]})]
    if pid:
        calls = [("get_patient", {"patient_id": pid}), ("predict_patient", {"patient_id": pid}),
                 ("rank_for_patient", {"patient_id": pid, "node_type": "gene", "k": 3}),
                 ("retrieve_subgraph", {"question": "warm-up", "seeds": [f"PATIENT:{pid}"], "budget": 10})] + calls
    t = time.perf_counter()
    for name, args in calls:
        dispatch(name, args)
    log(f"warm-up: {len(calls)} tool calls in {time.perf_counter() - t:.1f}s")


def run_one(q, mode, out_dir, run_id):
    llm = get_llm()  # one client per job: NimLLM keeps per-run state (truncation flags)
    t0 = time.perf_counter()
    trace = graph_agent.run(q["question"], patient_id=q.get("patient_id"), qid=q["qid"], mode=mode,
                            llm=llm, run_id=run_id)
    trace["e3_batch"] = {"kind": q.get("kind"), "trap": bool(q.get("trap")), "category": q.get("category")}
    write_json(out_dir / f"{q['qid']}.json", trace)
    return trace, time.perf_counter() - t0


def batch(qs, modes, run_id, root, workers, force, questions_path):
    jobs = []
    for mode in modes:
        d = root / f"{run_id}_{mode}"
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "_manifest.jsonl").exists() or force:
            write_manifest(d, manifest(questions_path, {"mode": mode, "run_id": f"{run_id}_{mode}"}))
        for q in qs:
            p = d / f"{q['qid']}.json"
            if not force and done(p):
                log(f"skip  [{mode}] {q['qid']} (already done)")
                continue
            jobs.append((q, mode, d))
    if not jobs:
        log("nothing to run")
        return 0
    if any(m == "agent" for _, m, _ in jobs):
        warm_up(qs)
    log(f"running {len(jobs)} job(s) with {workers} worker(s)")
    failures, t_all = 0, time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_one, q, m, d, f"{run_id}_{m}"): (q, m, d) for q, m, d in jobs}
        for i, f in enumerate(as_completed(futs), 1):
            q, m, d = futs[f]
            try:
                t, secs = f.result()
                v = t["verifier"]
                flags = [n for n in t.get("notes", []) if n.startswith(("MOCK", "LLM output cut off"))]
                probs = check(t)
                log(f"[{i}/{len(jobs)}] {m:8s} {q['qid']}  claims {v['n_claims']:3d}  supported {v['n_supported']:3d}  "
                    f"hyp {v['n_hypothesis']:2d}  removed {v['n_removed']:2d}  {secs:6.0f}s"
                    + (f"  !! {'; '.join(flags)}" if flags else "") + (f"  !! trace check: {probs}" if probs else ""))
            except Exception as e:
                failures += 1
                append_error(d, {"qid": q["qid"], "mode": m, "time": datetime.now().isoformat(timespec="seconds"),
                                 "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()})
                log(f"[{i}/{len(jobs)}] {m:8s} {q['qid']}  FAILED: {type(e).__name__}: {str(e)[:150]}  (see {d.name}/_errors.jsonl)")
    log(f"done in {(time.perf_counter() - t_all) / 60:.1f} min, {failures} failure(s). Rerun the same command to retry failures.")
    return failures


def reverify_one(path, out_dir):
    t = json.loads(path.read_text(encoding="utf-8"))
    llm = get_llm()
    calls = t.get("tool_calls", [])
    evidence = graph_agent._evidence(calls) if t["mode"] == "agent" else {"edge_ids": [], "pmids": []}
    claims, answer, stats, splitter = verify(t["draft"], evidence, llm, edge_names=graph_agent._edge_names(calls))
    new = copy.deepcopy(t)
    new.update(evidence=evidence, claims=claims, answer=answer, verifier=stats, verifier_splitter=splitter)
    new["run_id"] = out_dir.name
    new["reverified"] = {"from": str(path.relative_to(C.ROOT)) if C.ROOT in path.resolve().parents else str(path),
                         "at": datetime.now().isoformat(timespec="seconds"),
                         "verifier_sha": sha("agent/verifier.py"), "previous_verifier": t["verifier"]}
    write_json(out_dir / path.name, new)
    return t["verifier"], stats


def reverify(run_id, modes, root, workers, questions_path):
    jobs = []
    for mode in modes:
        src = root / f"{run_id}_{mode}"
        if not src.exists():
            log(f"no traces at {src}")
            continue
        dst = root / f"{run_id}_{mode}_rv"
        write_manifest(dst, manifest(questions_path, {"mode": mode, "reverified_from": src.name}))
        jobs += [(p, dst) for p in sorted(src.glob("*.json")) if not p.name.startswith("_")]
    log(f"re-verifying {len(jobs)} trace(s)")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(reverify_one, p, d): (p, d) for p, d in jobs}
        for f in as_completed(futs):
            p, d = futs[f]
            try:
                old, new = f.result()
                log(f"{d.name}/{p.name}: supported {old['n_supported']}/{old['n_claims']} -> "
                    f"{new['n_supported']}/{new['n_claims']}, removed {old['n_removed']} -> {new['n_removed']}")
            except Exception as e:
                append_error(d, {"file": p.name, "error": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()})
                log(f"{p.name}: FAILED {type(e).__name__}: {str(e)[:150]}")


def main(argv=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--questions", default=str(QUESTIONS))
    ap.add_argument("--only", nargs="*", help="qids to run")
    ap.add_argument("--mode", choices=["agent", "llm_only", "both"], default="both")
    ap.add_argument("--run-id", default="e3")
    ap.add_argument("--workers", type=int, default=3, help="parallel questions (NIM rate limits: keep this small)")
    ap.add_argument("--force", action="store_true", help="redo questions that already have a trace")
    ap.add_argument("--reverify", action="store_true", help="re-score saved drafts with the current verifier")
    ap.add_argument("--traces-dir", default=str(C.TRACES))
    ap.add_argument("--allow-mock", action="store_true", help="allow BIOHACK_MOCK=1 (tests only; E3 must use real data)")
    a = ap.parse_args(argv)

    if C.MOCK_MODE in ("1", "true", "yes") and not a.allow_mock:
        sys.exit("BIOHACK_MOCK=1: E3 must run on real data. Unset it (auto uses real files) or pass --allow-mock.")
    modes = ["agent", "llm_only"] if a.mode == "both" else [a.mode]
    root = Path(a.traces_dir)
    if a.reverify:
        reverify(a.run_id, modes, root, a.workers, a.questions)
        return 0
    qs = load_questions(a.questions, a.only)
    log(f"E3: {len(qs)} question(s) x {modes}  model={C.NIM_MODEL}  variant={C.VARIANT}  -> {root}/{a.run_id}_<mode>/")
    return 1 if batch(qs, modes, a.run_id, root, a.workers, a.force, a.questions) else 0


if __name__ == "__main__":
    sys.exit(main())
