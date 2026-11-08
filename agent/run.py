"""Run the agent from the command line and save traces.

One question:
    python -m agent.run -q "Why is this patient high-risk, and what would you target?" -p C3L-00004

The eval set (E3: agent vs the same LLM with no tools):
    python -m agent.run --questions eval/questions.jsonl --mode both

Demo cache (3 patients x the 5 "demo": true questions, replayed by the app if the network fails). Use the
same verifier settings as E3, so the demo shows the verifier the numbers come from:
    BIOHACK_MOCK=0 VERIFIER_STRICT=1 VERIFIER_SPLITTER=rules \
        python -m agent.run --questions eval/questions.jsonl --demo-patients C3N-01649 C3L-00416 C3L-00448
It writes cache/demo/<pid>__<qid>.json, each with a "run_info" block (commit, dirty files, settings), plus
cache/demo/_manifest.jsonl for the run as a whole (.jsonl, so neither the app nor common.validate reads it as a trace).

E4 live demo: set BIOHACK_VARIANT=e4 (or e4_dismech) first.
Offline smoke test: BIOHACK_MOCK=1 BIOHACK_FAKE_LLM=1 python -m agent.run -q "..." -p C3L-MOCK1
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

from agent import config as C
from agent.graph_agent import run
from agent.llm import get_llm
from agent.trace_check import check


def save(trace, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace, indent=1, default=str), encoding="utf-8")
    probs = check(trace)
    if probs:
        print(f"  ! trace check: {probs}", file=sys.stderr)
    return path


def load_questions(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):  # Windows consoles / Git Bash default to cp1252
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--question")
    ap.add_argument("-p", "--patient")
    ap.add_argument("--qid", default="adhoc")
    ap.add_argument("--questions", help="eval/questions.jsonl")
    ap.add_argument("--only", nargs="*", help="qids to run from --questions")
    ap.add_argument("--mode", choices=["agent", "llm_only", "both"], default="agent")
    ap.add_argument("--demo-patients", nargs="*", help="write cache/demo/<pid>__<qid>.json for the demo questions")
    ap.add_argument("--allow-mock", action="store_true", help="allow BIOHACK_MOCK=1 for --demo-patients (tests only)")
    ap.add_argument("--run-id")
    ap.add_argument("--model")
    a = ap.parse_args(argv)

    llm = get_llm(a.model)
    stamp = a.run_id or datetime.now().strftime("%Y-%m-%dT%H-%M")
    modes = ["agent", "llm_only"] if a.mode == "both" else [a.mode]

    if a.question:
        jobs = [{"qid": a.qid, "question": a.question, "patient_id": a.patient}]
    elif a.questions:
        jobs = load_questions(a.questions)
        if a.only:
            jobs = [j for j in jobs if j["qid"] in set(a.only)]
    else:
        ap.error("give -q or --questions")

    if a.demo_patients:
        return demo(a, jobs, llm, stamp)

    for mode in modes:
        run_id = f"{stamp}_{mode}"
        for j in jobs:
            t = run(j["question"], patient_id=j.get("patient_id") or a.patient, qid=j["qid"], mode=mode,
                    llm=llm, run_id=run_id)
            p = save(t, C.TRACES / run_id / f"{j['qid']}.json")
            print(f"[{mode}] {j['qid']}  {t['verifier']}  {t['elapsed_s']}s  -> {p.relative_to(C.ROOT)}")
            for n in t["notes"]:
                print(f"    note: {n}")
            if a.question:
                print("\n" + t["answer"])


def demo(a, jobs, llm, stamp):
    """3 patients x the demo questions -> cache/demo/, with a run record the app and reviewers can trust."""
    from eval.e3_batch import manifest, write_manifest  # same record as the E3 runs

    if C.MOCK_MODE in ("1", "true", "yes") and not a.allow_mock:
        sys.exit("BIOHACK_MOCK=1: the app shows cache/demo/ as real runs. Unset it (auto uses real files) or pass --allow-mock.")
    pq = [j for j in jobs if j.get("kind") == "patient"]
    qs = [j for j in pq if j.get("demo")] or pq[:5]
    if (os.environ.get("VERIFIER_STRICT"), os.environ.get("VERIFIER_SPLITTER", "llm")) != ("1", "rules"):
        print("  ! note: E3 used VERIFIER_STRICT=1 VERIFIER_SPLITTER=rules; this demo run uses different verifier settings",
              file=sys.stderr)
    run_id = f"{stamp}_demo"
    m = manifest(a.questions or "eval/questions.jsonl",
                 {"mode": "agent", "run_id": run_id, "demo_patients": a.demo_patients, "qids": [j["qid"] for j in qs]})
    write_manifest(C.DEMO_CACHE, m)
    info = {k: m[k] for k in ("run_id", "created", "git_commit", "git_dirty_files", "settings", "model", "variant")}
    total, i, t0 = len(a.demo_patients) * len(qs), 0, time.perf_counter()
    print(f"demo: {len(a.demo_patients)} patient(s) x {[j['qid'] for j in qs]} -> {C.DEMO_CACHE}  (commit {m['git_commit']})")
    for pid in a.demo_patients:
        for j in qs:
            i += 1
            t = run(j["question"], patient_id=pid, qid=j["qid"], mode="agent", llm=llm, run_id=run_id)
            t["run_info"] = info
            p = save(t, C.DEMO_CACHE / f"{pid}__{j['qid']}.json")
            v = t["verifier"]
            flags = [n for n in t.get("notes", []) if n.startswith(("MOCK", "LLM output cut off", "tool error"))]
            print(f"[{i}/{total}] {pid} {j['qid']}  supported {v['n_supported']}/{v['n_claims']}  removed {v['n_removed']}"
                  f"  {t['elapsed_s']:.0f}s" + (f"  !! {'; '.join(flags)}" if flags else ""))
    print(f"done in {(time.perf_counter() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
