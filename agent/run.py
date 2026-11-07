"""Run the agent from the command line and save traces.

One question:
    python -m agent.run -q "Why is this patient high-risk, and what would you target?" -p C3L-00004

The eval set (E3: agent vs the same LLM with no tools):
    python -m agent.run --questions eval/questions.jsonl --mode both

Demo cache (3 patients x 5 questions, replayed by the app if the network fails):
    python -m agent.run --questions eval/questions.jsonl --demo-patients C3L-00004 C3L-00010 C3L-00026

E4 live demo: set BIOHACK_VARIANT=e4 (or e4_dismech) first.
Offline smoke test: BIOHACK_MOCK=1 BIOHACK_FAKE_LLM=1 python -m agent.run -q "..." -p C3L-MOCK1
"""
import argparse
import json
import sys
from datetime import datetime

from agent import config as C
from agent.graph_agent import run
from agent.llm import get_llm
from agent.trace_check import check


def save(trace, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trace, indent=1, default=str))
    probs = check(trace)
    if probs:
        print(f"  ! trace check: {probs}", file=sys.stderr)
    return path


def load_questions(path):
    with open(path) as f:
        return [json.loads(l) for l in f if l.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-q", "--question")
    ap.add_argument("-p", "--patient")
    ap.add_argument("--qid", default="adhoc")
    ap.add_argument("--questions", help="eval/questions.jsonl")
    ap.add_argument("--only", nargs="*", help="qids to run from --questions")
    ap.add_argument("--mode", choices=["agent", "llm_only", "both"], default="agent")
    ap.add_argument("--demo-patients", nargs="*", help="write cache/demo/<pid>__<qid>.json for patient questions")
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
        pq = [j for j in jobs if j.get("kind") == "patient"]
        for pid in a.demo_patients:
            for j in pq[:5]:
                t = run(j["question"], patient_id=pid, qid=j["qid"], mode="agent", llm=llm, run_id=f"{stamp}_demo")
                p = save(t, C.DEMO_CACHE / f"{pid}__{j['qid']}.json")
                print(f"{p}  {t['verifier']}")
        return

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


if __name__ == "__main__":
    main()
