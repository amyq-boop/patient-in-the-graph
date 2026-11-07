"""E3: is the agent grounded? Aggregates verifier counts from traces into eval/results/e3.csv
(CONTRACTS.md §8: mode, qid, n_claims, n_supported, n_hypothesis, n_removed; plus
n_supported_entity_match as an extra column).

    python -m eval.e3_grounded eval/traces/e3_agent eval/traces/e3_llm_only
    python -m eval.e3_grounded --sample 50 ...   # also write a hand-check sheet of random claims
"""
import argparse
import csv
import json
import random
from pathlib import Path

from agent import config as C

COLS = ["mode", "qid", "n_claims", "n_supported", "n_hypothesis", "n_removed", "n_supported_entity_match"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--out", default=str(C.RESULTS / "e3.csv"))
    ap.add_argument("--sample", type=int, default=0, help="claims to sample for the human hand-check")
    a = ap.parse_args(argv)

    rows, claims = [], []
    for d in a.dirs:
        for f in sorted(Path(d).glob("*.json")):
            t = json.loads(f.read_text())
            rows.append({"mode": t["mode"], "qid": t["qid"], **{k: t["verifier"].get(k, "") for k in COLS[2:]}})
            claims += [{"mode": t["mode"], "qid": t["qid"], "claim_id": c["claim_id"], "text": c["text"],
                        "cited": " ".join(c["support"]["edge_ids"] + c["support"]["pmids"]),
                        "entity_match": c.get("entity_match"),
                        "verifier_status": c["status"], "human_status": ""} for c in t["claims"]]

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out} ({len(rows)} rows)")

    for mode in sorted({r["mode"] for r in rows}):
        rs = [r for r in rows if r["mode"] == mode]
        n = sum(r["n_claims"] for r in rs) or 1
        print(f"  {mode:9s} supported {sum(r['n_supported'] for r in rs) / n:6.1%}   "
              f"hypothesis {sum(r['n_hypothesis'] for r in rs) / n:6.1%}   "
              f"removed per answer {sum(r['n_removed'] for r in rs) / max(len(rs), 1):.2f}   ({len(rs)} answers)")

    if a.sample and claims:
        random.Random(42).shuffle(claims)
        hc = out.with_name("e3_handcheck.csv")
        with open(hc, "w", newline="") as f:
            w = csv.DictWriter(f, list(claims[0]))
            w.writeheader()
            w.writerows(claims[: a.sample])
        print(f"wrote {hc}: fill human_status (supported / unsupported) for each claim")


if __name__ == "__main__":
    main()
