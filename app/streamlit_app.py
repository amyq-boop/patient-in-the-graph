"""Patient-in-the-Graph: answers about a patient's tumor, each claim traced to its evidence.

    streamlit run app/streamlit_app.py            # from the repo root
    ?q=P01 ... P05   open a step directly         ?view=demo   compact view for the live pitch
Results come from the same validated files as the slide charts (eval/make_charts.py), so site and deck agree.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from app import data  # noqa: E402
from app.evidence import REL_WORDS, claim_edges, evidence, ok, render_answer, source_label  # noqa: E402

TEAM = {"Knowledge graph and patient layer": "", "GNN encoder and retriever": "",  # fill in teammates' names
        "Agent and verifier": "", "Product, demo and pitch": "Amy Qin"}
STEPS = {"P01": "Risk", "P02": "Hypertension", "P03": "Mechanism", "P04": "Driver genes", "P05": "Confidence"}

# Color carries meaning, the same way on the site, graph and slides:
#   observed in this patient = eosin pink, curated knowledge = hematoxylin violet, drug = amber
ROLE = {"observed": ("#fbe3ea", "#a3304f", "#d6457a"), "known": ("#e9e6f7", "#3b2f8f", "#4f3fbf"),
        "drug": ("#f6edd2", "#6b5100", "#b88a00")}
LABEL = {"observed": "Observed in this patient", "known": "Known biology", "drug": "Drug and its target"}
VIEW = st.query_params.get("view", "full")

st.set_page_config(page_title="Patient-in-the-Graph", page_icon="🧬", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500&family=Public+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root { --glass:#fbfafc; --ink:#1b1530; --hema:#4f3fbf; --eosin:#d6457a; --amber:#b88a00; --muted:#5d596b; --rule:#e6e2ee; --body:#332e47; }
header[data-testid="stHeader"], footer, #MainMenu, [data-testid="stToolbar"], [data-testid="stDecoration"] { display:none !important; }
.stApp { background: var(--glass); }
.block-container { max-width: 1180px; padding: 2.2rem 2.5rem 5rem; }
html, body, .stApp, p, li, label, input, button, [data-testid="stMarkdownContainer"] { font-family: "Public Sans", system-ui, sans-serif; color: var(--ink); }
h1, h2, h3, .serif { font-family: "Newsreader", Georgia, serif !important; font-weight: 400 !important; color: var(--ink) !important; letter-spacing: -0.01em; }
a { color: var(--hema); }
.nav { display:flex; justify-content:space-between; align-items:baseline; padding-bottom:1.1rem; border-bottom:1px solid var(--rule); }
.nav b { font-family:"Newsreader",serif; font-weight:500; font-size:1.25rem; }
.nav a { margin-left:1.6rem; color:var(--muted); text-decoration:none; font-size:.95rem; }
.hero { display:grid; grid-template-columns: 1.2fr .8fr; gap: 3.5rem; align-items:center; margin-top: 3rem; }
.hero h1 { font-size: clamp(2.6rem, 5vw, 4.1rem); line-height: 1.04; margin: 0 0 1.1rem; }
.hero p { font-size: 1.2rem; line-height: 1.6; color: var(--body); max-width: 54ch; margin: 0; }
.hero figure { margin:0; } .hero figure svg { width:100%; height:auto; display:block; border-radius:10px; }
.hero figcaption { font-size:.86rem; color: var(--muted); line-height:1.45; margin-top:.6rem; }
.chain { display:flex; align-items:flex-start; margin: 3rem 0 .8rem; }
.chain .n { flex: 0 1 auto; max-width: 9.5rem; }
.chain .w { font-family:"Newsreader",serif; font-size: 1.45rem; line-height:1.1; padding:.2rem .55rem; border-radius:6px; display:inline-block; }
.chain .c { font-size:.86rem; color: var(--muted); margin-top:.4rem; line-height:1.35; padding-left:.55rem; }
.chain .l { flex: 1 1 3.5rem; min-width: 4.6rem; margin: .95rem .45rem 0; position:relative; text-align:center; }
.chain .l span { font-size:.78rem; color: var(--muted); display:block; margin-bottom:.25rem; white-space:nowrap; }
.chain .l i { display:block; height:0; border-top:2px solid var(--hema); transform-origin:left; animation: draw .5s ease-out both; }
.chain .l.inf i { border-top-style:dashed; border-color: var(--eosin); }
.chain .l.inh i { border-color: var(--amber); position:relative; }
.chain .l.inh i::before { content:""; position:absolute; left:-1px; top:-9px; height:16px; border-left:3px solid var(--amber); }
.chain .l:nth-child(2) i { animation-delay:.1s } .chain .l:nth-child(4) i { animation-delay:.4s }
.chain .l:nth-child(6) i { animation-delay:.7s } .chain .l:nth-child(8) i { animation-delay:1s }
@keyframes draw { from { transform: scaleX(0) } to { transform: scaleX(1) } }
@media (prefers-reduced-motion: reduce) { .chain .l i { animation: none; } }
.legend { font-size:.88rem; color: var(--muted); display:flex; flex-wrap:wrap; gap:1.4rem; }
.legend i { display:inline-block; width:1.6rem; vertical-align:middle; margin-right:.4rem; border-top:2px solid var(--hema); }
.legend i.inf { border-top-style:dashed; border-color:var(--eosin); } .legend i.inh { border-color:var(--amber); border-left:3px solid var(--amber); height:9px; }
.factgroups { display:grid; grid-template-columns: 1fr 1fr; gap: 2.5rem; margin: 3rem 0 0; }
.fg h4 { font-family:"Public Sans",sans-serif; font-weight:600; font-size:.95rem; margin:0 0 .6rem; color: var(--body); }
.facts { display:grid; grid-template-columns: 1fr 1fr; border-top:1px solid var(--rule); border-bottom:1px solid var(--rule); }
.facts div { padding: 1.1rem 1rem 1.2rem 0; font-size: .96rem; line-height:1.45; color: var(--body); }
.facts div + div { padding-left: 1rem; border-left:1px solid var(--rule); }
.facts b { display:block; font-family:"Newsreader",serif; font-weight:400; font-size:2.1rem; color: var(--hema); margin-bottom:.15rem; }
.facts .pending b { color: var(--muted); font-size: 1.5rem; }
.sec { margin: 4.5rem 0 1.4rem; display:flex; justify-content:space-between; align-items:baseline; gap:2rem; }
.sec h2 { font-size: 2.5rem; margin:0; } .sec p { color: var(--muted); max-width: 48ch; margin:0; }
.profile .k { color: var(--muted); font-size:.88rem; margin-top: 1rem; } .profile .v { font-size: 1.02rem; line-height:1.45; }
.score { font-family:"Newsreader",serif; font-size:1.6rem; margin-top:.15rem; }
.q { font-family:"Newsreader",serif; font-size: 1.85rem; line-height:1.2; margin: 1.4rem 0 .9rem; }
.concl { font-family:"Newsreader",serif; font-size: 1.5rem; line-height: 1.4; color: var(--ink); max-width: 56ch; margin-bottom:.8rem; }
.answer { font-family:"Newsreader",serif; font-size: 1.2rem; line-height: 1.6; color: var(--body); max-width: 62ch; }
.chip { display:inline-block; min-width:1.3em; padding:0 5px; margin:0 1px; border-radius:4px; background: var(--hema); color:#fff !important;
        font: 600 .68rem "Public Sans",sans-serif; text-align:center; vertical-align: super; text-decoration:none; }
.tally { display:flex; gap:2.2rem; margin: 1.3rem 0 .4rem; padding: .85rem 0; border-top:1px solid var(--rule); border-bottom:1px solid var(--rule); }
.tally div { font-size:.92rem; color: var(--muted); }
.tally b { display:block; font-family:"Newsreader",serif; font-weight:400; font-size:1.65rem; color: var(--ink); }
.catch { border-left: 3px solid var(--eosin); padding: .7rem 0 .7rem 1rem; margin: 1rem 0; background: #fff; }
.catch .what { color: var(--muted); text-decoration: line-through; text-decoration-color: var(--eosin); font-size:1.05rem; }
.catch .tag { font-weight:600; font-size:.85rem; color:#a3304f; margin-right:.5rem; }
.catch .why { font-size:.95rem; color: var(--body); margin-top:.35rem; line-height:1.5; }
.catch.hyp { border-left-style: dashed; border-color: var(--muted); } .catch.hyp .tag { color: var(--muted); }
.sources { font-size:.92rem; line-height:1.55; color: var(--body); margin-top:.6rem; }
.sources li { margin:.2rem 0; } .sources .kind { color: var(--muted); }
.gcap { font-size:.9rem; color: var(--muted); margin:.2rem 0 .4rem; }
.demo { display:inline-block; font-size:.84rem; color:#6b5100; background:#f6edd2; padding:.15rem .55rem; border-radius:4px; }
.method { display:grid; grid-template-columns: repeat(4,1fr); gap: 2rem; }
.method .i { font-family:"Newsreader",serif; font-size:2.6rem; color: var(--hema); line-height:1; }
.method h3 { font-size:1.35rem; margin:.6rem 0 .4rem; } .method p { color:var(--body); font-size:.97rem; line-height:1.55; }
.bars .row { display:grid; grid-template-columns: 13.5rem 1fr 5.4rem; align-items:center; gap:1rem; margin:.5rem 0; }
.bars .lab { font-size:.95rem; } .bars .trk { height: 14px; background: #efecf4; border-radius: 3px; }
.bars .fill { height: 14px; border-radius: 3px; background:#c9c4d6; } .bars .ours .fill { background: var(--hema); }
.bars .ours .lab, .bars .ours .val { font-weight:600; }
.bars .val { font-family:"Newsreader",serif; font-size:1.2rem; text-align:right; white-space:nowrap; }
.bars .sep { border-top:1px dashed var(--rule); margin:.8rem 0 .3rem; padding-top:.4rem; font-size:.86rem; color:var(--muted); }
.result h3 { font-size:1.6rem; margin: 0 0 .3rem; } .result .lead { color:var(--body); max-width: 60ch; line-height:1.55; margin-bottom: .8rem; }
.pend { font-size:.9rem; color: var(--muted); border:1px dashed var(--rule); border-radius:6px; padding:.6rem .8rem; display:inline-block; }
.steprow { display:flex; align-items:center; gap:.6rem; margin:.35rem 0; font-size:1rem; }
.steprow .dot { width:12px; height:12px; border-radius:50%; border:2px solid var(--hema); } .steprow .dot.on { background: var(--hema); }
.foot { margin-top: 5rem; padding-top: 1.4rem; border-top:1px solid var(--rule); color: var(--muted); font-size:.9rem; line-height:1.6; display:flex; justify-content:space-between; gap:2rem; }
div.stButton > button { border-radius:6px; border:1px solid var(--rule); background:#fff; color: var(--ink); font-weight:500; padding:.7rem .9rem; font-size:1rem; }
div.stButton > button:hover { border-color: var(--hema); color: var(--hema); }
div.stButton > button[kind="primary"], button[data-testid="stBaseButton-primary"] { background: var(--hema) !important; border-color: var(--hema) !important; color:#fff !important; }
button[data-testid="stBaseButton-primary"] p { color:#fff !important; }
div.stButton > button p { white-space: normal; line-height: 1.25; }
div.stButton > button:focus-visible { outline: 2px solid var(--eosin); outline-offset: 2px; }
[data-testid="stExpander"] { border:none; border-top:1px solid var(--rule); border-radius:0; background:transparent; }
[data-baseweb="select"] > div { border-radius:6px; border-color: var(--rule); background:#fff; }
[data-testid="stChatMessage"] { background:#fff; border:1px solid var(--rule); border-radius:8px; }
@media (max-width: 860px) { .hero, .factgroups { grid-template-columns: 1fr; } .method { grid-template-columns: 1fr 1fr; } .sec { display:block; } .chain { flex-wrap:wrap; } }
</style>
""", unsafe_allow_html=True)


def esc(s):
    return html.escape(str(s))


# ------------------------------------------------------------------ evidence roles + graph drawing
def edge_role(key, e):
    if key.startswith("pt:"):
        return "observed"
    if e["rel"] in ("drug_protein", "indication", "drug_effect"):
        return "drug"
    return "known"


def node_role(n, observed_nodes):
    if n["type"] == "patient" or n["curie"] in observed_nodes:
        return "observed"
    return "drug" if n["type"] == "drug" else "known"


def graph_svg(nodes, edges, keys, cited, limit=18):
    """Layered evidence graph from the tools' real results: patient | measured | known biology | drugs."""
    observed = {e["dst"] for k, e in edges.items() if k.startswith("pt:")}
    keep = list(dict.fromkeys(n for k in keys for n in (edges[k]["src"], edges[k]["dst"])))
    hot = {n for k in cited for n in (edges[k]["src"], edges[k]["dst"])}
    if len(keep) > limit:
        keep = ([n for n in keep if n in hot] + [n for n in keep if n not in hot])[:limit]
    keepset = set(keep)
    keys = [k for k in keys if edges[k]["src"] in keepset and edges[k]["dst"] in keepset]

    def col(c):
        n = nodes[c]
        if n["type"] == "patient":
            return 0
        if c in observed:
            return 1
        return 3 if n["type"] == "drug" else 2

    cols = {i: [c for c in keep if col(c) == i] for i in range(4)}
    W, X = 900, [100, 315, 560, 805]
    rows = max(len(v) for v in cols.values()) or 1
    H = 48 + rows * 60
    pos = {}
    for i, cs in cols.items():
        top = (H - len(cs) * 60) / 2 + 36
        for j, c in enumerate(cs):
            pos[c] = (X[i], top + j * 60)
    out = [f'<svg viewBox="0 0 {W} {H}" width="100%" role="img" aria-label="Evidence graph: the facts the tools returned, '
           f'with the ones cited in the answer highlighted" style="font-family:Public Sans,sans-serif">']
    for k in sorted(keys, key=lambda k: k in cited):
        e = edges[k]
        (x1, y1), (x2, y2) = pos[e["src"]], pos[e["dst"]]
        role = edge_role(k, e)
        hit = k in cited
        color = ROLE[role][2] if hit else "#d9d5e3"
        dash = ' stroke-dasharray="5 4"' if role == "observed" else ""
        mx = (x1 + x2) / 2
        out.append(f'<path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" fill="none" stroke="{color}" '
                   f'stroke-width="{3 if hit else 1.6}"{dash}><title>{esc(REL_WORDS.get(e["rel"], e["rel"]))} '
                   f'[{esc(k)}]</title></path>')
        if role == "drug" and hit:  # inhibition bar at the target end
            tx = x2 + (16 if x1 > x2 else -16)
            out.append(f'<line x1="{tx}" y1="{y2 - 8}" x2="{tx}" y2="{y2 + 8}" stroke="{color}" stroke-width="3"/>')
    for c, (x, y) in pos.items():
        n = nodes[c]
        fill, ink, _ = ROLE[node_role(n, observed)]
        label = n["name"] if len(n["name"]) <= 22 else n["name"][:21] + "…"
        w = min(230, max(54, 10 * len(label) + 26))
        op = 1 if (c in hot or not cited) else .7
        out.append(f'<g opacity="{op}"><rect x="{x - w / 2}" y="{y - 18}" width="{w}" height="36" rx="7" fill="{fill}"/>'
                   f'<text x="{x}" y="{y + 6}" text-anchor="middle" font-size="17" font-weight="500" fill="{ink}">'
                   f'{esc(label)}</text><title>{esc(n["curie"])}</title></g>')
    for i, t in enumerate(["Patient", "Measured or on record", "Known biology", "Drugs"]):
        if cols[i]:
            out.append(f'<text x="{X[i]}" y="18" text-anchor="middle" font-size="15" fill="#5d596b">{t}</text>')
    out.append("</svg>")
    return "".join(out)


def split_conclusion(body):
    i = body.find(". ")
    return (body[: i + 1], body[i + 2:]) if 0 < i < 400 else (body, "")


# ------------------------------------------------------------------ results (shared with the slide charts)
@st.cache_data(ttl=60)
def results():
    from eval import make_charts as M
    out = {}
    for name in ("e1", "e2", "e3", "e4"):
        try:
            L = M.load(name, data.P.results, ROOT / "eval" / "e4_answer_key.json")
            out[name] = {"status": "PLACEHOLDER" if L.fake else "REAL", "data": L.data, "notes": L.notes}
        except M.ValidationError as e:
            out[name] = {"status": "ERROR", "data": None, "notes": [str(e)]}
    out["M"] = {"E1": dict(M.E1_METHODS), "E2": dict(M.E2_MODELS)}
    try:
        import json
        out["n_pat"] = json.loads((data.P.results / "e2_summary.json").read_text())["y_stage"]["n"]
    except Exception:  # noqa: BLE001
        out["n_pat"] = None
    return out


R = results()


def real(k):
    return R[k]["status"] == "REAL"


E1 = None
if real("e1"):
    m = R["e1"]["data"].groupby(["kind", "method"]).recall50.mean()
    g = m["gene"]
    base = g.drop("gnn_pcst").max()
    E1 = {"ours": g["gnn_pcst"], "base": base, "ratio": g["gnn_pcst"] / base if base else None, "m": m,
          "n": R["e1"]["data"].disease.nunique(), "llm": "llm_only" in set(R["e1"]["data"].method)}


def bars(rows, fmt):
    top = max([v for _, v, _, _ in rows] + [1e-9])
    out = []
    for label, v, ours, extra in rows:
        if label is None:
            out.append(f"<div class='sep'>{esc(extra)}</div>")
            continue
        out.append(f"<div class='row{' ours' if ours else ''}'><div class='lab'>{esc(label)}</div>"
                   f"<div class='trk'><div class='fill' style='width:{100 * v / top:.1f}%'></div></div>"
                   f"<div class='val'>{fmt(v)}{esc(extra or '')}</div></div>")
    return "<div class='bars'>" + "".join(out) + "</div>"


def pend(what):
    st.markdown(f"<div class='pend'>Pending: {esc(what)}</div>", unsafe_allow_html=True)


# ------------------------------------------------------------------ hero
st.markdown("""<div class="nav"><b>Patient-in-the-Graph</b>
<span><a href="#case">The case</a><a href="#method">How it works</a><a href="#results">Results</a></span></div>""",
            unsafe_allow_html=True)
if VIEW != "demo":
    TISSUE = (Path(__file__).parent / "assets" / "tissue.svg").read_text()
    st.markdown(f"""<div class="hero"><div><h1>Answers about a tumor, with every claim traced to its source.</h1>
<p>Pick a kidney cancer patient and ask why their tumor behaves the way it does. A graph neural network picks the
relevant facts out of millions, an agent writes the answer, and a verifier removes any sentence it can't back up.</p></div>
<figure>{TISSUE}<figcaption>Clear cell kidney cancer gets its name from how it looks under the microscope: the tumor cells
seem empty because their fat and sugar stores wash out during staining. Illustration.</figcaption></figure></div>""",
                unsafe_allow_html=True)
    fo, ko, do = (ROLE[r][0] for r in ("observed", "known", "drug"))
    fi, ki, di = (ROLE[r][1] for r in ("observed", "known", "drug"))
    st.markdown(f"""<div class="chain">
<div class="n"><div class="w" style="background:{fo};color:{fi}">Low VHL protein</div><div class="c">measured in this patient's tumor</div></div>
<div class="l inf"><span>fits VHL loss</span><i></i></div>
<div class="n"><div class="w" style="background:{ko};color:{ki}">HIF-2α builds up</div><div class="c">VHL normally clears it</div></div>
<div class="l"><span>drives</span><i></i></div>
<div class="n"><div class="w" style="background:{ko};color:{ki}">VEGF</div><div class="c">grows new blood vessels</div></div>
<div class="l"><span>activates</span><i></i></div>
<div class="n"><div class="w" style="background:{ko};color:{ki}">KDR</div><div class="c">the VEGF receptor</div></div>
<div class="l inh"><span>blocked by</span><i></i></div>
<div class="n"><div class="w" style="background:{do};color:{di}">Sunitinib</div><div class="c">a kinase inhibitor</div></div></div>
<div class="legend"><span><i class="inf"></i>inference from a patient measurement</span><span><i></i>curated mechanism, cited</span>
<span><i class="inh"></i>drug inhibition</span><span>Low VHL protein fits VHL loss; it doesn't prove a mutation.</span></div>""",
                unsafe_allow_html=True)

    n_pat = R["n_pat"] or "~100"
    if E1 and E1["ratio"]:
        e1card = (f"<div><b>{E1['ratio']:.1f}×</b>more hidden disease genes found than the next-best graph method "
                  f"({E1['ours']:.0%} vs {E1['base']:.0%}, {E1['n']} held-out diseases)</div>")
    else:
        e1card = "<div class='pending'><b>Pending</b>retrieval test on held-out diseases (E1)</div>"
    if real("e3"):
        from eval.make_charts import e3_shares
        _, sh = e3_shares(R["e3"]["data"])
        e3card = (f"<div><b>{sh.loc['agent', 'n_supported']:.0%}</b>of the agent's claims backed by evidence, vs "
                  f"{sh.loc['llm_only', 'n_supported']:.0%} for the same LLM without tools</div>")
    else:
        e3card = "<div class='pending'><b>Pending</b>share of claims backed by evidence, agent vs LLM alone (E3)</div>"
    st.markdown(f"""<div class="factgroups">
<div class="fg"><h4>What the graph holds</h4><div class="facts"><div><b>4.05M</b>connections between genes, drugs,
diseases and symptoms (PrimeKG), plus curated DisMech mechanisms</div><div><b>{n_pat}</b>kidney cancer patients from CPTAC,
each added to the graph with their tumor's proteins</div></div></div>
<div class="fg"><h4>Measured so far</h4><div class="facts">{e1card}{e3card}</div></div></div>""", unsafe_allow_html=True)

# ------------------------------------------------------------------ the case
st.markdown("<div id='case'></div><div class='sec'><h2>The case</h2><p>One patient, five questions. Each answer shows what "
            "was measured, what the GNN retrieved, and what the verifier removed.</p></div>", unsafe_allow_html=True)
pids = data.patients()
pid_col, _ = st.columns([1, 3])
pid = pid_col.selectbox("Patient", pids, label_visibility="collapsed")
if st.session_state.get("pid") != pid:
    st.session_state.update(pid=pid, qid=st.query_params.get("q"), live_trace=None)

qs = data.demo_questions()
cols = st.columns(len(qs) or 1)
for i, (col, qq) in enumerate(zip(cols, qs), 1):
    if col.button(f"{i}. {STEPS.get(qq['qid'], qq['question'][:24])}", key=f"q_{qq['qid']}", help=qq["question"],
                  use_container_width=True, type="primary" if st.session_state.get("qid") == qq["qid"] else "secondary"):
        st.session_state.update(qid=qq["qid"], live_trace=None)
        st.rerun()

left, right = st.columns([1, 2.6], gap="large")
with left:
    pt = data.patient(pid)
    info, pred = pt["info"], pt["pred"]
    if "error" in info:
        st.markdown(f"<p style='color:#5d596b'>No patient profile available: {esc(info['error'])}</p>", unsafe_allow_html=True)
    else:
        def fmt(xs):
            return ", ".join(f"{esc(x.get('symbol') or x['name'])} ({x['z']:+.1f})" for x in xs) or "none"

        rows = [("Patient", f"<span class='serif' style='font-size:1.45rem'>{esc(pid)}</span>"),
                ("Summary", esc(info.get("clinical_summary", "")))]
        if "error" not in pred:
            rows.append(("Model score, stage III–IV vs I–II", f"<div class='score'>{pred['risk']:.2f}</div>"
                         "<div style='font-size:.86rem;color:#5d596b'>Uncalibrated research model, scored out of fold. "
                         "Not a probability, and not for clinical decisions.</div>"))
        rows += [("Findings on record", esc(", ".join(x["name"] for x in info.get("phenotypes", [])) or "none")),
                 ("Protein higher than normal kidney", fmt(info.get("proteins_up", []))),
                 ("Protein lower than normal kidney", fmt(info.get("proteins_down", [])))]
        st.markdown("<div class='profile'>" + "".join(f"<div class='k'>{k}</div><div class='v'>{v}</div>" for k, v in rows)
                    + "<div class='k'>Values are z-scores against matched normal kidney tissue.</div></div>",
                    unsafe_allow_html=True)

    live = data.LIVE
    st.markdown(f"<h3 class='serif' style='font-size:1.4rem;margin:2.2rem 0 .3rem'>"
                f"{'Ask the agent' if live else 'Find a verified answer'}</h3><p style='font-size:.9rem;color:#5d596b'>"
                + ("Runs the agent live on your question." if live else
                   "Matches your question to the closest verified run of the agent. Live questions are off for the demo.")
                + "</p>", unsafe_allow_html=True)
    hist = st.session_state.setdefault("chat", {}).setdefault(pid, [])
    for i, msg in enumerate(hist):
        with st.chat_message(msg["role"]):
            st.markdown(msg["text"])
            if msg.get("qid") and st.button("Open this answer", key=f"open_{pid}_{i}"):
                st.session_state.update(qid=msg["qid"], live_trace=None)
                st.rerun()
    with st.form(f"chat_{pid}", clear_on_submit=True):
        q = st.text_input("Question", placeholder="Which drugs could act on this tumor?", label_visibility="collapsed")
        sent = st.form_submit_button("Ask" if live else "Find")
    if sent and q.strip():
        hist.append({"role": "user", "text": q})
        res = data.chat(pid, q)
        if res["trace"] and res["mode"] == "live":
            hist.append({"role": "assistant", "text": "Answer ready on the right."})
            st.session_state.update(qid=None, live_trace=res["trace"])
        elif res["trace"]:
            hist.append({"role": "assistant", "qid": res["qid"], "text": f"Closest verified run: *{res['trace']['question']}*"})
        else:
            hist.append({"role": "assistant", "text": "No verified run matches that yet. Pick one of the five steps above."})
        st.rerun()

with right:
    qid = st.session_state.get("qid")
    if qid:
        t, src = data.trace(pid, qid)
    else:
        t, src = st.session_state.get("live_trace"), "live"
    if not t:
        st.markdown("<p class='q' style='color:#5d596b'>" + ("There's no verified run for this step and patient yet."
                    if qid else "Start with step 1: what drives this patient's model score, and what would you target?")
                    + "</p>", unsafe_allow_html=True)
    else:
        nodes, edges, steps = evidence(t)
        claims = t.get("claims", [])
        sup = [c for c in claims if c.get("status") == "supported"]
        hyp = [c for c in claims if c.get("status") == "hypothesis"]
        rem = [c for c in claims if c.get("status") == "removed"]
        demo = "<span class='demo'>Demo data: placeholder IDs and citations</span>" if src == "mock" else ""
        st.markdown(f"{demo}<div class='q'>{esc(t['question'])}</div>", unsafe_allow_html=True)
        body, order = render_answer(t.get("answer", ""), nodes, edges)
        body = re.sub(r'<span class="chip">(\d+)</span>', r'<a class="chip" href="#src-\1">\1</a>', body)
        concl, rest = split_conclusion(body)
        st.markdown(f"<div class='concl'>{concl}</div><div class='answer'>{rest}</div>", unsafe_allow_html=True)
        st.markdown(f"<div class='tally'><div><b>{len(sup)}</b>claims backed by evidence</div>"
                    f"<div><b>{len(hyp)}</b>kept as hypotheses</div><div><b>{len(rem)}</b>removed by the verifier</div></div>",
                    unsafe_allow_html=True)

        def catch(c, as_hyp=False):
            why = c.get("reason") or ("Plausible but untested, so it is kept and labeled." if as_hyp else
                                     "Nothing the tools returned supports it: no edge ID or PMID.")
            txt = esc(c["text"]) if as_hyp else f"<span class='what'>{esc(c['text'])}</span>"
            return (f"<div class='catch{' hyp' if as_hyp else ''}'><span class='tag'>"
                    f"{'Kept as hypothesis' if as_hyp else 'Removed'}</span>{txt}<div class='why'>{esc(why)}</div></div>")

        if rem:  # the verifier, visible by default
            st.markdown(catch(rem[0]), unsafe_allow_html=True)
        more = rem[1:] + hyp
        if more:
            with st.expander(f"Everything else the verifier flagged ({len(more)})"):
                st.markdown("".join(catch(c) for c in rem[1:]) + "".join(catch(c, True) for c in hyp),
                            unsafe_allow_html=True)

        if order:  # every citation number resolves to a line here
            items = []
            for i, ref in enumerate(order, 1):
                if ref.startswith("pt:"):
                    kind = "observed in this patient"
                elif ref.startswith("PMID"):
                    kind = "PubMed"
                elif ref in edges:
                    kind = LABEL[edge_role(ref, edges[ref])].lower()
                else:
                    kind = "citation"
                label = source_label(ref, nodes, edges)
                label = label if ref.startswith("PMID") else label.rsplit(" · ", 1)[0]
                items.append(f"<li id='src-{i}'>{label} "
                             f"<span class='kind'>({kind}, {esc(ref)})</span></li>")
            st.markdown("<ol class='sources'>" + "".join(items) + "</ol>", unsafe_allow_html=True)

        sub = next((tc["result"] for tc in t.get("tool_calls", []) if ok(tc, "retrieve_subgraph")), None)
        cited = list(dict.fromkeys(k for c in sup for k in claim_edges(c, edges, steps)))
        keys = list(dict.fromkeys([k for k in edges if k.startswith("pt:")] +
                                  ([e["edge_id"] for e in sub["edges"] if e["edge_id"] in edges] if sub else []) + cited))
        st.markdown("<h3 class='serif' style='font-size:1.4rem;margin:1.8rem 0 .1rem'>What the GNN retrieved</h3>",
                    unsafe_allow_html=True)
        n_cand = (sub or {}).get("meta", {}).get("n_candidates")
        if sub and isinstance(n_cand, int):
            st.markdown(f"<p class='gcap'>For this question the GNN picked {len(sub['nodes'])} facts out of {n_cand:,} "
                        "candidates. Colored lines are the ones the answer cites; grey ones were retrieved but not used.</p>",
                        unsafe_allow_html=True)
        else:
            st.markdown("<p class='gcap'>Colored lines are cited in the answer; grey ones were retrieved but not used.</p>",
                        unsafe_allow_html=True)
        pick_opts = ["All cited evidence"] + [c["text"] for c in sup]
        pick = st.selectbox("Highlight", pick_opts, key=f"pick_{t.get('qid')}", label_visibility="collapsed")
        focus = cited if pick == pick_opts[0] else claim_edges(sup[pick_opts.index(pick) - 1], edges, steps)
        if keys:
            st.markdown(graph_svg(nodes, edges, keys, set(focus)), unsafe_allow_html=True)
        legend = "".join(f"<span><i style='border-color:{ROLE[r][2]};{'border-top-style:dashed' if r == 'observed' else ''}'>"
                         f"</i>{LABEL[r]}</span>" for r in ROLE)
        st.markdown(f"<div class='legend' style='margin-top:.3rem'>{legend}<span>A bar at the end of a drug line means it "
                    "inhibits its target.</span></div>", unsafe_allow_html=True)

# ------------------------------------------------------------------ method + results
if VIEW != "demo":
    st.markdown("<div id='method'></div><div class='sec'><h2>How it works</h2><p>Four stages run for every question, each "
                "built and tested by a different member of the team.</p></div>", unsafe_allow_html=True)
    st.markdown("""<div class="method">
<div><div class="i">1</div><h3>One graph</h3><p>PrimeKG, DisMech's curated disease mechanisms and each CPTAC patient's
proteins and findings, merged into one knowledge graph with a single ID per gene, drug or symptom.</p></div>
<div><div class="i">2</div><h3>The GNN picks the evidence</h3><p>A graph neural network trained on NVIDIA GPUs scores which
facts matter for this patient and question, and hands over about 50 of them.</p></div>
<div><div class="i">3</div><h3>The agent answers</h3><p>An LLM on NVIDIA NIM plans, calls the graph tools and drafts an
answer. It never sees the whole graph, only what the tools return.</p></div>
<div><div class="i">4</div><h3>The verifier checks</h3><p>Every sentence must cite an edge or PubMed paper the tools actually
returned. Anything else is removed or labeled a hypothesis.</p></div></div>""", unsafe_allow_html=True)

    st.markdown("<div id='results'></div><div class='sec'><h2>Results</h2><p>Read from the same validated result files "
                "as our slides. Tests that haven't finished say so.</p></div>", unsafe_allow_html=True)
    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("<div class='result'><h3>Finding hidden biology</h3>", unsafe_allow_html=True)
        if E1:
            st.markdown(f"<p class='lead'>For {E1['n']} diseases we hid their known genes, then asked each method to find "
                        f"them again within 50 results. Ours recovered {E1['ours']:.0%}; the next best graph method, "
                        f"{E1['base']:.0%}.</p>", unsafe_allow_html=True)
            g = E1["m"]["gene"].sort_values(ascending=False)
            names = R["M"]["E1"]
            st.markdown(bars([(names.get(k, k).replace(" (ours)", ""), v, k == "gnn_pcst", None) for k, v in g.items()],
                             lambda v: f"{v:.0%}"), unsafe_allow_html=True)
            gg = E1["m"]["go"]
            st.markdown(f"<p style='font-size:.9rem;color:#5d596b'>Gene-function terms: {gg['gnn_pcst']:.0%} for ours vs "
                        f"{gg.drop('gnn_pcst').max():.0%} for the next best."
                        + ("" if E1["llm"] else " The LLM-alone comparison is still running.") + "</p>",
                        unsafe_allow_html=True)
        else:
            pend("retrieval on held-out diseases (E1)")
        st.markdown("</div><div class='result' style='margin-top:2.4rem'><h3>Is every claim backed?</h3>",
                    unsafe_allow_html=True)
        if real("e3"):
            from eval.make_charts import e3_shares
            _, sh = e3_shares(R["e3"]["data"])
            st.markdown(f"<p class='lead'>Share of all claims, before verification, that cite evidence the tools returned "
                        f"({R['e3']['data'].qid.nunique()} questions).</p>", unsafe_allow_html=True)
            st.markdown(bars([("Our agent", sh.loc["agent", "n_supported"], True, None),
                              ("Same LLM, no tools", sh.loc["llm_only", "n_supported"], False, None)],
                             lambda v: f"{v:.0%}"), unsafe_allow_html=True)
        else:
            pend("agent vs the same LLM without tools on 20 questions (E3), running tonight")
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        st.markdown("<div class='result'><h3>Predicting cancer stage</h3>", unsafe_allow_html=True)
        if real("e2"):
            from eval.make_charts import e2_stats
            gst = e2_stats(R["e2"]["data"])
            names = R["M"]["E2"]
            main = gst.drop(index=[m for m in ("gnn_mut",) if m in gst.index]).sort_values("mean", ascending=False)
            top = main["mean"].round(2)
            leaders = [names[m].replace(" (ours)", "") for m in main.index if top[m] == top.iloc[0]]
            lead = (f"{' and '.join(leaders)} tie at {top.iloc[0]:.2f}" if len(leaders) > 1 else
                    f"{leaders[0]} scores highest ({top.iloc[0]:.2f})")
            st.markdown(f"<p class='lead'>Across 5 fixed folds of {R['n_pat'] or 'about 100'} patients, {lead}. The spread "
                        "between folds is larger than the gaps between models, so no model clearly wins. The graph adds "
                        "explanations rather than accuracy at this cohort size.</p>", unsafe_allow_html=True)
            rows = [(names[m].replace(" (ours)", ""), r["mean"], m == "gnn", f" ±{r['std']:.2f}")
                    for m, r in main.iterrows()]
            if "gnn_mut" in gst.index:
                r = gst.loc["gnn_mut"]
                rows += [(None, 0, False, "Separate ablation, outside the five planned models"),
                         ("Patient in graph + somatic mutation calls", r["mean"], True, f" ±{r['std']:.2f}")]
            st.markdown(bars(rows, lambda v: f"{v:.2f}"), unsafe_allow_html=True)
            st.markdown("<p style='font-size:.9rem;color:#5d596b'>AUROC, mean ± standard deviation over 5 folds. 0.5 is "
                        "chance, 1.0 is perfect. The ablation adds CPTAC's sequencing-based somatic mutation calls, which "
                        "are separate from the protein measurements.</p>", unsafe_allow_html=True)
        else:
            pend("stage prediction (E2)")
        st.markdown("</div><div class='result' style='margin-top:2.4rem'><h3>Rebuilding a hidden pathway</h3>",
                    unsafe_allow_html=True)
        st.markdown("<p class='lead'>We remove ccRCC's curated DisMech mechanism and PrimeKG's direct ccRCC gene and drug links "
                    "from the graph, block the mechanism lookup for ccRCC, and ask the agent to explain a patient. A step "
                    "counts only if a backed claim names it and cites evidence the tools returned. This tests reconstruction "
                    "under controlled evidence, not discovery: the LLM may already know this biology.</p>",
                    unsafe_allow_html=True)
        if real("e4"):
            d = R["e4"]["data"]
            ev = d.get("step_evidence") or {}
            got = {s for s in d["recovered_steps"] if ev.get(s)}
            st.markdown("".join(f"<div class='steprow'><span class='dot{' on' if s in got else ''}'></span>{esc(s)}"
                                f"<span style='color:#5d596b;font-size:.88rem'>{'' if s in got else ', not recovered'}</span>"
                                f"</div>" for s in d["expected_steps"]), unsafe_allow_html=True)
            st.markdown(f"<p style='font-size:.95rem'>{len(got)} of {len(d['expected_steps'])} steps recovered with "
                        f"citations. Drugs named: {esc(', '.join(d['drugs_surfaced']) or 'none')}.</p>", unsafe_allow_html=True)
        else:
            pend("pathway reconstruction (E4), running tonight now that the lookup leak is fixed")
        st.markdown("</div>", unsafe_allow_html=True)

credits = "<br>".join(f"{esc(role)}{': ' + esc(n) if n else ''}" for role, n in TEAM.items())
st.markdown(f"""<div class="foot"><div>{credits}</div><div style="max-width:42ch">A research tool for generating hypotheses
about tumors. Not for diagnosis or treatment decisions. Built at Nucleate Pittsburgh BioHack 2026 on PrimeKG, DisMech, CPTAC
and NVIDIA.</div></div>""", unsafe_allow_html=True)
