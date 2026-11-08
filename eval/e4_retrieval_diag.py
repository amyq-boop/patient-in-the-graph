"""E4 retrieval diagnostic: is the masked ccRCC chain missing from the agent's subgraphs because of retrieval?

Exploratory, run after the pre-registered E4 cohort; it changes nothing about that run or its scores. No LLM.
For each cohort trace it compares what retrieve_subgraph returned under different seeds, on the same graph variant:

  agent         the subgraph(s) the agent actually got (read from the trace, not re-run)
  agent_rerun   the agent's first retrieve_subgraph call re-run with the same arguments (checks it is reproducible)
  vhl_only      the same call seeded with VHL alone
  mutated_only  the same call seeded with the patient's somatically mutated genes alone (from get_patient)

and reports which marker genes for each chain step appear as nodes, and whether an edge links VHL to a step-2
marker. The markers are this script's choice (the HIF pathway as commonly described), not the E4 answer key, and
the key file is never read. A marker present in the subgraph means the agent could have cited it; absent means it
could not.

    python -m eval.e4_retrieval_diag                       # eval/traces/e4_cohort -> eval/results/e4_retrieval_diag.{json,csv}
    python -m eval.e4_retrieval_diag --trace-dir eval/traces/e4_cohort_full --variant full
    python -m eval.e4_retrieval_diag --hub 0              # what-if: the re-run arms without the agent's hub penalty
                                                          # (0.25) -> eval/results/e4_retrieval_diag_hub0.{json,csv}
                                                          # (--hub 0.25 -> ..._hub0p25)

--hub changes only the re-run arms of this diagnostic (the agent's own subgraphs are read from the traces as they
are); nothing in the agent or the retriever changes.
"""
import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

MARKERS = {  # step -> gene symbols (resolved to CURIEs from nodes.parquet)
    "1_vhl": ["VHL"],
    "2_hif": ["EPAS1", "HIF1A", "ARNT"],
    "3_vegf": ["VEGFA", "KDR", "FLT1"],
    "4_growth_factor": ["PDGFB", "TGFA", "EGFR"],
    "5_glycolysis": ["SLC2A1", "PGK1", "LDHA", "HK2", "CA9"],
}
VHL = "NCBIGene:7428"
ARMS = ("agent", "agent_rerun", "vhl_only", "mutated_only")


def resolve_markers(nodes):
    """{step: {curie: symbol}} for the marker genes, looked up by name among gene nodes."""
    genes = nodes[nodes.type.astype(str) == "gene"]
    by_name = dict(zip(genes.name.astype(str), genes.curie.astype(str)))
    missing = [g for gs in MARKERS.values() for g in gs if g not in by_name]
    if missing:
        print(f"note: marker genes not in the graph: {missing}", file=sys.stderr)
    return {s: {by_name[g]: g for g in gs if g in by_name} for s, gs in MARKERS.items()}


def summarize(results, markers):
    """Which markers a set of retrieve_subgraph results contains (as nodes), and VHL-to-HIF edges."""
    nodes, edges = set(), []
    for r in results:
        nodes |= {n["curie"] for n in r.get("nodes", [])}
        edges += r.get("edges", [])
    hif = set(markers["2_hif"])
    vhl_hif = sorted({e["edge_id"] for e in edges
                      if {e["src_curie"], e["dst_curie"]} & {VHL} and {e["src_curie"], e["dst_curie"]} & hif})
    out = {"n_nodes": len(nodes), "n_edges": len({e["edge_id"] for e in edges}), "vhl_hif_edges": vhl_hif}
    for step, m in markers.items():
        out[step] = sorted(sym for cur, sym in m.items() if cur in nodes)
    return out


def _calls(trace, name):
    return [tc for tc in trace.get("tool_calls", []) if tc.get("name") == name and isinstance(tc.get("result"), dict)
            and "error" not in tc["result"]]


def diagnose(trace, retrieve, markers):
    """One row per arm for one trace. `retrieve(**args)` is the retrieve_subgraph tool (injectable for tests)."""
    rs = _calls(trace, "retrieve_subgraph")
    pat = next((tc["result"] for tc in _calls(trace, "get_patient")), {})
    mutated = sorted({m["curie"] for m in pat.get("mutations", []) if m.get("curie")})
    rows = {"agent": summarize([tc["result"] for tc in rs], markers)}
    if not rs:
        return rows, {"note": "no successful retrieve_subgraph call in the trace"}
    base = {k: v for k, v in rs[0].get("args", {}).items() if k in ("question", "seeds", "budget")}
    seeds = {"agent_rerun": list(base.get("seeds", [])), "vhl_only": [VHL], "mutated_only": mutated}
    for arm, s in seeds.items():
        if not s:
            rows[arm] = {"note": "no seeds (no mutations in get_patient)"}
            continue
        t0 = time.perf_counter()
        res = retrieve(**{**base, "seeds": s})
        rows[arm] = {**summarize([res], markers), "seconds": round(time.perf_counter() - t0, 2)}
        if "error" in res:
            rows[arm]["error"] = res["error"]
    return rows, {"agent_seeds": len(base.get("seeds", [])), "n_agent_retrieve_calls": len(rs),
                  "vhl_mutated": VHL in mutated, "n_mutated": len(mutated)}


def flat(pid, group, rows):
    out = []
    for arm in ARMS:
        r = rows.get(arm, {})
        out.append({"patient_id": pid, "group": group, "arm": arm, "n_nodes": r.get("n_nodes"),
                    "n_edges": r.get("n_edges"), **{s: ";".join(r.get(s, [])) for s in MARKERS},
                    "vhl_hif_edge": bool(r.get("vhl_hif_edges")), "note": r.get("note") or r.get("error", "")})
    return out


def rates(all_rows, arm, group=None):
    rs = [r for r in all_rows if r["arm"] == arm and (group is None or r["group"] == group) and r["n_nodes"] is not None]
    if not rs:
        return None
    return {"n": len(rs), **{s: round(sum(bool(r[s]) for r in rs) / len(rs), 2) for s in MARKERS},
            "vhl_hif_edge": round(sum(r["vhl_hif_edge"] for r in rs) / len(rs), 2),
            "mean_nodes": round(sum(r["n_nodes"] for r in rs) / len(rs), 1)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trace-dir", default=None)
    ap.add_argument("--variant", default="e4", choices=("e4", "full"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--hub", type=float, default=None,
                    help="degree penalty for the re-run arms (default: the agent's, models.retriever.AGENT_HUB_PENALTY)")
    a = ap.parse_args(argv)
    if os.environ.get("BIOHACK_VARIANT", a.variant) != a.variant:
        sys.exit(f"BIOHACK_VARIANT={os.environ['BIOHACK_VARIANT']} but --variant {a.variant}")
    os.environ["BIOHACK_VARIANT"] = a.variant
    os.environ["BIOHACK_MOCK"] = "0"

    import pandas as pd
    from common.contracts import P
    from agent import config as C
    from agent import tools as T
    assert C.VARIANT == a.variant, (C.VARIANT, a.variant)

    tdir = Path(a.trace_dir) if a.trace_dir else P.traces / ("e4_cohort" if a.variant == "e4" else "e4_cohort_full")
    plan = json.loads((P.traces.parent.parent / "eval" / "e4_cohort_plan.json").read_text()) \
        if (P.traces.parent.parent / "eval" / "e4_cohort_plan.json").exists() else None
    groups = {p["patient_id"]: p["group"] for p in (plan or {}).get("patients", [])}
    markers = resolve_markers(pd.read_parquet(P.nodes, columns=["curie", "name", "type"]))

    if a.hub is None:
        def retrieve(**kw):
            return T.dispatch("retrieve_subgraph", kw)
    else:  # the tool's exact call (agent/tools.retrieve_subgraph), with only the hub penalty overridden
        import models.retriever as R

        def retrieve(question, seeds, budget=None):
            b = None if C.BUDGET_MODE == "auto" and getattr(R, "ADAPTIVE_BUDGET", False) else budget
            return R.retrieve_subgraph(question=question, seeds=list(seeds), budget=b, retriever="gnn_pcst",
                                       variant=a.variant, target_types=C.TARGET_TYPES, hub=a.hub,
                                       anchor=C.ANCHOR_EDGES)
    a.out = a.out or ("e4_retrieval_diag" + ("" if a.hub is None else "_hub" + f"{a.hub:g}".replace(".", "p")))  # hub0p25: a "." would be eaten by with_suffix

    detail, table = {}, []
    files = sorted(f for f in tdir.glob("*.json") if not f.name.startswith("_"))
    if not files:
        sys.exit(f"no traces in {tdir}")
    for f in files:
        t = json.loads(f.read_text(encoding="utf-8"))
        if t.get("variant") not in (None, a.variant):
            print(f"skip {f.name}: trace variant {t.get('variant')} != {a.variant}", file=sys.stderr)
            continue
        pid = t.get("patient_id") or f.stem
        rows, info = diagnose(t, retrieve, markers)
        detail[pid] = {"group": groups.get(pid, ""), **info, "arms": rows}
        table += flat(pid, groups.get(pid, ""), rows)
        ag, vo = rows.get("agent", {}), rows.get("vhl_only", {})
        print(f"{pid:11s} {groups.get(pid, ''):12s} agent hif={ag.get('2_hif')} vegf={ag.get('3_vegf')}  |  "
              f"vhl_only hif={vo.get('2_hif')} vegf={vo.get('3_vegf')} vhl-hif edge={bool(vo.get('vhl_hif_edges'))}")

    summary = {"eval": "e4_retrieval_diag", "variant": a.variant, "trace_dir": str(tdir), "markers": MARKERS,
               "hub_penalty_rerun_arms": a.hub,
               "note": "exploratory, after the pre-registered run; markers are not the answer key",
               "rates": {arm: {"all": rates(table, arm), **{g: rates(table, arm, g) for g in sorted(set(groups.values()))}}
                         for arm in ARMS},
               "per_patient": detail}
    out = P.results / a.out
    out.with_suffix(".json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(table[0]))
        w.writeheader()
        w.writerows(table)
    print("\nshare of patients whose subgraph contains a marker for each step:")
    print(f"{'arm':13s} " + " ".join(f"{s:>15s}" for s in list(MARKERS) + ["vhl_hif_edge", "mean_nodes"]))
    for arm in ARMS:
        r = summary["rates"][arm]["all"]
        if r:
            print(f"{arm:13s} " + " ".join(f"{r[s]:>15}" for s in list(MARKERS) + ["vhl_hif_edge", "mean_nodes"]))
    print(f"wrote {out.with_suffix('.json')} and {out.with_suffix('.csv')}")


if __name__ == "__main__":
    main()
