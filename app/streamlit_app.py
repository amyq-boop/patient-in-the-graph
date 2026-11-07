"""Patient-in-the-Graph demo (WS4 / Demo).

    streamlit run app/streamlit_app.py          # from the repo root

Renders traces in the CONTRACTS.md section 8 shape: cache/demo/ first, then app/mock_traces/.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
import streamlit.components.v1 as components  # noqa: E402
from pyvis.network import Network  # noqa: E402

from app import data  # noqa: E402
from common.contracts import CCRCC_ID, patient_curie  # noqa: E402

st.set_page_config(page_title="Patient-in-the-Graph", layout="wide")

COLOR = {"patient": "#E4572E", "gene": "#2E86AB", "disease": "#7B2CBF", "pathway": "#3BB273",
         "phenotype": "#F3A712", "drug": "#C2185B", "mechanism": "#6D597A", "biological_process": "#3BB273"}
CITED = "#E4572E"
MOCK_PMID = re.compile(r"PMID:0+\d$")


# ------------------------------------------------------------------ helpers
def ok(tc, name):
    r = tc.get("result")
    return tc.get("name") == name and isinstance(r, dict) and "error" not in r


def build_graph(trace):
    """Merge every tool result in the trace into one graph: {curie: node}, {edge_id: edge}."""
    nodes, edges = {}, {}

    def node(curie, name=None, ntype="unknown", **kw):
        n = nodes.setdefault(curie, {"curie": curie, "name": name or curie, "type": ntype})
        for k, v in kw.items():
            if v is not None:
                n[k] = v

    for tc in trace.get("tool_calls", []):
        r = tc.get("result")
        if ok(tc, "retrieve_subgraph"):
            for n in r["nodes"]:
                node(n["curie"], n.get("name"), n.get("type", "unknown"), prize=n.get("prize"), score=n.get("score"))
            for e in r["edges"]:
                edges[e["edge_id"]] = {"id": e["edge_id"], "src": e["src_curie"], "dst": e["dst_curie"],
                                       "rel": e["rel"], "origin": e.get("origin", ""), "bifo": e.get("bifo"), "pmids": []}
        elif ok(tc, "get_patient"):
            pc = r.get("curie") or patient_curie(r["patient_id"])
            node(pc, r["patient_id"], "patient")
            for key, rel in (("phenotypes", "has_phenotype"), ("proteins_up", "protein_up"), ("proteins_down", "protein_down")):
                for x in r.get(key, []):
                    node(x["curie"], x.get("name") or x.get("symbol"), "phenotype" if key == "phenotypes" else "gene")
                    z = f" (z={x['z']:+.1f})" if "z" in x else ""
                    edges[x["edge_id"]] = {"id": x["edge_id"], "src": pc, "dst": x["curie"], "rel": rel + z,
                                           "origin": "cptac", "bifo": "observational", "pmids": []}
        elif ok(tc, "get_mechanism"):
            dis = r.get("disease", CCRCC_ID)
            node(dis, r.get("name"), "disease")
            for ch in r.get("chains", []):
                prev = dis
                for s in ch.get("steps", []):
                    node(s["mechanism"], s.get("label"), "mechanism")
                    eid = s.get("edge_id") or f"{prev}->{s['mechanism']}"
                    edges[eid] = {"id": eid, "src": prev, "dst": s["mechanism"], "rel": "mechanism step",
                                  "origin": "dismech", "bifo": None, "pmids": s.get("pmids", [])}
                    prev = s["mechanism"]
    for e in edges.values():  # endpoints we only know by ID
        node(e["src"]); node(e["dst"])
    return nodes, edges


def cited(trace):
    sup = [c for c in trace.get("claims", []) if c.get("status") == "supported"]
    return ({e for c in sup for e in c["support"].get("edge_ids", [])},
            {p for c in sup for p in c["support"].get("pmids", [])})


def render_graph(nodes, edges, cited_edges, cited_pmids):
    net = Network(height="560px", width="100%", directed=True, bgcolor="#ffffff", cdn_resources="in_line")
    net.barnes_hut(gravity=-5000, spring_length=130)
    for n in nodes.values():
        size = 14 + 14 * float(n.get("prize") or 0.4)
        tip = f"{n['name']} ({n['type']})\n{n['curie']}"
        if isinstance(n.get("score"), dict):
            tip += "\nscore: " + ", ".join(f"{k} {v:.2f}" for k, v in n["score"].items())
        elif n.get("prize") is not None:
            tip += f"\nretrieval prize: {n['prize']:.2f}"
        net.add_node(n["curie"], label=n["name"][:28], title=tip, color=COLOR.get(n["type"], "#9aa0a6"), size=size)
    for e in edges.values():
        hit = e["id"] in cited_edges or bool(set(e["pmids"]) & cited_pmids)
        tip = f"[{e['id']}] {e['rel']} ({e['origin']})" + (f"\nBIFO: {e['bifo']}" if e.get("bifo") else "")
        if e["pmids"]:
            tip += "\n" + ", ".join(e["pmids"])
        net.add_edge(e["src"], e["dst"], title=tip, width=4 if hit else 1.5,
                     color=CITED if hit else "#b0b6bc", dashes=e.get("bifo") == "observational")
    components.html(net.generate_html(), height=580)


def ref_label(ref, nodes, edges):
    if ref.startswith("PMID:"):
        if MOCK_PMID.match(ref):
            return f"`{ref}` (mock)"
        return f"[{ref}](https://pubmed.ncbi.nlm.nih.gov/{ref.split(':')[1]}/)"
    e = edges.get(ref)
    if e:
        s, d = nodes.get(e["src"], {}).get("name", e["src"]), nodes.get(e["dst"], {}).get("name", e["dst"])
        return f"`{ref}` {s} → {e['rel']} → {d}"
    return f"`{ref}`"


def show_trace(trace, source):
    nodes, edges = build_graph(trace)
    ce, cp = cited(trace)
    v = trace.get("verifier", {})

    if source == "mock" or any("MOCK" in n for n in trace.get("notes", [])):
        st.caption("⚠️ Mock trace: fake IDs and PMIDs, generated offline. Real traces replace these in cache/demo/.")
    m = st.columns(4)
    m[0].metric("Claims", v.get("n_claims", len(trace.get("claims", []))))
    m[1].metric("Supported", v.get("n_supported", 0))
    m[2].metric("Hypotheses", v.get("n_hypothesis", 0))
    m[3].metric("Removed by verifier", v.get("n_removed", 0))

    left, right = st.columns([5, 6])
    with left:
        st.subheader("Answer")
        st.markdown(trace.get("answer", ""))
        st.subheader("Claim check")
        for c in trace.get("claims", []):
            refs = c["support"].get("edge_ids", []) + c["support"].get("pmids", [])
            c = {**c, "text": re.sub(r"^\s*hypothesis:\s*", "", c["text"].replace(" .", "."), flags=re.I)}
            status = c.get("status")
            if status == "supported":
                st.markdown(f"✅ {c['text']}")
                for r in refs:
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;<small>{ref_label(r, nodes, edges)}</small>", unsafe_allow_html=True)
            elif status == "hypothesis":
                st.markdown(f"💡 *Hypothesis:* {c['text']}")
            else:
                st.markdown(f"<span style='color:#9aa0a6'>✖ <s>{c['text']}</s> (no supporting edge or PMID)</span>",
                            unsafe_allow_html=True)
        with st.expander("How the agent reasoned"):
            st.dataframe(pd.DataFrame([{"tool": tc["name"], "ms": tc.get("ms", 0),
                                        "ok": "error" not in (tc.get("result") or {})}
                                       for tc in trace.get("tool_calls", [])]), hide_index=True)
    with right:
        st.subheader("Evidence graph")
        render_graph(nodes, edges, ce, cp)
        legend = " &nbsp;".join(f"<span style='color:{c}'>●</span> {t}" for t, c in COLOR.items() if t != "biological_process")
        st.markdown(f"<small>{legend}<br><b style='color:{CITED}'>━</b> cited by a supported claim &nbsp; "
                    "┄ patient measurement &nbsp; node size = retrieval prize</small>", unsafe_allow_html=True)


# ------------------------------------------------------------------ sidebar
pids = data.patients()
pid = st.sidebar.selectbox("Patient", pids)
pt = data.patient(pid)
info, pred = pt["info"], pt["pred"]
if "error" not in pred:
    st.sidebar.metric(f"Predicted risk ({pred.get('label', '')})", f"{pred['risk']:.0%}")
    st.sidebar.caption("Out-of-fold GNN estimate. Drivers: " +
                       ", ".join(x["name"] for x in pred.get("top_contributing", [])))
if "error" not in info:
    st.sidebar.markdown("**Clinical summary**")
    st.sidebar.write(info.get("clinical_summary", ""))
    for key, title in (("phenotypes", "Phenotypes"), ("proteins_up", "Proteins up"), ("proteins_down", "Proteins down")):
        items = info.get(key, [])
        st.sidebar.markdown(f"**{title}**")
        st.sidebar.write(", ".join((x.get("symbol") or x["name"]) + (f" ({x['z']:+.1f})" if "z" in x else "")
                                   for x in items) or "none")
else:
    st.sidebar.warning(info["error"])

# ------------------------------------------------------------------ main
st.title("Patient-in-the-Graph")
st.caption("A GNN picks the evidence from PrimeKG + DisMech; the agent answers from it; the verifier removes "
           "every claim without a graph edge or PMID.")
tab_ask, tab_results = st.tabs(["Ask", "Results"])

with tab_ask:
    qs = data.demo_questions()
    if st.session_state.get("pid") != pid:
        st.session_state.update(pid=pid, trace=None, source=None)
    cols = st.columns(max(len(qs), 1))
    for col, q in zip(cols, qs):
        if col.button(q["question"], key=q["qid"]):
            t, src = data.trace(pid, q["qid"])
            st.session_state.update(trace=t, source=src, missing=None if t else q["question"])
    typed = st.chat_input("Ask about this patient")
    if typed:
        t = data.ask_live(pid, typed)
        if t is None:
            st.info("Live agent is off (set BIOHACK_LIVE=1 to enable). Pick one of the cached questions above.")
        else:
            st.session_state.update(trace=t, source="live")
    if st.session_state.get("trace"):
        t = st.session_state.trace
        st.markdown(f"#### {t['question']}")
        show_trace(t, st.session_state.source)
    elif st.session_state.get("missing"):
        st.warning("No cached trace for this patient and question yet.")
    else:
        st.write("Pick a question to see the agent's answer and the evidence behind it.")

with tab_results:
    r = data.results()
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**E1 · The GNN finds the mechanism** (Recall@50 on held-out diseases)")
        if r["e1"] is not None:
            st.bar_chart(r["e1"].groupby("method")["recall50"].mean())
        else:
            st.info("Waiting for eval/results/e1.csv")
        st.markdown("**E3 · Every claim is checkable** (share of claims supported)")
        if r["e3"] is not None:
            g = r["e3"].groupby("mode")[["n_supported", "n_claims"]].sum()
            st.bar_chart(g["n_supported"] / g["n_claims"])
        else:
            st.info("Waiting for eval/results/e3.csv")
    with c2:
        st.markdown("**E2 · Patients in the graph predict better** (AUROC across 5 folds)")
        if r["e2"] is not None:
            g = r["e2"].groupby("model")["auroc"].agg(["mean", "std"]).round(3)
            st.dataframe(g)
            st.bar_chart(g["mean"])
        else:
            st.info("Waiting for eval/results/e2.csv")
        st.markdown("**E4 · It rediscovers ccRCC biology on its own**")
        key = r["e4_key"]
        if key:
            chain = [s["label"] for s in key["core_chain"]] + ["{" + ", ".join(s["label"] for s in key["downstream_of_hif2"]) + "}"]
            st.write("Expected (DisMech, hidden from the agent): " + " → ".join(chain))
        if r["e4"] is not None:
            e4 = r["e4"]
            st.write(f"Recovered {len(e4.get('recovered_steps', []))} of {len(e4.get('expected_steps', []))} steps")
            st.write("Drugs surfaced: " + ", ".join(e4.get("drugs_surfaced", [])))
        else:
            st.info("Waiting for eval/results/e4.json")
