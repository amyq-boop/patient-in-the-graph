"""Evidence-anchored, exploratory graph reasoning for the public Alexander Young case.

Calls the repo's *real* models.retriever (GNN-B), optional NVIDIA LLM,
then its real agent.verifier. It never calls the kidney cancer risk model,
never accepts mock graph results and never interprets a graph edge as
proof of treatment effectiveness. This is an independent trace format;
it does not claim to satisfy the ccRCC E1-E4 trace schema.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from app.external_case import joined_findings

# Public source features -> genes in PrimeKG. The values are gene CURIEs,
# not a claim of alteration, driver status or therapy sensitivity.
MARKERS = {
    "CEACAM5 IHC H-score": ("CEACAM5", "NCBIGene:1048"),
    "MET IHC H-score": ("MET", "NCBIGene:4233"),
    "TROP2 IHC H-score": ("TROP2 / TACSTD2", "NCBIGene:4070"),
    "HER2 IHC score": ("HER2 / ERBB2", "NCBIGene:2064"),
}
DEFAULT_QUESTION = (
    "Which knowledge-graph connections are relevant to the reported tumor marker, "
    "and what can we NOT conclude about drug response?"
)
MAX_EDGES_FOR_MODEL = 20


class ExternalReasoningError(RuntimeError):
    """Explicit failure: do not silently replace real evidence with mock outputs."""


def available_markers(case: dict) -> list[dict]:
    """Only join-complete, source-linked IHC observations become graph seeds."""
    out = []
    for f in joined_findings(case):
        if f["provenance_status"] != "linked" or f.get("feature") not in MARKERS:
            continue
        symbol, curie = MARKERS[f["feature"]]
        if f.get("value") is None or not f.get("sample_id"):
            continue
        fact = dict(f)
        fact.update(symbol=symbol, curie=curie,
                    evidence_id=f"pt:youngcrc:reported_ihc:{curie}")
        out.append(fact)
    return out


def graph_prerequisites() -> list[str]:
    """Check original graph assets; allow an explicit, informative stop."""
    from common.contracts import P

    required = [P.nodes, P.edges, P.evidence, P.feat_text, P.emb("main")]
    return [str(p) for p in required if not p.exists()]


def observation_sentence(f: dict) -> str:
    """This sentence is templated from the source, not authored by the LLM."""
    value = f["value"]
    unit = f.get("unit")
    value_str = f"{value} {unit}" if unit else str(value)
    return (
        f"A published report extraction lists {f['feature']} = {value_str} for the "
        f"archived {f['sample_label']} (specimen date {f.get('sample_date') or 'unspecified'}) "
        f"[{f['evidence_id']}]."
    )


def edge_summary(graph: dict, seed_curie: str, limit: int = MAX_EDGES_FOR_MODEL) -> list[dict]:
    """Prioritize graph edges connected to the observed gene; keep original edge IDs."""
    nodes = {n["curie"]: n.get("name") or n["curie"] for n in graph.get("nodes", [])}
    edges = [e for e in graph.get("edges", []) if
             re.fullmatch(r"E\d+", str(e.get("edge_id", ""))) and
             e.get("src_curie") and e.get("dst_curie")]
    def ordering(e):
        direct = seed_curie in (e["src_curie"], e["dst_curie"])
        return (not direct, e["edge_id"])
    out = []
    for e in sorted(edges, key=ordering)[:limit]:
        a, b = e["src_curie"], e["dst_curie"]
        out.append({
            "edge_id": e["edge_id"],
            "src_curie": a, "dst_curie": b,
            "source": nodes.get(a, a), "target": nodes.get(b, b),
            "relation": e.get("rel", "associated_with"),
            "origin": e.get("origin", "unknown"),
            "touches_marker": seed_curie in (a, b),
        })
    return out


def graph_only_draft(f: dict, edges: list[dict]) -> str:
    """Always usable without an API key; claims only about source and KG contents."""
    lines = ["### Reported patient observation", observation_sentence(f),
             "### Retrieved graph facts (not patient-specific causal findings)"]
    for e in edges[:5]:
        lines.append(
            f"The knowledge graph records {e['source']} --{e['relation']}--> "
            f"{e['target']} [{e['edge_id']}]."
        )
    if not edges:
        lines.append("No citable graph edges were returned for this marker.")
    lines.extend(["### Limitations", "The retrieved connections do not establish a clinical response or an effective treatment for this individual."])
    return "\n\n".join(lines)


def llm_draft(f: dict, edges: list[dict], question: str, llm) -> str:
    triples = "\n".join(
        f"[{e['edge_id']}] {e['source']} ({e['src_curie']}) --{e['relation']}--> "
        f"{e['target']} ({e['dst_curie']})" for e in edges
    )
    system = (
        "You are a cautious scientific evidence-synthesis assistant, NOT a clinician. "
        "Respond with up to four short statements about the published observation and the "
        "retrieved graph ONLY. Every positive biomedical or graph claim must cite an exact "
        "evidence identifier in square brackets from the supplied facts. "
        "A graph relation is evidence that the graph contains that relation, NOT clinical "
        "proof of causation, drug efficacy, eligibility, present-day biology, or prognosis. "
        "Do not prescribe or recommend a therapy. Do not add identifiers or experiments "
        "you have not observed. Distinguish observed measurements from exploratory hypotheses. "
        "If insufficient evidence, abstain."
    )
    user = (
        f"QUESTION: {question}\n"
        f"ARCHIVED PUBLIC OBSERVATION: {observation_sentence(f)}\n"
        f"REPORT EVIDENCE CLASS: {f.get('evidence_class')}; "
        f"CURRENT-NODE ASSESSMENT: {f.get('current_node_status')}\n"
        f"KG EDGES (directed, graph assertions only):\n{triples or '(none)'}"
    )
    response = llm.chat([{"role": "system", "content": system},
                         {"role": "user", "content": user}], purpose="agent")
    answer = response.get("content") or ""
    if not answer.strip():
        raise ExternalReasoningError("LLM returned an empty draft")
    # Include the exact source-backed observation independent of generative text.
    return "### Verified source extraction\n\n" + observation_sentence(f) + "\n\n### Exploratory graph synthesis\n\n" + answer


def run_case(case: dict, selected_finding_id: str, question: str = DEFAULT_QUESTION,
             use_llm: bool = False, demo_rejection: bool = True,
             retriever=None, verifier=None, llm=None, budget: int = 50) -> dict:
    """Real GNN retrieval -> optional real LLM synthesis -> real claim-ID verifier.

    Injecting retriever/verifier is for offline unit tests only. UI always uses
    real implementations; no BIOHACK_MOCK fallback is used. A missing graph
    asset or retrieval error raises ExternalReasoningError.
    """
    observations = available_markers(case)
    f = next((x for x in observations if x["finding_id"] == selected_finding_id), None)
    if f is None:
        raise ExternalReasoningError("Select a source-linked IHC observation first")
    if not isinstance(question, str) or not question.strip() or len(question) > 750:
        raise ExternalReasoningError("Question must be between 1 and 750 characters")
    if not 10 <= budget <= 100:
        raise ExternalReasoningError("Budget must be between 10 and 100 nodes")
    if retriever is None:
        missing = graph_prerequisites()
        if missing:
            raise ExternalReasoningError("GNN graph data missing: " + ", ".join(missing))
        from models.retriever import retrieve_subgraph
        retriever = retrieve_subgraph
    t0 = time.perf_counter()
    args = {"question": question.strip(), "seeds": [f["curie"]],
            "budget": budget, "retriever": "gnn_pcst", "variant": "full",
            "target_types": ("gene", "biological_process", "molecular_function",
                             "drug", "pathway", "disease"), "anchor": 15}
    try:
        graph = retriever(**args)
    except Exception as exc:
        raise ExternalReasoningError(f"Live GNN retrieval failed: {type(exc).__name__}: {exc}") from exc
    if not isinstance(graph, dict) or graph.get("error"):
        raise ExternalReasoningError(str((graph or {}).get("error") or "invalid graph response"))
    if (graph.get("meta") or {}).get("mock") or graph.get("_fallback"):
        raise ExternalReasoningError("Refusing mock retrieval for a public real-patient demonstration")
    if (graph.get("meta") or {}).get("retriever") != "gnn_pcst":
        raise ExternalReasoningError("Expected real gnn_pcst retrieval, not a different retriever")
    if (graph.get("meta") or {}).get("variant") != "full":
        raise ExternalReasoningError("Expected full knowledge graph variant")
    ids = {e.get("edge_id") for e in graph.get("edges", [])}
    if set(graph.get("edge_ids", [])) != ids:
        raise ExternalReasoningError("Graph returned inconsistent evidence IDs")
    ms = round((time.perf_counter() - t0) * 1000)
    edges = edge_summary(graph, f["curie"])
    mode = "real GNN + deterministic synthesis"
    if use_llm:
        if llm is None:
            from agent.llm import get_llm
            llm = get_llm()
        if type(llm).__name__ == "FakeLLM":
            raise ExternalReasoningError("FakeLLM must not be used for a live public-patient demo")
        draft = llm_draft(f, edges, question, llm)
        mode = "real GNN + LLM synthesis"
    else:
        draft = graph_only_draft(f, edges)
    if demo_rejection:
        draft += ("\n\n### Deliberately injected unsupported claim (demo only)\n\n"
                  "This archival marker proves a drug will cure the patient's current metastases.")
    evidence = {"edge_ids": [f["evidence_id"]] + sorted(ids), "pmids": []}
    names = {f["evidence_id"]: [f["symbol"], f["sample_label"], f["feature"]]}
    node_names = {n["curie"]: n.get("name") or n["curie"] for n in graph.get("nodes", [])}
    for e in graph.get("edges", []):
        names[e["edge_id"]] = [node_names.get(e["src_curie"], e["src_curie"]),
                                node_names.get(e["dst_curie"], e["dst_curie"]),
                                e["src_curie"], e["dst_curie"]]
    if verifier is None:
        from agent.verifier import verify
        verifier = verify
    # Efficient and predictable: use repo's real verifier with its deterministic
    # claim splitting mode; don't make an additional expensive LLM call.
    with patch.dict(os.environ, {"VERIFIER_SPLITTER": "rules"}):
        claims, answer, stats, splitter = verifier(draft, evidence, llm, edge_names=names, strict=True)
    return {
        "source_release": case.get("release"), "generated_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": "youngcrc", "finding_id": f["finding_id"],
        "specimen_id": f["sample_id"], "source_ids": f["source_ids"],
        "source_sha256": case.get("sha256", {}),
        "observation": observation_sentence(f), "mode": mode, "question": question.strip(),
        "graph_backend": "gnn_pcst", "graph_variant": "full",
        "graph_meta": graph.get("meta", {}),
        "n_graph_nodes": len(graph.get("nodes", [])),
        "n_graph_edges": len(graph.get("edges", [])),
        "selected_edges": edges,
        "tool_calls": [
            {"name": "get_external_observation", "args": {"finding_id": f["finding_id"]},
             "result": {"finding_id": f["finding_id"], "source_ids": f["source_ids"],
                        "evidence_id": f["evidence_id"], "sample_id": f["sample_id"],
                        "value": f["value"], "feature": f["feature"]}, "ms": 0},
            {"name": "retrieve_subgraph", "args": args, "result": graph, "ms": ms},
        ],
        "evidence": evidence, "draft": draft,
        "claims": claims, "answer": answer, "verifier": stats,
        "verifier_splitter": splitter,
        "limitations": [
            "Graph association and citation-ID matching do not prove causal or clinical entailment.",
            "The underlying marker is historical; no current-node state is inferred.",
            "No kidney-cancer risk head, patient-level drug efficacy or survival prediction was run.",
            "The optional unsupported claim is injected for verifier testing, not generated by the model.",
        ],
    }


def run_source_check(case: dict, selected_finding_id: str, demo_rejection: bool = True,
                     verifier=None) -> dict:
    """Genuine public-source verification when the large KG assets are unavailable.

    No GNN/LLM claim is made. This is deliberately a separate execution mode.
    """
    f = next((x for x in available_markers(case) if x["finding_id"] == selected_finding_id), None)
    if f is None:
        raise ExternalReasoningError("The selected marker is not linked to a published assay and source")
    draft = "### Source-linked finding\n\n" + observation_sentence(f)
    if demo_rejection:
        draft += ("\n\n### Deliberately injected unsupported claim (demo only)\n\n"
                  "This archival marker proves a drug will cure the patient's current metastases.")
    if verifier is None:
        from agent.verifier import verify
        verifier = verify
    ev = {"edge_ids": [f["evidence_id"]], "pmids": []}
    with patch.dict(os.environ, {"VERIFIER_SPLITTER": "rules"}):
        claims, answer, stats, splitter = verifier(
            draft, ev, None, edge_names={f["evidence_id"]:
                                         [f["symbol"], f["sample_label"], f["feature"]]}, strict=True)
    return {
        "source_release": case.get("release"), "generated_utc": datetime.now(timezone.utc).isoformat(),
        "case_id": "youngcrc", "finding_id": f["finding_id"], "specimen_id": f["sample_id"],
        "source_ids": f["source_ids"], "source_sha256": case.get("sha256", {}),
        "observation": observation_sentence(f), "mode": "source-only verifier (no GNN / no LLM)",
        "question": "Is the released observation traceable, and does it support a clinical efficacy claim?",
        "graph_backend": "not run", "graph_variant": "not run", "graph_meta": {},
        "n_graph_nodes": 0, "n_graph_edges": 0, "selected_edges": [],
        "tool_calls": [{"name": "get_external_observation", "args": {"finding_id": f["finding_id"]},
                        "result": {"finding_id": f["finding_id"], "feature": f["feature"],
                                   "value": f["value"], "source_ids": f["source_ids"],
                                   "evidence_id": f["evidence_id"]}, "ms": 0}],
        "evidence": ev, "draft": draft, "claims": claims, "answer": answer,
        "verifier": stats, "verifier_splitter": splitter,
        "limitations": ["No GNN or LLM was executed in this source-only mode.",
                        "Reference validation does not establish a treatment response."]}
