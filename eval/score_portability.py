#!/usr/bin/env python3
"""
Score the pre-registered BRCA portability expectations (eval/brca_portability.json).

  python -m eval.score_portability

Only ADDS a "results" block (and scored_at/status); the pre-registered cases and expectations are never modified,
so the git diff shows they were not edited after seeing results.

Checkable now (from data/processed/brca): protein top-50 up/down membership and mutation edges.
Pending: expectations that need rank_for_patient or an agent trace (drug targets, agent explanations).
Each result: met | not met | not testable | pending, with the evidence used.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SPEC = Path("eval/brca_portability.json")
BASE = Path("data/processed/brca")
HER2_NEIGHBOURS = ["GRB7", "STARD3", "PGAP3"]
ER_PROGRAM = ["ESR1", "GATA3", "FOXA1", "PGR"]
BASAL_PROLIF = ["KRT5", "KRT14", "KRT17", "EGFR", "MKI67", "TOP2A"]
TRUNCATING = {"Nonsense_Mutation", "Frame_Shift_Del", "Frame_Shift_Ins", "Splice_Site"}


def read(path):
    for p, fn in ((path.with_suffix(".parquet"), pd.read_parquet), (path.with_suffix(".csv"), pd.read_csv)):
        if p.exists():
            return fn(p)
    return pd.DataFrame()


class Patient:
    def __init__(self, pid, prot, muts, measured, clusters):
        p = prot[prot["patient_id"] == pid]
        self.up = {r.symbol: (round(float(r.z), 2), int(r.rank)) for r in p[p.direction == "up"].itertuples()}
        self.down = {r.symbol: (round(float(r.z), 2), int(r.rank)) for r in p[p.direction == "down"].itertuples()}
        m = muts[muts["patient_id"] == pid] if len(muts) else muts
        self.muts = {r.gene_symbol: str(r.classes) for r in m.itertuples()} if len(m) else {}
        self.measured, self.cluster = measured, clusters.get(pid)
        self.present = len(p) > 0

    def where(self, g):
        if g in self.up:
            return f"{g} up (z={self.up[g][0]}, rank {self.up[g][1]})"
        if g in self.down:
            return f"{g} down (z={self.down[g][0]}, rank {self.down[g][1]})"
        return f"{g} measured, not in top-50 up/down" if g in self.measured else f"{g} not measured"


def r(status, evidence):
    return {"result": status, "evidence": evidence}


def any_up(pt, genes):
    measured = [g for g in genes if g in pt.measured]
    if not measured:
        return r("not testable", f"none of {genes} measured")
    hits = [g for g in measured if g in pt.up]
    return r("met" if hits else "not met", "; ".join(pt.where(g) for g in measured))


def not_up(pt, g):
    if g not in pt.measured:
        return r("not testable", f"{g} not measured")
    return r("not met" if g in pt.up else "met", pt.where(g))


PENDING = r("pending", "needs rank_for_patient(drug) or an agent trace (Kaitlyn's BRCA runs)")


def score(eid, pt):
    if not pt.present:
        return r("not testable", "patient not in BRCA patient_proteins")
    if eid in ("11BR060-1", "11BR028-1"):
        return any_up(pt, ["ERBB2"])
    if eid == "11BR060-2":
        return any_up(pt, HER2_NEIGHBOURS)
    if eid == "11BR060-4":
        return not_up(pt, "ESR1")
    if eid in ("18BR019-1", "18BR003-3"):
        return any_up(pt, ER_PROGRAM)
    if eid == "18BR003-1":
        cls = pt.muts.get("CDH1")
        if cls is None:
            return r("not testable", "no CDH1 mutation edge (gene not in mutation table for this patient)")
        if not set(cls.split(";")) & TRUNCATING:
            return r("not testable", f"condition not met: CDH1 mutation is {cls}, not truncating")
        return r("met" if "CDH1" in pt.down else "not met", f"CDH1 mutation {cls}; " + pt.where("CDH1"))
    if eid == "18BR003-2":
        has = "PIK3CA" in pt.muts
        return r("partially scored", f"PIK3CA mutation edge {'present (' + pt.muts['PIK3CA'] + ')' if has else 'ABSENT'}; "
                                     "agent explanation part pending")
    if eid == "05BR044-1":
        if "ESR1" not in pt.measured:
            return r("not testable", "ESR1 not measured")
        if "ESR1" in pt.up:
            return r("not met", pt.where("ESR1"))
        return r("met", pt.where("ESR1") + ("" if "ESR1" in pt.down else " (not up; stated 'down if measured' only partly)"))
    if eid == "05BR044-2":
        return any_up(pt, BASAL_PROLIF)
    return PENDING  # drug / agent expectations


def main():
    spec = json.loads(SPEC.read_text())
    prot = read(BASE / "patients/patient_proteins")
    muts = read(BASE / "patient_mutation_edges")
    measured = set(read(BASE / "ccrcc_prot_z").columns) - {"sample_id"}
    cl = Path("data/processed/clusters/clusters_brca.json")
    clusters = {a["patient_id"]: a["cluster"] for a in json.loads(cl.read_text())["assignments"]} if cl.exists() else {}

    results, tally = [], {}
    for case in spec["cases"]:
        pt = Patient(case["patient_id"], prot, muts, measured, clusters)
        for e in case["expectations"]:
            res = {"id": e["id"], "patient_id": case["patient_id"], **score(e["id"], pt)}
            results.append(res)
            tally[res["result"]] = tally.get(res["result"], 0) + 1
        results.append({"id": f"{case['patient_id']}-context", "patient_id": case["patient_id"], "result": "context",
                        "evidence": f"cluster {pt.cluster} in clusters_brca.json; mutations: "
                                    f"{', '.join(f'{g} ({c})' for g, c in pt.muts.items()) or 'none in CGC genes'}"})
    spec["results"] = results
    spec["results_summary"] = tally
    spec["scored_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    spec["status"] = "partially scored (protein/mutation expectations); drug/agent expectations pending"
    spec["execution"] = "cached"
    SPEC.write_text(json.dumps(spec, indent=2))
    for x in results:
        print(f"{x['id']:16s} {x['result']:16s} {x['evidence']}")
    print("\nsummary:", tally)


if __name__ == "__main__":
    main()
