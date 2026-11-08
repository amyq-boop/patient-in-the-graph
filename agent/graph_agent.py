"""The agent loop: plan -> call tools -> (retry retrieval) -> draft -> verify -> answer.

    from agent.graph_agent import run
    trace = run("Why is this patient high-risk, and what would you target?", patient_id="C3L-00004")

A plain tool-calling loop rather than LangGraph: fewer moving parts, and every step lands in
the trace (CONTRACTS.md §8) that E3 and the demo read.
"""
import json
import time
from collections import defaultdict
from datetime import datetime

from agent import config as C
from agent import prompts
from agent.llm import get_llm
from agent.schemas import TOOLS
from agent.tools import timed_dispatch
from agent.verifier import verify

PATIENT_LISTS = ("phenotypes", "proteins_up", "proteins_down", "mutations", "medications")


def _ok(tc, name=None):
    r = tc["result"]
    return isinstance(r, dict) and "error" not in r and (name is None or tc["name"] == name)


def _llm_view(name, res):
    """What the LLM sees from a tool result. For retrieve_subgraph that's the triples text (§7)."""
    if not isinstance(res, dict):
        return json.dumps(res)[: C.TOOL_RESULT_CHARS]
    if "error" in res:
        return json.dumps({"error": res["error"]})
    if name == "retrieve_subgraph":
        return json.dumps(_subgraph_view(res))
    if name == "get_patient":  # compact lines, mutations first: 100 protein dicts used to push them past the cut
        return json.dumps(_patient_view(res))
    elif name == "predict_patient" and res.get("top_contributing"):  # tumor vs clinical drivers, one line each
        line = lambda x: f"[{x['evidence_id']}] {x.get('name') or x['curie']} score={x.get('score')}"  # noqa: E731
        view = {k: res.get(k) for k in ("patient_id", "label", "risk", "model", "fold", "evidence_id")}
        view.update({"tumor_drivers": [line(x) for x in res.get("tumor_drivers", [])],
                     "clinical_drivers": [line(x) for x in res.get("clinical_drivers", [])],
                     "driver_split": res.get("driver_split", {}),
                     "note": "tumor_drivers are tumor proteins; clinical_drivers are recorded phenotypes "
                             "(symptoms or comorbidities), which the model also uses but which are not tumor biology"})
    elif name == "get_mechanism":  # one line per citable edge: models copy IDs reliably from aligned lines, not JSON
        view = {"disease": res.get("disease"), "name": res.get("name"), "citable_lines": _mechanism_lines(res),
                "genes": res.get("genes", [])}
    else:
        view = {k: v for k, v in res.items() if not k.startswith("_")}
    s = json.dumps(view)
    return s if len(s) <= C.TOOL_RESULT_CHARS else s[: C.TOOL_RESULT_CHARS] + " ...[truncated]"


def _patient_view(res, n_up=25, n_down=15):
    up, down = res.get("proteins_up", []), res.get("proteins_down", [])
    line = lambda x, extra="": f"[{x['edge_id']}] {x.get('symbol') or x.get('name') or x['curie']}{extra}"  # noqa: E731
    return {
        "patient_id": res.get("patient_id"), "curie": res.get("curie"),
        "clinical_summary": res.get("clinical_summary", ""), "note": res.get("note", ""),
        "somatic_mutations": [line(m, f" {m.get('protein_change', '')} (somatic)") for m in res.get("mutations", [])],
        "baseline_medications": [line(m) for m in res.get("medications", [])],
        "phenotypes": [line(p) for p in res.get("phenotypes", [])],
        "proteins_up": [line(p, f" z={p.get('z')}") for p in up[:n_up]],
        "proteins_down": [line(p, f" z={p.get('z')}") for p in down[:n_down]],
        "more_proteins": (f"{max(len(up) - n_up, 0)} more up and {max(len(down) - n_down, 0)} more down-regulated "
                          "proteins are measured (all citable; ask retrieve_subgraph about specific ones)"),
    }


def _ranked_lines(res, skip=()):
    """triples_text lines ordered by relevance: edges touching a seed first, then by the higher endpoint prize."""
    lines = {}
    for line in (res.get("triples_text") or "").splitlines():
        if line.startswith("[") and "]" in line:
            lines[line[1:line.index("]")]] = line
    prize = {n["curie"]: (1 if n.get("is_seed") else 0, float(n.get("prize") or 0)) for n in res.get("nodes", [])}
    ranked = sorted(res.get("edges", []), key=lambda e: max(prize.get(e["src_curie"], (0, 0)),
                                                              prize.get(e["dst_curie"], (0, 0))), reverse=True)
    out = [lines[e["edge_id"]] for e in ranked if e["edge_id"] in lines and e["edge_id"] not in skip]
    return out + [l for k, l in lines.items() if k not in skip and l not in out]  # anything without an edge record


def _fit(lines, limit):
    shown, size = [], 0
    for line in lines:
        if size + len(line) + 1 > limit:
            break
        shown.append(line)
        size += len(line) + 1
    note = f"\n...{len(lines) - len(shown)} lower-ranked edges not shown" if len(shown) < len(lines) else ""
    return "\n".join(shown) + note, len(shown)


def _subgraph_view(res, skip=()):
    lines = _ranked_lines(res, skip)
    text, n = _fit(lines, C.RETRIEVE_VIEW_CHARS)
    return {"n_nodes": len(res.get("nodes", [])), "n_edges": len(lines), "n_edges_shown": n, "triples_text": text}


def _mechanism_lines(res):
    dis = res.get("name", "")
    lines = []
    for ch in res.get("chains", []):
        for i, st in enumerate(ch.get("steps", [])):
            eid = f"[{st['edge_id']}] " if st.get("edge_id") else "(no edge ID) "
            pm = (" " + " ".join(f"[{p}]" for p in st.get("pmids", []))) if st.get("pmids") else ""
            lines.append(f"{eid}{ch.get('chain_id')} step {i + 1}: {st.get('label', '')} (mechanism of {dis}){pm}")
    for d in res.get("drug_evidence", []):
        pm = (" " + " ".join(f"[{p}]" for p in d.get("pmids", []))) if d.get("pmids") else ""
        lines.append(f"[{d['edge_id']}] {d.get('name', '')} (drug) --{d.get('relation', '')}--> {dis} (disease){pm}")
    return "\n".join(lines)


def _evidence(tool_calls):
    edges, pmids = [], []
    for tc in tool_calls:
        if not _ok(tc):
            continue
        r = tc["result"]
        if tc["name"] == "retrieve_subgraph":
            edges += r.get("edge_ids", [])
        elif tc["name"] == "get_patient":
            edges += [x["edge_id"] for k in PATIENT_LISTS for x in r.get(k, []) if x.get("edge_id")]
        elif tc["name"] == "get_mechanism":
            steps = [st for ch in r.get("chains", []) for st in ch.get("steps", [])]
            pmids += [p for st in steps for p in st.get("pmids", [])]
            pmids += [p for d in r.get("drug_evidence", []) for p in d.get("pmids", [])]
            edges += [st["edge_id"] for st in steps if st.get("edge_id")]
            edges += [d["edge_id"] for d in r.get("drug_evidence", []) if d.get("edge_id")]
        elif tc["name"] == "predict_patient":  # model outputs are citable too (pred:/contrib:), counted separately
            edges += [r["evidence_id"]] if r.get("evidence_id") else []
            edges += [x["evidence_id"] for x in r.get("top_contributing", []) if x.get("evidence_id")]
        elif tc["name"] == "rank_for_patient":
            edges += [x["evidence_id"] for x in r.get("items", []) if x.get("evidence_id")]
    return {"edge_ids": list(dict.fromkeys(edges)), "pmids": list(dict.fromkeys(pmids))}


def _edge_names(tool_calls):
    """edge_id -> endpoint names and CURIEs, for the verifier's entity_match check."""
    out = {}
    for tc in tool_calls:
        if not _ok(tc):
            continue
        r = tc["result"]
        if tc["name"] == "retrieve_subgraph":
            names = {n["curie"]: n.get("name", n["curie"]) for n in r.get("nodes", [])}
            for e in r.get("edges", []):
                out[e["edge_id"]] = [names.get(e["src_curie"], e["src_curie"]), names.get(e["dst_curie"], e["dst_curie"]),
                                     e["src_curie"], e["dst_curie"]]
        elif tc["name"] == "get_patient":
            for k in PATIENT_LISTS:
                for x in r.get(k, []):
                    out[x["edge_id"]] = ["patient", x.get("name") or x.get("symbol", ""), x["curie"]]
        elif tc["name"] == "get_mechanism":
            for ch in r.get("chains", []):
                for st in ch.get("steps", []):
                    if st.get("edge_id"):  # the step itself, not the disease, so a mislabel can't match on "ccRCC"
                        out[st["edge_id"]] = ["~soft", st.get("label", ""), st.get("mechanism", "")]
            for d in r.get("drug_evidence", []):
                if d.get("edge_id"):
                    out[d["edge_id"]] = [d.get("name", ""), d["curie"]]
        elif tc["name"] == "predict_patient":
            if r.get("evidence_id"):
                out[r["evidence_id"]] = ["patient", "risk"]
            for x in r.get("top_contributing", []):
                if x.get("evidence_id"):
                    out[x["evidence_id"]] = ["patient", x.get("name", ""), x["curie"]]
        elif tc["name"] == "rank_for_patient":
            for x in r.get("items", []):
                if x.get("evidence_id"):
                    out[x["evidence_id"]] = ["patient", x.get("name", ""), x["curie"]]
    return out


def _patient_result(tool_calls):
    return next((tc["result"] for tc in tool_calls if _ok(tc, "get_patient")), None)


def protein_phenotype_connected(tool_calls, patient):
    """True if a retrieved KG path (not through the patient) links one of the patient's proteins to
    one of their phenotypes. Patients with no phenotypes pass trivially."""
    phen = {p["curie"] for p in patient.get("phenotypes", [])}
    prot = {p["curie"] for p in patient.get("proteins_up", []) + patient.get("proteins_down", [])}
    if not phen:
        return True
    adj = defaultdict(set)
    for tc in tool_calls:
        if not _ok(tc, "retrieve_subgraph"):
            continue
        for e in tc["result"].get("edges", []):
            a, b = e["src_curie"], e["dst_curie"]
            if not (a.startswith("PATIENT:") or b.startswith("PATIENT:")):
                adj[a].add(b)
                adj[b].add(a)
    seen, stack = set(prot), list(prot)
    while stack:
        n = stack.pop()
        if n in phen:
            return True
        for m in adj[n] - seen:
            seen.add(m)
            stack.append(m)
    return False


def _call(tool_calls, name, args, by="llm"):
    res, ms = timed_dispatch(name, args)
    tool_calls.append({"name": name, "args": args, "result": res, "ms": ms, "by": by})
    return res


def _notes(tool_calls):
    mocked = sorted({tc["name"] for tc in tool_calls if isinstance(tc["result"], dict) and (
        tc["result"].get("_fallback") or (tc["result"].get("meta") or {}).get("mock") or C.MOCK_MODE == "1")})
    notes = ["MOCK DATA used for: " + ", ".join(mocked)] if mocked else []
    return notes + [f"tool error: {tc['name']}: {tc['result']['error']}" for tc in tool_calls
                    if isinstance(tc["result"], dict) and "error" in tc["result"]]


def _retrieval_guarantee(question, tool_calls, msgs, draft, llm, patient):
    """Patient questions must end with a protein -> phenotype path, or 3 retries (build guide WS5)."""
    prior = [tc for tc in tool_calls if _ok(tc, "retrieve_subgraph")]
    budget = int(prior[-1]["args"].get("budget") or C.DEFAULT_BUDGET) if prior else C.DEFAULT_BUDGET
    tried = {s for tc in prior for s in tc["args"].get("seeds", [])}
    up, down = patient.get("proteins_up", []), patient.get("proteins_down", [])
    seed_rounds = [  # each retry brings seeds the agent hasn't tried, instead of only a bigger budget
        [m["curie"] for m in patient.get("mutations", [])] + [p["curie"] for p in patient["phenotypes"]]
        + [p["curie"] for p in up[:10]] + [p["curie"] for p in down[:5]],
        [p["curie"] for p in up[10:25]] + [p["curie"] for p in down[5:12]],
        [p["curie"] for p in up[25:50]] + [p["curie"] for p in down[12:25]],
    ]
    tries = 0
    while (not prior or not protein_phenotype_connected(tool_calls, patient)) and tries < C.MAX_RETRIEVAL_RETRIES:
        new_seeds = [s for s in seed_rounds[min(tries, len(seed_rounds) - 1)] if s not in tried]
        tries += 1
        if prior:
            budget = min(budget + C.RETRY_BUDGET_STEP, max(C.RETRY_BUDGET_MAX, budget))
        seeds = [patient["curie"]] + list(dict.fromkeys(new_seeds or seed_rounds[0]))
        tried.update(seeds)
        res = _call(tool_calls, "retrieve_subgraph", {"question": question, "seeds": seeds, "budget": budget},
                    by="controller")
        if "error" not in res:
            prior.append(res)
    if not tries:
        return draft
    seen = {e for tc in tool_calls if tc["by"] == "llm" and _ok(tc, "retrieve_subgraph")
            for e in tc["result"].get("edge_ids", [])}
    new = []
    for tc in tool_calls:  # only edges the model hasn't seen yet, ranked, within the same view limit
        if tc["by"] == "controller" and _ok(tc, "retrieve_subgraph"):
            new.append(_subgraph_view(tc["result"], skip=seen)["triples_text"])
            seen.update(tc["result"].get("edge_ids", []))
    new = [x for x in new if x.strip()]
    if not any(new):
        return draft
    if not C.REVISE_ALWAYS and not protein_phenotype_connected(tool_calls, patient):
        return draft  # retries found no protein -> phenotype link: a rewrite (~90 s) would only restate the gap
    msgs += [{"role": "assistant", "content": draft},
             {"role": "user", "content": "NEW EVIDENCE:\n" + "\n".join(new) + "\n\n" + prompts.RETRY_NOTE}]
    return llm.chat(msgs, purpose="agent")["content"] or draft


def run(question, patient_id=None, qid="adhoc", mode="agent", llm=None, run_id=None):
    llm = llm or get_llm()
    run_id = run_id or datetime.now().strftime("%Y-%m-%dT%H-%M") + f"_{mode}"
    if isinstance(getattr(llm, "truncated", None), list):
        llm.truncated.clear()
    if isinstance(getattr(llm, "calls", None), list):
        llm.calls.clear()
    t0 = time.perf_counter()
    tool_calls = []
    user = question + (f"\n\nPatient: {patient_id}" if patient_id else "")

    if mode == "llm_only":
        msgs = [{"role": "system", "content": prompts.LLM_ONLY_SYSTEM}, {"role": "user", "content": user}]
        draft = llm.chat(msgs, purpose="agent")["content"] or ""
    else:
        system = prompts.AGENT_SYSTEM.replace("__CCRCC_ID__", C.CCRCC_ID)
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        draft = None
        while len(tool_calls) < C.MAX_TOOL_CALLS:
            out = llm.chat(msgs, tools=TOOLS, purpose="agent")
            if not out["tool_calls"]:
                draft = out["content"] or ""
                break
            msgs.append({"role": "assistant", "content": out["content"] or "", "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])}}
                for tc in out["tool_calls"]]})
            for tc in out["tool_calls"]:
                res = _call(tool_calls, tc["name"], tc["arguments"])
                msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": _llm_view(tc["name"], res)})
        if draft is None:
            msgs.append({"role": "user", "content": "Stop calling tools. Write the final answer now from the evidence you have."})
            draft = llm.chat(msgs, purpose="agent")["content"] or ""

        if patient_id:
            patient = _patient_result(tool_calls)
            if patient is None:
                res = _call(tool_calls, "get_patient", {"patient_id": patient_id}, by="controller")
                patient = None if "error" in res else res
            if patient:
                draft = _retrieval_guarantee(question, tool_calls, msgs, draft, llm, patient)

    evidence = _evidence(tool_calls) if mode == "agent" else {"edge_ids": [], "pmids": []}
    claims, answer, stats, splitter = verify(draft, evidence, llm, edge_names=_edge_names(tool_calls))
    return {
        "contract_version": C.CONTRACT_VERSION,
        "run_id": run_id, "qid": qid, "mode": mode,
        "patient_id": patient_id, "question": question, "model": getattr(llm, "model", "unknown"),
        "variant": C.VARIANT,
        "tool_calls": tool_calls if mode == "agent" else [],
        "evidence": evidence,
        "draft": draft,
        "claims": claims,
        "answer": answer,
        "verifier": stats,
        "verifier_splitter": splitter,
        "notes": _notes(tool_calls) + [f"LLM output cut off at max_tokens: {p} (raise NIM_MAX_TOKENS"
                                       f"{'_CLAIMS' if p == 'claims' else ''})"
                                       for p in sorted(set(getattr(llm, "truncated", None) or []))],
        "elapsed_s": round(time.perf_counter() - t0, 2),
        # where the time went: one entry per LLM call, plus the size of every retrieved subgraph
        "llm_calls": list(getattr(llm, "calls", None) or []),
        "subgraph_sizes": [{"by": tc["by"], "requested_budget": tc["args"].get("budget"),
                            "budget": (tc["result"].get("meta") or {}).get("budget", tc["args"].get("budget")),
                            "adaptive": bool((tc["result"].get("meta") or {}).get("adaptive_budget")),
                            "n_nodes": len(tc["result"].get("nodes", [])), "n_edges": len(tc["result"].get("edges", []))}
                           for tc in tool_calls if _ok(tc, "retrieve_subgraph")],
    }
