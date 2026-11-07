"""Stricter trace check than common/validate.py (CONTRACTS.md §8). Returns a list of problems.

    python -m agent.trace_check eval/traces/<run_id>/*.json
"""
import json
import sys

REQUIRED = ["contract_version", "run_id", "qid", "mode", "patient_id", "question", "model",
            "tool_calls", "evidence", "draft", "claims", "answer", "verifier"]
STATUSES = {"supported", "hypothesis", "removed"}


def check(t):
    p = [f"missing key {k}" for k in REQUIRED if k not in t]
    if p:
        return p
    if t["mode"] not in ("agent", "llm_only"):
        p.append(f"bad mode {t['mode']}")
    if t["mode"] == "llm_only" and (t["tool_calls"] or t["evidence"]["edge_ids"] or t["evidence"]["pmids"]):
        p.append("llm_only trace has tool calls or evidence")
    for tc in t["tool_calls"]:
        if not {"name", "args", "result", "ms"} <= set(tc):
            p.append(f"tool call missing keys: {tc.get('name')}")
    ev_e, ev_p = set(t["evidence"]["edge_ids"]), set(t["evidence"]["pmids"])
    strict = t["verifier"].get("strict", False)
    for c in t["claims"]:
        if c.get("status") not in STATUSES:
            p.append(f"{c.get('claim_id')}: bad status {c.get('status')}")
        s = c.get("support", {})
        ok = bool(set(s.get("edge_ids", [])) & ev_e or set(s.get("pmids", [])) & ev_p)
        if c.get("status") == "supported" and not ok:
            p.append(f"{c.get('claim_id')}: marked supported without an evidence edge or PMID")
        if ok and c.get("status") != "supported" and not strict:
            p.append(f"{c.get('claim_id')}: has evidence but is {c.get('status')}")
    v = t["verifier"]
    for k, st in [("n_supported", "supported"), ("n_hypothesis", "hypothesis"), ("n_removed", "removed")]:
        if v.get(k) != sum(c["status"] == st for c in t["claims"]):
            p.append(f"verifier.{k} does not match claims")
    if v.get("n_claims") != len(t["claims"]):
        p.append("verifier.n_claims does not match claims")
    return p


if __name__ == "__main__":
    bad = 0
    for f in sys.argv[1:]:
        probs = check(json.load(open(f)))
        bad += bool(probs)
        print(("OK   " if not probs else "FAIL ") + f + "".join(f"\n     - {x}" for x in probs))
    sys.exit(1 if bad else 0)
