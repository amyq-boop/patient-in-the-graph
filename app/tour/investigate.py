"""Chapter 5/6 and the workspace: run the repo's real GNN-B retrieval + verifier on Alexander Young, save the
trace, replay saved real traces, and write a source-linked case review.

All execution goes through `app.external_reasoner` (teammate's module, unchanged): it refuses mock retrieval,
the FakeLLM, and any retriever other than gnn_pcst on the full graph. This module adds capability checks,
trace persistence under cache/demo/youngcrc/ (committed, like the other demo traces), and the review writer.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.external_reasoner import (DEFAULT_QUESTION, MARKERS, ExternalReasoningError, available_markers,
                                   graph_prerequisites, run_case, run_source_check)
from common.contracts import P

TRACE_DIR = P.demo_cache / "youngcrc"
SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def capabilities() -> dict:
    missing_files = graph_prerequisites()
    missing_pkgs = [m for m in ("torch", "torch_geometric", "pcst_fast", "sentence_transformers")
                    if importlib.util.find_spec(m) is None]
    try:
        from agent import config as agent_config
        llm = bool(agent_config.NIM_API_KEY)
        model = getattr(agent_config, "NIM_MODEL", None) or getattr(agent_config, "LLM_MODEL", None)
    except Exception:  # noqa: BLE001
        llm, model = False, None
    return {"gnn_ready": not missing_files and not missing_pkgs, "missing_files": missing_files,
            "missing_packages": missing_pkgs, "llm_ready": llm, "llm_model": model,
            "default_question": DEFAULT_QUESTION,
            "how_to_enable": ("Extract the team data kit into data/processed (nodes, edges, evidence parquet, "
                              "feat_text.pt, emb_main.pt), `pip install -r requirements.txt` (pcst_fast from source, "
                              "see models/retriever.py), then restart. Add NVIDIA_API_KEY to .env for LLM synthesis.")}


def markers(case: dict) -> list[dict]:
    return [{"finding_id": m["finding_id"], "feature": m["feature"], "value": m["value"], "unit": m.get("unit"),
             "symbol": m["symbol"], "curie": m["curie"], "evidence_id": m["evidence_id"],
             "sample_id": m["sample_id"], "sample_label": m["sample_label"], "sample_date": m["sample_date"],
             "assay_label": m["assay_label"], "source_ids": m["source_ids"]}
            for m in available_markers(case)]


def _save(trace: dict) -> str:
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    kind = "gnn" if trace.get("graph_backend") == "gnn_pcst" else "source"
    name = SAFE.sub("_", f"{trace['finding_id']}__{kind}__{stamp}.json")
    (TRACE_DIR / name).write_text(json.dumps(trace, indent=1, default=str), encoding="utf-8")
    return name


def _real_gnn(t: dict) -> bool:
    meta = t.get("graph_meta") or {}
    return (t.get("graph_backend") == "gnn_pcst" and meta.get("retriever") == "gnn_pcst"
            and meta.get("variant") == "full" and not meta.get("mock") and t.get("case_id") == "youngcrc")


def run(case: dict, finding_id: str, question: str, use_llm: bool, inject: bool, budget: int = 50) -> dict:
    """Real run, or a clear refusal. Never substitutes saved or mock output for a live request."""
    caps = capabilities()
    if not caps["gnn_ready"]:
        return {"ok": False, "reason": "gnn_unavailable", "capabilities": caps}
    try:
        t = run_case(case, finding_id, question or DEFAULT_QUESTION, use_llm=use_llm and caps["llm_ready"],
                     demo_rejection=inject, budget=budget)
    except ExternalReasoningError as exc:
        return {"ok": False, "reason": "run_failed", "error": str(exc), "capabilities": caps}
    t["saved_as"] = _save(t)
    return {"ok": True, "trace": t, "replayed": False}


def source_check(case: dict, finding_id: str, inject: bool) -> dict:
    try:
        t = run_source_check(case, finding_id, demo_rejection=inject)
    except ExternalReasoningError as exc:
        return {"ok": False, "reason": "run_failed", "error": str(exc)}
    return {"ok": True, "trace": t, "replayed": False}


def saved() -> list[dict]:
    out = []
    for f in sorted(TRACE_DIR.glob("*.json"), reverse=True) if TRACE_DIR.exists() else []:
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out.append({"name": f.name, "finding_id": t.get("finding_id"), "mode": t.get("mode"),
                    "generated_utc": t.get("generated_utc"), "real_gnn": _real_gnn(t),
                    "n_graph_edges": t.get("n_graph_edges"), "source_release": t.get("source_release")})
    return out


def load_saved(name: str) -> dict:
    path = (TRACE_DIR / SAFE.sub("_", name)).resolve()
    if TRACE_DIR.resolve() not in path.parents or not path.exists():
        return {"ok": False, "reason": "not_found"}
    t = json.loads(path.read_text(encoding="utf-8"))
    if t.get("graph_backend") == "gnn_pcst" and not _real_gnn(t):
        return {"ok": False, "reason": "not_a_real_run"}
    return {"ok": True, "trace": t, "replayed": True, "saved_as": path.name}


# ------------------------------------------------------------------------------------------- shaping for the UI

def view(trace: dict) -> dict:
    """The trace plus the retrieved subgraph in a display-ready form (nodes, edges, claim -> IDs)."""
    rs = next((c["result"] for c in trace.get("tool_calls", []) if c.get("name") == "retrieve_subgraph"), None) or {}
    nodes = rs.get("nodes", [])
    edges = [{"edge_id": e.get("edge_id"), "src": e.get("src_curie"), "dst": e.get("dst_curie"), "rel": e.get("rel"),
              "origin": e.get("origin"), "pmids": e.get("pmids") or [], "quote": e.get("quote"), "citable": True,
              "provenance": "returned by retrieve_subgraph in this run"} for e in rs.get("edges", [])]
    claims = []
    for c in trace.get("claims", []):
        ids = re.findall(r"\b(E\d+|pt:[^\s\]\),;\"']+|PMID:\d+)", " ".join([str(c.get("quote") or ""),
                                                                       json.dumps(c.get("support") or {})]))
        claims.append({**c, "cited_ids": list(dict.fromkeys(ids)),
                       "kind": ("removed" if c.get("status") == "removed"
                                else "hypothesis" if c.get("status") == "hypothesis" or c.get("is_hypothesis")
                                else "supported" if c.get("status") == "supported" else c.get("status") or "other")})
    seed = next((c["result"] for c in trace.get("tool_calls", []) if c.get("name") == "get_external_observation"), {})
    return {"trace": {k: v for k, v in trace.items() if k != "tool_calls"},
            "graph": {"nodes": nodes, "edges": edges, "meta": rs.get("meta", {})},
            "claims": claims, "seed": seed}


def review_markdown(trace: dict, patient: dict) -> str:
    """A source-linked case review. Observations, graph facts, verifier output and limits are kept apart."""
    v = view(trace)
    t = v["trace"]
    find = next((f for f in patient.get("findings", []) if f["finding_id"] == t.get("finding_id")), None)
    lines = [f"# Research case review: {patient.get('display_name', 'public case')}",
             "",
             f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} by Patient-in-the-Graph. "
             "Research use only. Not medical advice, not a treatment recommendation.",
             "",
             "## 1. Data and provenance",
             f"- Source: Alexander Young, *Open Cancer Data*, {patient.get('base_url')} (release {t.get('source_release')}, "
             f"loaded from the {'live site' if patient.get('data_source') == 'live' else 'hash-verified offline snapshot'}).",
             f"- Run mode: {t.get('mode')}. " + (f"Graph backend: {t.get('graph_backend')} on the {t.get('graph_variant')} graph."
                                               if t.get("graph_backend") == "gnn_pcst" else "No graph retrieval in this mode."),
             f"- Trace generated {t.get('generated_utc')}" + (f", saved as `cache/demo/youngcrc/{trace['saved_as']}`."
                                                              if trace.get("saved_as") else "."),
             "", "## 2. Observed (source-linked) finding"]
    if find:
        lines += [f"- **{find['feature']} = {find['value']}{(' ' + find['unit']) if find['unit'] else ''}**",
                  f"- Specimen: {find['sample_label']} ({find['sample_id']}), collected {find['sample_date'] or 'date not stated'}.",
                  f"- Assay: {find['assay_label']}. Evidence class: {find['evidence_class']}.",
                  f"- Current metastatic-node status: {find['current_node_status']} (this is a historical specimen).",
                  "- Sources: " + "; ".join(f"[{s['id']}] {s['label']}" + (f" ({s['url']})" if s.get('url') else "")
                                           for s in find["sources"])]
    lines += ["", "## 3. Knowledge-graph evidence retrieved in this run"]
    if v["graph"]["edges"]:
        names = {n["curie"]: n.get("name") or n["curie"] for n in v["graph"]["nodes"]}
        lines.append(f"{t.get('n_graph_nodes')} nodes and {t.get('n_graph_edges')} edges returned by GNN-B "
                     f"(gnn_pcst). Edges touching the seed are listed first:")
        for e in t.get("selected_edges", [])[:12]:
            lines.append(f"- [{e['edge_id']}] {e['source']} --{e['relation']}--> {e['target']} ({e['origin']})")
    else:
        lines.append("No graph retrieval was run in this mode; no graph edges are cited.")
    claims = v["claims"]
    sup = [c for c in claims if c["kind"] == "supported"]
    hyp = [c for c in claims if c["kind"] == "hypothesis"]
    rem = [c for c in claims if c["kind"] == "removed"]
    lines += ["", "## 4. Verifier-supported statements",
              "Supported means every cited ID was returned in this trace and names an entity in the sentence. "
              "It does not establish biological or clinical truth."]
    lines += [f"- {c['text']} [{', '.join(c['cited_ids']) or 'no ID'}]" for c in sup] or ["- None."]
    lines += ["", "## 5. Hypotheses for research follow-up (not findings)"]
    lines += [f"- {c['text']}" for c in hyp] or [
        "- Graph associations above are hypotheses about relevance to this tumour. None was tested in this patient."]
    lines += ["", "## 6. Statements the verifier removed"]
    lines += [f"- ~~{c['text']}~~" for c in rem] or ["- None."]
    lines += ["", "## 7. What this review does not conclude"]
    lines += [f"- {x}" for x in t.get("limitations", [])]
    lines += ["- The CPTAC kidney-cancer stage model was not run and its outputs do not apply to this colorectal case.",
              "", "## 8. File hashes checked against the release manifest"]
    lines += [f"- `{h[:16]}...` {p}" for p, h in (patient.get("sha256") or {}).items()]
    return "\n".join(lines) + "\n"
