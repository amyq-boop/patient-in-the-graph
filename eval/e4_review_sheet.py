"""Build E4 review sheets (HIF-1alpha accepted) for the single-case E4 traces, so a reviewer can produce
eval/results/e4.json for the app.

For each trace this writes, under eval/results/e4_review_sheets/:
    <patient>.review.json   the file to fill in (same format as e4_case_study's template), and
    <patient>.md            the same candidates in readable form, with every cited edge ID spelled out.
Compared with e4_case_study's own template, the HIF stabilization step also lists claims that name HIF-1alpha
(added Nov 8 at Aniket's request). The answer key and e4_case_study.py are unchanged.

    python -m eval.e4_review_sheet
Then, after filling one sheet in:
    python -m eval.e4_case_study --score eval/traces/<run>/E4.json --review eval/results/e4_review_sheets/<patient>.review.json
"""
import copy
import json
import re
from pathlib import Path

import pandas as pd

from common.contracts import P, ROOT, triple_line
from eval import e4_case_study as E4

TRACES = ["20261108T062325952035Z_e4_1687cf59", "20261108T030653824420Z_e4_25491ba4",
          "20261107T204619162319Z_e4_c714a161"]
HIF1_TERMS = ["HIF-1alpha", "HIF1A", "HIF1alpha", "HIF-1"]
OUT = P.results / "e4_review_sheets"


def main():
    key = copy.deepcopy(E4.load_key())
    key["core_chain"][1]["accept_terms"] += HIF1_TERMS
    OUT.mkdir(parents=True, exist_ok=True)
    sheets = []
    for run in TRACES:
        tpath = P.traces / run / "E4.json"
        trace = E4.read_json(tpath)
        claims = E4.supported_claims(trace)
        sheet = E4.make_review_template(trace, key, claims)
        sheet["trace"] = str(tpath.relative_to(ROOT)).replace("\\", "/")
        sheet["patient_id"] = trace["patient_id"]
        sheet["note"] = "HIF stabilization step also accepts HIF-1alpha (Aniket, Nov 8). Fill in reviewer, then per step claim_id, evidence_ids, verified, causal_link_verified."
        sheets.append((trace, sheet, tpath))
    rows = {int(m) for _, s, _ in sheets for st in s["steps"] + s["drugs"] for c in st["candidates"]
            for e in c["evidence_ids"] for m in re.findall(r"^E(\d+)$", e)}
    edges = pd.read_parquet(P.edges, columns=["edge_row", "src", "dst", "rel"])
    edges = edges[edges.edge_row.isin(rows)]
    nodes = pd.read_parquet(P.nodes, columns=["gidx", "name", "type"]).set_index("gidx")
    tri = {f"E{r.edge_row}": triple_line(f"E{r.edge_row}", nodes.at[r.src, "name"], nodes.at[r.src, "type"], r.rel,
                                         nodes.at[r.dst, "name"], nodes.at[r.dst, "type"]) for r in edges.itertuples()}
    for trace, sheet, tpath in sheets:
        pid = trace["patient_id"]
        (OUT / f"{pid}.review.json").write_text(json.dumps(sheet, indent=2, ensure_ascii=False), encoding="utf-8")
        n_cand = sum(bool(s["candidates"]) for s in sheet["steps"])
        L = [f"# E4 review sheet: {pid}", "",
             f"Trace: `{sheet['trace']}` (run `{sheet['run_id']}`). Steps with at least one candidate claim: {n_cand} of 5.", "",
             "**How to fill in** `" + f"{pid}.review.json" + "`: put your name in `reviewer`. For each step you accept, copy one "
             "candidate's `claim_id` into the step's `claim_id`, copy the evidence IDs that actually support it into "
             "`evidence_ids`, and set `verified: true`. For steps 2-5 also set `causal_link_verified: true` if the answer "
             "links the step to its parent (VHL -> HIF, or HIF -> downstream). For drugs, set `target_verified: true` "
             "and fill `claim_id` / `evidence_ids` the same way. Leave anything you don't accept as it is.", "",
             "Then run:", "", "```",
             f"python -m eval.e4_case_study --score {sheet['trace']} --review eval/results/e4_review_sheets/{pid}.review.json",
             "```", ""]
        for st in sheet["steps"] + sheet["drugs"]:
            L.append(f"## {st.get('label') or 'Drug: ' + st['name']}")
            if not st["candidates"]:
                L += ["", "_No candidate claim. This step can't be recovered for this trace._", ""]
                continue
            for c in st["candidates"]:
                L += ["", f"**{c['claim_id']}**: {c['text']}", ""]
                for e in c["evidence_ids"]:
                    if e in tri:
                        L.append(f"- `{tri[e]}`")
                    elif e.startswith("PMID:"):
                        L.append(f"- [{e}](https://pubmed.ncbi.nlm.nih.gov/{e.split(':')[1]}/)")
                    else:
                        L.append(f"- `{e}`")
            L.append("")
        (OUT / f"{pid}.md").write_text("\n".join(L), encoding="utf-8")
        print(f"{pid}: {n_cand}/5 steps with candidates, drugs with candidates: {[d['name'] for d in sheet['drugs']]}")
        for st in sheet["steps"]:
            print(f"   {st['label']:48s} {[c['claim_id'] for c in st['candidates']]}")
    print(f"wrote {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
