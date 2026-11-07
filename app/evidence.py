"""Evidence model for the demo: turns a contract trace into nodes, edges, cited paths and readable sources."""
from __future__ import annotations

import html
import re

from common.contracts import patient_curie

REL_WORDS = {"disease_protein": "linked to", "drug_protein": "targets", "protein_protein": "interacts with",
             "pathway_protein": "in pathway", "dm_contributes_to": "contributes to", "protein_up": "up in tumor",
             "protein_down": "down in tumor", "has_phenotype": "has", "causes": "causes", "indication": "treats",
             "model_contrib": "drives the model score", "model_rank": "ranked for this patient", "treats": "treats"}
_TOK = r"(?:E\d+|pt:[^\]\s,;]+|PMID:\s?\d+|(?:pred|contrib|rank):[^\]\s,;]+)"
ID_RE = re.compile(rf"\[({_TOK}(?:\s*[,;]\s*{_TOK})*)\]")
MODEL_PREFIXES = ("pred:", "contrib:", "rank:")
MOCK_PMID = re.compile(r"PMID:0+\d$")


def ok(tc, name):
    r = tc.get("result")
    return tc.get("name") == name and isinstance(r, dict) and "error" not in r


def evidence(trace):
    """All nodes/edges the tools returned, keyed for lookup. Mechanism steps become a 'causes' chain."""
    nodes, edges, steps = {}, {}, []

    def node(c, name=None, t=None):
        n = nodes.setdefault(c, {"curie": c, "name": name or c, "type": t or "gene"})
        if name and n["name"] == c:
            n["name"] = name
        if t:
            n["type"] = t

    for tc in trace.get("tool_calls", []):
        r = tc.get("result")
        if ok(tc, "get_patient"):
            pc = r.get("curie") or patient_curie(r["patient_id"])
            node(pc, f"Patient {r['patient_id']}", "patient")
            for key, rel in (("phenotypes", "has_phenotype"), ("proteins_up", "protein_up"),
                             ("proteins_down", "protein_down")):
                for x in r.get(key, []):
                    arrow = " ↑" if rel == "protein_up" else (" ↓" if rel == "protein_down" else "")
                    node(x["curie"], (x.get("symbol") or x["name"]) + arrow, "phenotype" if key == "phenotypes" else "gene")
                    edges[x["edge_id"]] = {"src": pc, "dst": x["curie"], "rel": rel, "z": x.get("z"), "pmids": []}
        elif ok(tc, "retrieve_subgraph"):
            for n in r["nodes"]:
                node(n["curie"], n.get("name"), n.get("type"))
            for e in r["edges"]:
                edges[e["edge_id"]] = {"src": e["src_curie"], "dst": e["dst_curie"], "rel": e["rel"], "pmids": []}
        elif ok(tc, "predict_patient"):
            pc = patient_curie(r["patient_id"])
            node(pc, f"Patient {r['patient_id']}", "patient")
            for x in r.get("top_contributing", []):
                if x.get("evidence_id"):
                    node(x["curie"], x.get("name"), x.get("type"))
                    edges[x["evidence_id"]] = {"src": pc, "dst": x["curie"], "rel": "model_contrib", "pmids": []}
        elif ok(tc, "rank_for_patient"):
            pc = patient_curie(r["patient_id"])
            for x in r.get("items", []):
                if x.get("evidence_id"):
                    node(x["curie"], x.get("name"), r.get("node_type"))
                    edges[x["evidence_id"]] = {"src": pc, "dst": x["curie"], "rel": "model_rank", "pmids": []}
        elif ok(tc, "get_mechanism"):
            dis = r.get("disease")
            if dis:
                node(dis, r.get("name"), "disease")
            for d in r.get("drug_evidence", []):
                if d.get("edge_id") and dis:
                    node(d["curie"], d.get("name"), "drug")
                    edges[d["edge_id"]] = {"src": d["curie"], "dst": dis, "rel": d.get("relation") or "indication",
                                           "pmids": list(d.get("pmids", []))}
            for ch in r.get("chains", []):
                prev = None
                for s in ch.get("steps", []):
                    node(s["mechanism"], s.get("label"), "mechanism")
                    steps.append(s)
                    if prev:
                        edges[f"{prev['mechanism']}>{s['mechanism']}"] = {
                            "src": prev["mechanism"], "dst": s["mechanism"], "rel": "causes",
                            "pmids": sorted(set(prev.get("pmids", [])) | set(s.get("pmids", [])))}
                    prev = s
    for e in edges.values():
        node(e["src"])
        node(e["dst"])
    return nodes, edges, steps


def claim_edges(claim, edges, steps):
    """Edge keys that support one claim; PMID citations map onto the mechanism chain."""
    sup = claim.get("support", {})
    keys = [e for e in sup.get("edge_ids", []) if e in edges]
    pm = set(sup.get("pmids", []))
    if pm:
        hit = [s["mechanism"] for s in steps if pm & set(s.get("pmids", []))]
        chain = [k for k, e in edges.items() if e["rel"] == "causes" and (e["src"] in hit and e["dst"] in hit)]
        if not chain:  # single step: show it with the step before it
            chain = [k for k, e in edges.items() if e["rel"] == "causes" and e["dst"] in hit]
        keys += chain
    return list(dict.fromkeys(keys))


def paths(edges, keys):
    """Join the cited edges into readable chains (src -> ... -> dst)."""
    keys = list(dict.fromkeys(keys))
    out_of = {}
    for k in keys:
        out_of.setdefault(edges[k]["src"], []).append(k)
    has_in = {edges[k]["dst"] for k in keys}
    starts = [k for k in keys if edges[k]["src"] not in has_in] or keys[:1]
    chains, seen = [], set()

    def walk(k, chain):
        chain = chain + [k]
        seen.add(k)
        nxt = [n for n in out_of.get(edges[k]["dst"], []) if n not in chain]
        if not nxt:
            chains.append(chain)
        for n in nxt:
            walk(n, chain)

    for k in starts:
        walk(k, [])
    for k in keys:  # anything left in a cycle
        if k not in seen:
            walk(k, [])
    return chains


def source_label(ref, nodes, edges):
    if ref.startswith("PMID"):
        num = ref.split(":")[1].strip()
        if MOCK_PMID.match(ref.replace(" ", "")):
            return f"{ref} · mock citation"
        return f"[{ref}](https://pubmed.ncbi.nlm.nih.gov/{num}/)"
    e = edges.get(ref)
    if not e:
        return ref
    s, d = nodes[e["src"]]["name"], nodes[e["dst"]]["name"]
    origin = ("patient measurement" if ref.startswith("pt:") else
              "model output" if ref.startswith(MODEL_PREFIXES) else "knowledge graph")
    return f"{s} → {REL_WORDS.get(e['rel'], e['rel'])} → {d} · {origin}"


def render_answer(answer, nodes, edges):
    """Swap raw IDs for numbered chips; return (html, ordered sources)."""
    order = []

    def sub(m):
        nums = []
        for ref in re.split(r"\s*[,;]\s*", m.group(1)):
            ref = ref.replace("PMID: ", "PMID:")
            if ref not in order:
                order.append(ref)
            nums.append(str(order.index(ref) + 1))
        return "".join(f'<span class="chip">{n}</span>' for n in nums)

    text = "\n".join(l for l in answer.splitlines() if not l.strip().startswith("_Verifier"))
    text = ID_RE.sub(sub, html.escape(text, quote=False).replace("&lt;", "<").replace("&gt;", ">"))
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text).replace("\n", "<br>")
    return text, order
