"""E4, exploratory re-score: weighted credit for the HIF stabilization step by isoform.

Added Nov 8 at the bio lead's (Aniket's) request, after the original E4 scoring. It does not change
eval/e4_answer_key.json, eval/e4_cohort.py or the original results; it re-reads the saved traces and writes its own files.

Rule: the HIF stabilization step is scored over the same claims eval/e4_cohort.score_trace scans (verifier-SUPPORTED,
citing an observed ID; the KG column also needs a graph edge or PMID), but HIF-1alpha now counts too. Its credit becomes
    HIF-1alpha named            -> 0.7
    HIF-2alpha named            -> 0.3
    both named                  -> 1.0   (0.7 + 0.3)
    only generic "HIF stabilization" -> 1.0  (as in the pre-registered key)
Every other step keeps its pre-registered 0/1 credit. Weights can be changed with --hif1 / --hif2.

    python -m eval.e4_hif_weighted        # -> eval/results/e4_hif_weighted.{json,csv}
"""
import argparse
import csv
import datetime as dt
import functools
import json
from pathlib import Path

import pandas as pd

from eval import e4_case_study as E4
from eval import e4_cohort as C

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "eval" / "results"
STEP = "HIF-2alpha Stabilization"
HIF1_TERMS = ["HIF-1alpha", "HIF1A", "HIF1alpha", "HIF-1"]
HIF2_TERMS = ["HIF-2alpha", "HIF2A", "HIF2alpha", "HIF-2", "EPAS1", STEP]
GENERIC_TERMS = ["HIF stabilization"]
RUNS = [  # (name, plan, trace dir, ceiling, pre-registered results file)
    ("first_cohort_hidden", "eval/e4_cohort_plan.json", "eval/traces/e4_cohort_rv", False, "e4_cohort_rv.json"),
    ("replication_cohort_hidden", "eval/e4_cohort_plan_b.json", "eval/traces/e4_cohort_b_rv", False, "e4_cohort_b_rv.json"),
    ("first_cohort_full_graph", "eval/e4_cohort_plan.json", "eval/traces/e4_cohort_full_rv", True, "e4_cohort_full_rv.json"),
]


def _cache_parquet():
    """audit_graph re-reads the edge table for every trace; read it once."""
    orig = pd.read_parquet

    @functools.lru_cache(maxsize=4)
    def cached(path, columns):
        return orig(path, columns=list(columns) if columns else None)

    pd.read_parquet = lambda path, columns=None, **kw: cached(str(path), tuple(columns) if columns else None).copy()


def hif_credit(texts, w1, w2):
    m = lambda terms: any(C.mentions(t, terms) for t in texts)
    h1, h2, g = m(HIF1_TERMS), m(HIF2_TERMS), m(GENERIC_TERMS)
    if h1 or h2:
        return min(1.0, w1 * h1 + w2 * h2), ("both" if h1 and h2 else "hif1" if h1 else "hif2")
    return (1.0, "generic") if g else (0.0, "none")


def score_run(name, plan_path, trace_dir, ceiling, prereg_file, key, w1, w2):
    plan = json.loads((ROOT / plan_path).read_text(encoding="utf-8"))
    rows, invalid = [], []
    for p in plan["patients"]:
        f = ROOT / trace_dir / f"{p['patient_id']}.json"
        if not f.exists():
            invalid.append({"patient_id": p["patient_id"], "reason": "missing trace"})
            continue
        t = json.loads(f.read_text(encoding="utf-8"))
        t.setdefault("e4_cohort", {"group": p["group"]})
        row, matches = C.score_trace(t, key, ceiling=ceiling)
        if not row["valid"]:
            invalid.append({"patient_id": p["patient_id"], "reason": row["invalid_reason"]})
            continue
        t2 = C.bracket_paren_citations(t)
        claims = E4.supported_claims(t2)
        quotes = {c["claim_id"]: c.get("quote") or c["text"] for c in t2.get("claims", [])}
        out = {"run": name, "patient_id": p["patient_id"], "group": p["group"], "hif_prereg": int(row[f"step:{STEP}"])}
        kg_ids = [cid for cid, c in claims.items() if c["mechanism_ids"]]
        for kind, ids in (("", list(claims)), ("_kg", kg_ids)):
            texts = [s for cid in ids for s in (claims[cid]["text"], quotes.get(cid, ""))]
            credit, why = hif_credit(texts, w1, w2)
            base = row["n_steps_recovered" + kind]
            out[f"hif_credit{kind}"] = credit
            out[f"hif_match{kind}"] = why
            out[f"steps_prereg{kind}"] = base
            out[f"steps_weighted{kind}"] = base - int(row[f"step{kind}:{STEP}"]) + credit
        rows.append(out)
    n = len(rows)
    mean = lambda k: round(sum(r[k] for r in rows) / n, 3) if n else None
    prereg = json.loads((RESULTS / prereg_file).read_text(encoding="utf-8"))
    summary = {
        "n_valid": n, "invalid": invalid,
        "reported_prereg_mean_steps": prereg["overall"]["mean_steps_recovered"],
        "reported_prereg_n_valid": prereg["n_valid"],
        "mean_steps_prereg": mean("steps_prereg"), "mean_steps_weighted": mean("steps_weighted"),
        "mean_steps_prereg_kg": mean("steps_prereg_kg"), "mean_steps_weighted_kg": mean("steps_weighted_kg"),
        "hif_step_prereg": mean("hif_prereg"),
        "hif_step_weighted": mean("hif_credit"),
        "hif_matches": {k: sum(r["hif_match"] == k for r in rows) for k in ("hif1", "hif2", "both", "generic", "none")},
    }
    return summary, rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--hif1", type=float, default=0.7)
    ap.add_argument("--hif2", type=float, default=0.3)
    a = ap.parse_args(argv)
    _cache_parquet()
    key = E4.load_key()
    result = {
        "eval": "E4 exploratory: isoform-weighted HIF stabilization credit",
        "status": "exploratory scoring variant, added after the original E4 scoring",
        "requested_by": "Aniket (bio lead), Nov 8",
        "weights": {"hif1": a.hif1, "hif2": a.hif2, "both": min(1.0, a.hif1 + a.hif2), "generic_only": 1.0},
        "answer_key_sha256": C.sha_file(E4.KEY_PATH) if hasattr(E4, "KEY_PATH") else None,
        "scored_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "runs": {},
    }
    all_rows = []
    for name, plan, tdir, ceil, pre in RUNS:
        s, rows = score_run(name, plan, tdir, ceil, pre, key, a.hif1, a.hif2)
        result["runs"][name] = s
        all_rows += rows
        print(f"{name:27s} n={s['n_valid']:2d}  mean steps {s['mean_steps_prereg']} -> {s['mean_steps_weighted']}"
              f"  (KG {s['mean_steps_prereg_kg']} -> {s['mean_steps_weighted_kg']})  HIF step {s['hif_step_prereg']} -> "
              f"{s['hif_step_weighted']}  {s['hif_matches']}  [reported pre-reg: {s['reported_prereg_mean_steps']}, "
              f"n={s['reported_prereg_n_valid']}]")
    (RESULTS / "e4_hif_weighted.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    with open(RESULTS / "e4_hif_weighted.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(all_rows[0]))
        w.writeheader()
        w.writerows(all_rows)
    print("wrote eval/results/e4_hif_weighted.{json,csv}")


if __name__ == "__main__":
    main()
