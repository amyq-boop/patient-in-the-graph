"""Patient-in-the-Graph: evidence-grounded answers about one patient's tumor.

    streamlit run app/streamlit_app.py          # from the repo root
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from app import data  # noqa: E402
from common.contracts import patient_curie  # noqa: E402

# Fill in names for the "How it works" credits.
TEAM = {"Knowledge graph + patient layer": "", "GNN encoder + retriever": "", "Agent + verifier": "",
        "Product, demo + pitch": ""}

st.set_page_config(page_title="Patient-in-the-Graph", page_icon="🧬", layout="wide")
st.markdown("""
<style>
html, body, [class*="css"] { font-size: 17px; }
.block-container { padding-top: 1.6rem; max-width: 1400px; }
.hero h1 { font-size: 2.3rem; margin-bottom: .1rem; }
.hero p { color:#52514e; font-size:1.08rem; margin:0 0 .4rem 0; }
.badge { display:inline-block; padding:2px 10px; margin:2px 6px 2px 0; border-radius:999px;
         background:#eef3fb; color:#1f4f8f; font-size:.8rem; }
.warn  { background:#fff3e8; color:#9a4a12; }
.chip  { display:inline-block; min-width:1.4em; padding:0 6px; margin:0 1px; border-radius:6px;
         background:#2a78d6; color:#fff; font-size:.72rem; font-weight:600; text-align:center; vertical-align:super; }
.answer { font-size:1.15rem; line-height:1.65; }
.verdict { font-size:1rem; padding:8px 12px; border-radius:8px; background:#f4f6f8; margin:.4rem 0 .8rem 0; }
.muted { color:#898781; font-size:.85rem; }
.claim-removed { color:#9aa0a6; text-decoration:line-through; }
.kv b { display:inline-block; min-width:5.5em; color:#52514e; font-weight:500; }
</style>
""", unsafe_allow_html=True)

SHORT = {"P01": "Risk & targets", "P02": "Hypertension cause", "P03": "Proteins → symptoms",
         "P04": "Driver genes", "P05": "Model confidence"}
TYPE_COLOR = {"patient": "#E4572E", "gene": "#2a78d6", "disease": "#6250d6", "pathway": "#1baf7a",
              "phenotype": "#eda100", "drug": "#d14a8b", "mechanism": "#52514e"}
REL_WORDS = {"disease_protein": "linked to", "drug_protein": "targets", "protein_protein": "interacts with",
             "pathway_protein": "in pathway", "dm_contributes_to": "contributes to", "protein_up": "up in tumor",
             "protein_down": "down in tumor", "has_phenotype": "has", "causes": "causes", "indication": "treats"}
ID_RE = re.compile(r"\[((?:E\d+|pt:[^\]\s,;]+|PMID:\s?\d+)(?:\s*[,;]\s*(?:E\d+|pt:[^\]\s,;]+|PMID:\s?\d+))*)\]")
MOCK_PMID = re.compile(r"PMID:0+\d$")


# ------------------------------------------------------------------ evidence model
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
        elif ok(tc, "get_mechanism"):
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


def dot(nodes, edges, keys, focus=None):
    lines = ['digraph G { rankdir=LR; bgcolor="transparent"; pad=0.2; nodesep=0.25; ranksep=0.35;',
             'node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=30, fontcolor="white", '
             'margin="0.25,0.12", penwidth=0];',
             'edge [fontname="Helvetica", fontsize=24, fontcolor="#52514e", color="#9aa0a6", penwidth=1.6];']
    used = {n for k in keys for n in (edges[k]["src"], edges[k]["dst"])}
    for c in used:
        n = nodes[c]
        label = html.escape(n["name"][:40]).replace('"', "'")
        lines.append(f'"{c}" [label="{label}", fillcolor="{TYPE_COLOR.get(n["type"], "#898781")}"];')
    for k in keys:
        e = edges[k]
        words = REL_WORDS.get(e["rel"], e["rel"].replace("_", " "))
        hi = focus is None or k in focus
        lines.append(f'"{e["src"]}" -> "{e["dst"]}" [label="{words}", color="{"#E4572E" if hi else "#c3c1b8"}", '
                     f'penwidth={2.6 if hi else 1.4}];')
    return "\n".join(lines) + "}"


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


def path_html(nodes, edges, keys, focus=None):
    def chip(c):
        n = nodes[c]
        return (f"<span style='background:{TYPE_COLOR.get(n['type'], '#898781')};color:#fff;padding:6px 12px;"
                f"border-radius:8px;font-weight:600;white-space:nowrap'>{html.escape(n['name'])}</span>")

    def arrow(e):
        words = REL_WORDS.get(e["rel"], e["rel"].replace("_", " "))
        return (f"<span style='display:inline-flex;flex-direction:column;align-items:center;margin:0 8px;"
                f"color:#52514e;font-size:.8rem;line-height:1.1'>{words}<span style='font-size:1.3rem;color:#E4572E'>"
                "⟶</span></span>")

    rows = []
    for chain in paths(edges, keys):
        lit = focus is None or any(k in focus for k in chain)
        parts = [chip(edges[chain[0]]["src"])]
        for k in chain:
            parts += [arrow(edges[k]), chip(edges[k]["dst"])]
        rows.append(f"<div style='display:flex;align-items:center;flex-wrap:wrap;gap:2px;margin:10px 0;"
                    f"opacity:{1 if lit else .25}'>{''.join(parts)}</div>")
    return "".join(rows)


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
    origin = "patient measurement" if ref.startswith("pt:") else "knowledge graph"
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


# ------------------------------------------------------------------ views
def patient_card(pid):
    pt = data.patient(pid)
    info, pred = pt["info"], pt["pred"]
    with st.container(border=True):
        st.markdown(f"<span class='muted'>PATIENT</span><br><b style='font-size:1.3rem'>{pid}</b>", unsafe_allow_html=True)
        if "error" in info:
            st.caption(f"Patient data unavailable: {info['error']}")
            return
        st.markdown(info.get("clinical_summary", ""))
        if "error" not in pred:
            r = pred["risk"]
            tier = "Higher" if r >= 0.6 else ("Intermediate" if r >= 0.4 else "Lower")
            st.markdown(f"**{tier} stage risk** · model score {r:.2f}")
            st.markdown('<span class="muted">Out-of-fold estimate from ~100 tumors. A research signal, '
                        "not a clinical prediction.</span>", unsafe_allow_html=True)
        rows = [("Findings", ", ".join(x["name"] for x in info.get("phenotypes", [])) or "none"),
                ("Up in tumor", ", ".join(f"{x.get('symbol') or x['name']} ({x['z']:+.1f})" for x in info.get("proteins_up", [])) or "none"),
                ("Down in tumor", ", ".join(f"{x.get('symbol') or x['name']} ({x['z']:+.1f})" for x in info.get("proteins_down", [])) or "none")]
        st.markdown("".join(f"<div class='muted' style='margin-top:.5rem'>{k.upper()}</div><div>{html.escape(v)}</div>"
                            for k, v in rows),
                    unsafe_allow_html=True)
        st.markdown('<span class="muted">Protein values: z-score vs. matched normal kidney.</span>',
                    unsafe_allow_html=True)


def show_trace(t, source):
    nodes, edges, steps = evidence(t)
    claims = t.get("claims", [])
    sup = [c for c in claims if c.get("status") == "supported"]
    hyp = [c for c in claims if c.get("status") == "hypothesis"]
    rem = [c for c in claims if c.get("status") == "removed"]

    st.markdown(f"### {t['question']}")
    if source == "mock":
        st.markdown('<span class="badge warn">Demo data · placeholder IDs and citations</span>', unsafe_allow_html=True)
    left, right = st.columns([3, 2], gap="large")
    with left:
        body, order = render_answer(t.get("answer", ""), nodes, edges)
        st.markdown(f"<div class='answer'>{body}</div>", unsafe_allow_html=True)
    with right:
        st.markdown(f"<div class='verdict'>✅ <b>{len(sup)}</b> claims backed by evidence<br>"
                    f"💡 <b>{len(hyp)}</b> labeled as hypothesis<br>✖ <b>{len(rem)}</b> removed by the verifier</div>",
                    unsafe_allow_html=True)
        if rem or hyp:
            with st.expander("What the verifier caught", expanded=True):
                for c in rem:
                    st.markdown(f"<span class='claim-removed'>{html.escape(c['text'])}</span><br>"
                                "<span class='muted'>no edge or citation · removed</span>", unsafe_allow_html=True)
                for c in hyp:
                    st.markdown(f"💡 {c['text']}<br><span class='muted'>kept as a hypothesis to test</span>",
                                unsafe_allow_html=True)
        if order:
            with st.expander(f"Sources ({len(order)})"):
                for i, ref in enumerate(order, 1):
                    st.markdown(f"**{i}.** {source_label(ref, nodes, edges)}")
    with st.container(border=True):
        st.markdown("#### Why we believe it")
        options = ["All supporting evidence"] + [c["text"] for c in sup]
        pick = st.selectbox("Show the evidence for", options, key=f"pick_{t['qid']}", label_visibility="collapsed")
        all_keys = list(dict.fromkeys(k for c in sup for k in claim_edges(c, edges, steps)))
        if pick == options[0]:
            keys, focus = all_keys, None
        else:
            c = sup[options.index(pick) - 1]
            focus = claim_edges(c, edges, steps)
            keys = all_keys
            refs = c["support"].get("edge_ids", []) + c["support"].get("pmids", [])
            st.caption("Cited: " + " · ".join(source_label(r, nodes, edges).replace("[", "").split("](")[0] for r in refs))
        if keys:
            st.markdown(path_html(nodes, edges, keys, set(focus) if focus else None), unsafe_allow_html=True)
        else:
            st.info("No graph path for this answer; it rests on the patient's own measurements.")
        legend = " ".join(f"<span style='color:{c}'>■</span> {k}" for k, c in TYPE_COLOR.items())
        st.markdown(f"<span class='muted'>{legend} &nbsp;·&nbsp; faded rows = evidence for other claims</span>",
                    unsafe_allow_html=True)
        with st.expander("Everything the GNN retrieved"):
            sub = next((tc["result"] for tc in t.get("tool_calls", []) if ok(tc, "retrieve_subgraph")), None)
            if sub:
                meta = sub.get("meta", {})
                st.caption(f"{len(sub['nodes'])} nodes picked from {meta.get('n_candidates', '?')} candidates · "
                           f"retriever {meta.get('retriever', '?')} · budget {meta.get('budget', '?')}")
                st.graphviz_chart(dot(nodes, edges, [e["edge_id"] for e in sub["edges"] if e["edge_id"] in edges],
                                      focus=set(all_keys)))
            st.dataframe(pd.DataFrame([{"tool": tc["name"], "ms": tc.get("ms", 0),
                                        "ok": "error" not in (tc.get("result") or {})} for tc in t["tool_calls"]]),
                         hide_index=True)


def chat_panel(pid):
    st.markdown("### 💬 Ask the agent")
    st.caption("Ask anything about this patient. Every answer comes with its evidence.")
    hist = st.session_state.setdefault("chat", {}).setdefault(pid, [])
    for i, m in enumerate(hist):
        with st.chat_message(m["role"]):
            st.markdown(m["text"])
            if m.get("qid"):
                if st.button("Show the evidence →", key=f"open_{pid}_{i}"):
                    st.session_state.qid = m["qid"]
                    st.rerun()
    with st.form(f"chat_{pid}", clear_on_submit=True):
        q = st.text_input("Question", placeholder="e.g. Which drugs could target this tumor?",
                          label_visibility="collapsed")
        sent = st.form_submit_button("Ask")
    if sent and q.strip():
        hist.append({"role": "user", "text": q})
        r = data.chat(pid, q)
        if r["trace"]:
            body, _ = render_answer(r["trace"]["answer"], *evidence(r["trace"])[:2])
            plain = re.sub(r"<[^>]+>", "", re.sub(r'<span class="chip">\d+</span>', "", body).replace("<br>", "\n"))
            plain = re.sub(r" +([.,])", r"\1", plain)
            v = r["trace"].get("verifier", {})
            note = ("" if r["mode"] == "live" else f"\n\n_Closest verified run: “{r['trace']['question']}”_")
            hist.append({"role": "assistant", "qid": r["qid"],
                         "text": f"{plain}\n\n✅ {v.get('n_supported', 0)} backed · ✖ {v.get('n_removed', 0)} removed{note}"})
        else:
            sugg = "\n".join(f"- {x['question']}" for x in data.demo_questions())
            hist.append({"role": "assistant", "text": "I don't have a verified answer for that one yet. "
                                                      f"I can answer these right now:\n{sugg}"})
        st.rerun()
    if not data.LIVE:
        st.caption("Demo mode: answers come from verified, cached agent runs.")


# ------------------------------------------------------------------ page
st.markdown("""<div class="hero"><h1>🧬 Patient-in-the-Graph</h1>
<p>Ask why a patient's tumor behaves the way it does. Get an answer where every claim links to evidence.</p>
<span class="badge">For translational oncology teams</span><span class="badge">PrimeKG · 4M facts</span>
<span class="badge">DisMech curated mechanisms</span><span class="badge">CPTAC kidney cancer patients</span>
<span class="badge">GNN on NVIDIA L40S</span><span class="badge">LLM on NVIDIA NIM</span></div>""",
            unsafe_allow_html=True)

pids = data.patients()
top = st.columns([2, 5])
pid = top[0].selectbox("Patient", pids)
if st.session_state.get("pid") != pid:
    st.session_state.update(pid=pid, qid=st.query_params.get("q"))  # ?q=P01 opens a step directly

tab_case, tab_how, tab_results = st.tabs(["Patient case", "How it works", "Results"])

with tab_case:
    side, main = st.columns([1, 3], gap="large")
    with side:
        patient_card(pid)
        st.markdown("")
        chat_panel(pid)
    with main:
        qs = data.demo_questions()
        st.markdown("**Walk through the case**")
        cols = st.columns(len(qs) or 1)
        for i, (col, q) in enumerate(zip(cols, qs), 1):
            if col.button(f"{i}. {SHORT.get(q['qid'], q['question'][:28])}", help=q["question"], key=f"q_{q['qid']}", use_container_width=True,
                          type="primary" if st.session_state.get("qid") == q["qid"] else "secondary"):
                st.session_state.qid = q["qid"]
        qid = st.session_state.get("qid")
        if qid:
            t, src = data.trace(pid, qid)
            if t:
                show_trace(t, src)
            else:
                st.warning("No verified run for this question yet.")
        else:
            st.info("Pick a step above. Each answer shows its evidence path and what the verifier removed.")

with tab_how:
    st.markdown("### From a patient's proteins to a cited answer")
    st.graphviz_chart("""digraph { rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled",
      fontname="Helvetica", fontsize=14, fillcolor="#eef3fb", color="#2a78d6", margin="0.2,0.1"];
      edge [color="#898781"];
      d [label="Data\\nPrimeKG · DisMech · CPTAC"]; g [label="Knowledge graph\\none ID per entity\\npatients as nodes"];
      n [label="GNN\\nscores which facts matter\\nfor this question"]; a [label="Agent (NIM)\\nplans · calls tools\\ndrafts answer"];
      v [label="Verifier\\nkeeps only claims with\\nan edge or a PMID"]; u [label="Answer\\n+ evidence path"];
      d -> g -> n -> a -> v -> u; }""")
    c1, c2, c3 = st.columns(3)
    c1.markdown("**Why a GNN?**\n\nThe graph has ~4 million facts; an LLM can't read them. The GNN picks the "
                "~50 that matter for this patient and question.")
    c2.markdown("**Why a verifier?**\n\nLLMs make things up. Every sentence must cite something the tools "
                "actually returned, or it's removed or labeled a hypothesis.")
    c3.markdown("**Who it's for**\n\nTranslational researchers generating hypotheses. Not a diagnostic and "
                "not for treatment decisions.")
    st.markdown("#### Built by")
    st.markdown(" · ".join(f"**{role}**" + (f" ({name})" if name else "") for role, name in TEAM.items()))

with tab_results:
    r = data.results()
    st.caption("Charts update automatically as each result file lands in eval/results/.")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**E1 · Does the GNN find hidden mechanisms?** Recall@50 on held-out diseases")
        if r["e1"] is not None:
            st.bar_chart(r["e1"].groupby("method")["recall50"].mean())
        else:
            st.info("Waiting for eval/results/e1.csv")
        st.markdown("**E3 · Are the agent's claims backed?** Share of claims supported")
        if r["e3"] is not None:
            g = r["e3"].groupby("mode")[["n_supported", "n_claims"]].sum()
            st.bar_chart(g["n_supported"] / g["n_claims"])
        else:
            st.info("Waiting for eval/results/e3.csv")
    with c2:
        st.markdown("**E2 · Does putting patients in the graph help predict stage?** AUROC, 5 folds")
        if r["e2"] is not None:
            g = r["e2"].groupby("model")["auroc"].agg(["mean", "std"]).round(3).sort_values("mean")
            st.dataframe(g)
            st.bar_chart(g["mean"])
        else:
            st.info("Waiting for eval/results/e2.csv")
        st.markdown("**E4 · Does it rediscover kidney-cancer biology on its own?**")
        key = r["e4_key"]
        if key:
            expected = [s["label"] for s in key["core_chain"] + key["downstream_of_hif2"]]
            got = set((r["e4"] or {}).get("recovered_steps", []))
            for s in expected:
                st.markdown(("✅ " if s in got else "⬜ ") + s)
            if r["e4"]:
                st.caption("Drugs surfaced: " + (", ".join(r["e4"].get("drugs_surfaced", [])) or "none"))
            else:
                st.caption("Expected chain from DisMech, hidden from the agent. Waiting for eval/results/e4.json")
