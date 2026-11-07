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
        view = {"triples_text": res.get("triples_text", ""), "edge_ids": res.get("edge_ids", []),
                "n_nodes": len(res.get("nodes", []))}
    elif name == "get_mechanism":  # one line per citable edge: models copy IDs reliably from aligned lines, not JSON
        view = {"disease": res.get("disease"), "name": res.get("name"), "citable_lines": _mechanism_lines(res),
                "genes": res.get("genes", [])}
    else:
        view = {k: v for k, v in res.items() if not k.startswith("_")}
    s = json.dumps(view)
    return s if len(s) <= C.TOOL_RESULT_CHARS else s[: C.TOOL_RESULT_CHARS] + " ...[truncated]"


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
    tries = 0
    while (not prior or not protein_phenotype_connected(tool_calls, patient)) and tries < C.MAX_RETRIEVAL_RETRIES:
        tries += 1
        if prior:
            budget = int(budget * 1.5)
        seeds = ([patient["curie"]] + [p["curie"] for p in patient["phenotypes"]]
                 + [p["curie"] for p in patient["proteins_up"][:10]] + [p["curie"] for p in patient["proteins_down"][:5]])
        res = _call(tool_calls, "retrieve_subgraph", {"question": question, "seeds": seeds, "budget": budget},
                    by="controller")
        if "error" not in res:
            prior.append(res)
    if not tries:
        return draft
    new = [tc["result"].get("triples_text", "") for tc in tool_calls
           if tc["by"] == "controller" and _ok(tc, "retrieve_subgraph")]
    if not any(new):
        return draft
    msgs += [{"role": "assistant", "content": draft},
             {"role": "user", "content": "NEW EVIDENCE:\n" + "\n".join(new) + "\n\n" + prompts.RETRY_NOTE}]
    return llm.chat(msgs, purpose="agent")["content"] or draft


def run(question, patient_id=None, qid="adhoc", mode="agent", llm=None, run_id=None):
    llm = llm or get_llm()
    run_id = run_id or datetime.now().strftime("%Y-%m-%dT%H-%M") + f"_{mode}"
    if isinstance(getattr(llm, "truncated", None), list):
        llm.truncated.clear()
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
    }
