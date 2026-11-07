"""Patient-in-the-Graph: answers about a patient's tumor, each claim traced to its evidence.

    streamlit run app/streamlit_app.py            # from the repo root
    ?q=P01 ... P05   open a step directly         ?view=demo   compact view for the live pitch
Results come from the same validated files as the slide charts (eval/make_charts.py), so site and deck agree.
"""
from __future__ import annotations

import html
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
import streamlit.components.v1  # noqa: E402,F401

from app import data  # noqa: E402
from app import run_view as RV  # noqa: E402
from app.run_view import LABEL, ROLE  # noqa: E402

TEAM = {"Knowledge graph and patient layer": "", "GNN encoder and retriever": "",  # fill in teammates' names
        "Agent and verifier": "", "Product, demo and pitch": "Amy Qin"}
STEPS = {"P01": "Risk", "P02": "Hypertension", "P03": "Mechanism", "P04": "Driver genes", "P05": "Confidence"}
VIEW = st.query_params.get("view", "full")
PACE = 1.0  # replay speed: 1.0 is about 15 seconds per run

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
:root { --hl:#ece7fb; }
.intake { display:grid; grid-template-columns: 1.1fr 1fr 1.6fr; gap:2rem; padding:1.1rem 0 1.3rem; margin:.4rem 0 1.2rem;
          border-top:1px solid var(--rule); border-bottom:1px solid var(--rule); }
.intake .k { color:var(--muted); font-size:.84rem; margin-bottom:.45rem; }
.intake .pid { font-family:"Newsreader",serif; font-size:1.6rem; line-height:1.1; }
.intake .sum { font-size:.95rem; color:var(--body); margin-top:.3rem; line-height:1.45; }
.ic { display:inline-block; font-size:.92rem; padding:.18rem .55rem; border-radius:5px; margin:0 .35rem .4rem 0; }
.ic.obs { background:#fbe3ea; color:#a3304f; } .ic small { margin-left:.35rem; opacity:.75; font-size:.78rem; }
.strip { display:grid; grid-template-columns: repeat(6, 1fr); gap:.5rem; margin:.4rem 0 1.4rem; }
.stg { border-top:3px solid var(--rule); padding-top:.5rem; transition: border-color .3s; }
.stg .nm { font-size:.86rem; font-weight:600; color:var(--muted); } .stg .mt { font-size:.82rem; color:var(--muted); min-height:1.1em; }
.stg .dot { display:none; }
.stg.active { border-color: var(--eosin); } .stg.active .nm { color: var(--ink); }
.stg.active .mt { color:var(--eosin); animation: pulse 1s ease-in-out infinite; }
.stg.done { border-color: var(--hema); } .stg.done .nm { color: var(--ink); } .stg.done .mt { color: var(--hema); }
@keyframes pulse { 50% { opacity:.35 } }
.pred { display:grid; grid-template-columns: auto 1fr; column-gap:1.6rem; row-gap:.2rem; align-items:end;
        padding:1rem 1.2rem; background:#fff; border:1px solid var(--rule); border-radius:8px; margin-bottom:1.2rem; }
.pred .pl { grid-column:1; font-size:.84rem; color:var(--muted); } .pred .pv { grid-column:1; grid-row:2;
        font-family:"Newsreader",serif; font-size:3rem; line-height:1; color:var(--ink); }
.pred .pt { grid-column:2; grid-row:1 / 3; align-self:center; }
.pred .trk { position:relative; height:8px; border-radius:4px; background:linear-gradient(90deg,#e9e6f7,#fbe3ea); }
.pred .trk i { position:absolute; top:-6px; width:4px; height:20px; margin-left:-2px; border-radius:2px; background:var(--ink);
        transition:left .08s linear; }
.pred .ends { display:flex; justify-content:space-between; font-size:.8rem; color:var(--muted); margin-top:.45rem; }
.pred .pn { grid-column:1 / 3; font-size:.95rem; color:var(--body); margin-top:.7rem; line-height:1.5; }
.pred .pn span { font-size:.84rem; color:var(--muted); }
.pc { display:inline-block; background:#fbe3ea; color:#a3304f; border-radius:4px; padding:0 .45rem; margin:0 .2rem; font-size:.88rem; }
.funnel { margin:.3rem 0 1rem; }
.funnel .fr { display:grid; grid-template-columns: 7.5rem 1fr; column-gap:1rem; align-items:center; margin:.35rem 0;
              animation: rise .45s ease-out both; }
.funnel b { font-family:"Newsreader",serif; font-weight:400; font-size:1.5rem; text-align:right; color:var(--ink); }
.funnel span { font-size:.88rem; color:var(--muted); }
.funnel .fb { grid-column:2; height:6px; background:#efecf4; border-radius:3px; }
.funnel .fb i { display:block; height:6px; border-radius:3px; background:var(--hema); animation: grow .7s ease-out both; }
.funnel .fr:last-child .fb i { background:var(--eosin); }
@keyframes grow { from { width:0 } } @keyframes rise { from { opacity:0; transform:translateY(6px) } }
.mchain { display:flex; flex-wrap:wrap; align-items:center; gap:.5rem; margin:.4rem 0 1.2rem; }
.mchain .ms { background:#e9e6f7; color:#3b2f8f; border-radius:6px; padding:.35rem .65rem; font-family:"Newsreader",serif;
              font-size:1.1rem; animation: rise .4s ease-out both; animation-delay:var(--d); }
.mchain .ms small { display:block; font-family:"Public Sans",sans-serif; font-size:.74rem; color:var(--muted); }
.mchain .ar { color:var(--hema); }
.answer p, .answer li { margin:0 0 .7rem; } .answer h4 { font-family:"Public Sans",sans-serif; font-size:1rem; margin:.8rem 0 .3rem; }
.answer ul { padding-left:1.2rem; margin:0 0 .7rem; }
.sn.cite { border-radius:3px; transition: background .12s, box-shadow .12s; cursor:default; }
.sn.cite:hover { background:var(--hl); box-shadow:0 0 0 3px var(--hl); }
.hyptag { font:600 .72rem "Public Sans",sans-serif; color:var(--muted); border:1px dashed var(--muted); border-radius:4px;
          padding:0 .35rem; margin-right:.35rem; vertical-align:middle; }
.draft .dr.new { animation: rise .35s ease-out both; }
.draft .caret { display:inline-block; width:2px; height:1.1em; background:var(--hema); vertical-align:-2px; animation: pulse .8s infinite; }
.draft .dr.strike { color:#a3304f; background:#fbe3ea; border-radius:3px; text-decoration: line-through;
          text-decoration-color: var(--eosin); text-decoration-thickness:2px; animation: flag 1.2s ease-out both; }
@keyframes flag { 0% { background:transparent; color:var(--body); text-decoration-color:transparent }
                  40% { background:#f7c6d6 } 100% { background:#fbe3ea } }
.draft .dr.hyp { animation: rise .4s ease-out both; }
.sub-h { font-family:"Newsreader",serif; font-size:1.4rem; margin:1.8rem 0 .5rem; display:flex; justify-content:space-between;
         align-items:baseline; gap:1rem; }
.sub-h span { font-family:"Public Sans",sans-serif; font-size:.85rem; color:var(--muted); }
.egraph .g-e .vis { stroke:#d9d5e3; stroke-width:1.6px; transition: stroke .15s, stroke-width .15s; }
.egraph .g-e.cited .vis { stroke:var(--c); stroke-width:2.6px; }
.egraph .g-e, .egraph .g-n { transition: opacity .15s; }
.egraph .g-n:not(.hot) { opacity:.8; }
.egraph .g-e .hit, .egraph .g-n { cursor:pointer; }
.egraph.intro .g-n { animation: rise .4s ease-out both; animation-delay: var(--d); }
.egraph.intro .g-e { animation: fade .8s ease-out .6s both; }
@keyframes fade { from { opacity:0 } }
.guide-h { display:flex; flex-direction:column; margin:.2rem 0 .8rem; }
.guide-h b { font-family:"Newsreader",serif; font-weight:400; font-size:1.5rem; }
.guide-h span { font-size:.84rem; color:var(--muted); }
.guide { display:flex; flex-direction:column-reverse; gap:.6rem; margin-bottom:.8rem; max-height:56vh; overflow-y:auto;
         padding-right:.3rem; scrollbar-width:thin; }
div[data-testid="stColumn"]:has(.guide-h) { position:sticky; top:1rem; align-self:flex-start; }
#runtop { scroll-margin-top:1rem; }
.gm { flex-shrink:0; font-size:.94rem; line-height:1.5; padding:.65rem .8rem; border-radius:10px; animation: rise .35s ease-out both; }
.gm.bot { background:#fff; border:1px solid var(--rule); border-top-left-radius:3px; color:var(--body); }
.gm.user { background:var(--hema); color:#fff; align-self:flex-end; max-width:85%; border-top-right-radius:3px; }
.gm .gs { display:block; font-size:.76rem; font-weight:600; color:var(--hema); margin-bottom:.15rem; }
.gm.typing { display:flex; gap:4px; width:3.2rem; } .gm.typing i { width:6px; height:6px; border-radius:50%; background:#c9c4d6;
             animation: pulse 1s infinite; } .gm.typing i:nth-child(2) { animation-delay:.2s } .gm.typing i:nth-child(3) { animation-delay:.4s }
@media (prefers-reduced-motion: reduce) { .gm, .funnel .fr, .funnel .fb i, .mchain .ms, .draft .dr, .egraph .g-n, .egraph .g-e,
  .stg .mt, .caret { animation:none !important; } }
@media (max-width: 860px) { .intake { grid-template-columns:1fr; gap:1rem; } .strip { grid-template-columns: repeat(3,1fr); } }
@media (max-width: 860px) { .hero, .factgroups { grid-template-columns: 1fr; } .method { grid-template-columns: 1fr 1fr; } .sec { display:block; } .chain { flex-wrap:wrap; } }
</style>
""", unsafe_allow_html=True)


def esc(s):
    return html.escape(str(s))


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

# ------------------------------------------------------------------ the case: patient in, run replayed, answer out
if VIEW == "demo":  # the live pitch: patient and run start right under the nav
    st.markdown("<div id='case'></div><style>.block-container{padding-top:1.2rem} .nav{padding-bottom:.6rem}</style>"
                "<div style='height:.8rem'></div>", unsafe_allow_html=True)
else:
    st.markdown("<div id='case'></div><div class='sec'><h2>The case</h2><p>Pick a patient and a question, then press Run. "
                "Watch the GNN pick the evidence, the agent draft an answer and the verifier cut what it can't back up."
                "</p></div>", unsafe_allow_html=True)
pids = data.patients()
pid_col, _ = st.columns([1, 3])
pid = pid_col.selectbox("Patient", pids, label_visibility="collapsed")
if st.session_state.get("pid") != pid:
    st.session_state.update(pid=pid, qid=st.query_params.get("q"), live_trace=None)
played = st.session_state.setdefault("played", set())

pt = data.patient(pid)
info = pt["info"]
if "error" in info:
    st.markdown(f"<p style='color:#5d596b'>No patient profile available: {esc(info['error'])}</p>", unsafe_allow_html=True)
else:
    def pchips(xs, arrow):
        return "".join(f"<span class='ic obs'>{esc(x.get('symbol') or x['name'])} {arrow}<small>{x['z']:+.1f}</small>"
                       "</span>" for x in xs)
    phen = "".join(f"<span class='ic obs'>{esc(x['name'])}</span>" for x in info.get("phenotypes", [])) or "none"
    st.markdown(f"""<div class='intake'><div><div class='k'>Patient</div><div class='pid'>{esc(pid)}</div>
<div class='sum'>{esc(info.get('clinical_summary', ''))}</div></div>
<div><div class='k'>On record</div>{phen}</div>
<div><div class='k'>Tumor proteins vs matched normal kidney (z-score)</div>{pchips(info.get('proteins_up', []), '↑')}
{pchips(info.get('proteins_down', []), '↓')}</div></div>""", unsafe_allow_html=True)

qs = data.demo_questions()
cols = st.columns(len(qs) or 1)
for i, (col, qq) in enumerate(zip(cols, qs), 1):
    if col.button(f"{i}. {STEPS.get(qq['qid'], qq['question'][:24])}", key=f"q_{qq['qid']}", help=qq["question"],
                  use_container_width=True, type="primary" if st.session_state.get("qid") == qq["qid"] else "secondary"):
        st.session_state.update(qid=qq["qid"], live_trace=None, play=True)
        st.rerun()

qid = st.session_state.get("qid")
t, src = data.trace(pid, qid) if qid else (st.session_state.get("live_trace"), "live")
run_key = f"{pid}|{qid or 'live'}|{(t or {}).get('run_id', '')}"
play = st.session_state.pop("play", False)
V = RV.build(t) if t else None

score_note = ""
if real("e2"):
    from eval.make_charts import e2_stats
    _g = e2_stats(R["e2"]["data"])["mean"]
    if "gnn" in _g and "prot_pca_lr" in _g:
        score_note = (f"Across five folds the GNN's AUROC is {_g['gnn']:.2f}, versus {_g['prot_pca_lr']:.2f} for proteins "
                      "alone, so at this cohort size the graph adds explanations more than accuracy.")

left, right = st.columns([2.15, 1], gap="large")
with right:
    st.markdown("<div class='guide-h'><b>Guide</b><span>Walks you through the run. It only uses this run's evidence."
                "</span></div>", unsafe_allow_html=True)
    guide_ph = st.empty()


def bubble(text, who="bot", step=None):
    tag = f"<span class='gs'>{esc(step)}</span>" if step else ""
    return f"<div class='gm {who}'>{tag}{esc(text)}</div>"


def guide(msgs):
    # column-reverse keeps the newest message in view while the panel scrolls
    guide_ph.markdown("<div class='guide'>" + "".join(reversed(msgs)) + "</div>", unsafe_allow_html=True)


def nap(s):
    time.sleep(s * PACE)


def replay(v):
    """Plays the cached trace back stage by stage. Marked as played first, so pressing Skip lands on the result."""
    played.add(run_key)
    narr = dict(RV.narration(v))
    names = dict(RV.STAGES)
    calls = v["calls"]
    with left:
        st.components.v1.html("<script>const a=parent.document.getElementById('runtop');"
                              "if(a)a.scrollIntoView({behavior:'smooth',block:'start'});</script>", height=0)
        if st.button("Skip to the result", key="skip"):
            st.rerun()
        strip_ph = st.empty()
        focus = st.empty()  # every stage plays in this one spot, so eyes stay put
    msgs = []
    for i, (key, _) in enumerate(RV.STAGES):
        strip_ph.markdown(RV.strip_html(v, i, i), unsafe_allow_html=True)
        msgs.append(bubble(narr[key], step=f"{i + 1} · {names[key]}"))
        guide(msgs + ["<div class='gm bot typing'><i></i><i></i><i></i></div>"] if key != "verify" else msgs)
        if key == "patient":
            nap(1.6)
        elif key == "score" and (p := calls.get("predict_patient")):
            for f in range(1, 13):
                focus.markdown(RV.prediction_html(p, p["risk"] * f / 12), unsafe_allow_html=True)
                nap(.06)
            nap(1.6)
        elif key == "retrieve" and (s := calls.get("retrieve_subgraph")):
            cand = s.get("meta", {}).get("n_candidates")
            cand = cand if isinstance(cand, int) else None
            for k in range(1, 4 if cand else 3):
                rows = [RV.PRIMEKG_EDGES, cand, len(s.get("nodes", []))] if cand else [RV.PRIMEKG_EDGES, len(s["nodes"])]
                focus.markdown(funnel(rows[:k]), unsafe_allow_html=True)
                nap(.7)
            focus.markdown(funnel(rows) + RV.graph_svg(v, intro=True), unsafe_allow_html=True)
            nap(2.0)
        elif key == "lookup" and (g := calls.get("get_mechanism")):
            focus.markdown("<div class='sub-h'>Curated mechanism, DisMech</div>" + RV.chain_html(g), unsafe_allow_html=True)
            nap(2.0)
        elif key == "draft":
            n = RV.draft_sentences(v)
            for k in range(1, n + 1):
                focus.markdown("<div class='sub-h'>Agent draft</div>" + RV.draft_partial(v, k), unsafe_allow_html=True)
                nap(min(.55, 4.0 / max(n, 1)))
            nap(.6)
        elif key == "verify":
            focus.markdown("<div class='sub-h'>Verifier<span>Struck: no evidence the tools returned</span></div>"
                           + RV.draft_html(v, "verify"), unsafe_allow_html=True)
            nap(3.2)
        else:
            nap(1.0)
    strip_ph.markdown(RV.strip_html(v, -1, len(RV.STAGES)), unsafe_allow_html=True)
    nap(.5)


def funnel(rows):
    top = math.log10(rows[0])
    labels = ["connections in the graph", "candidates near this patient", "facts the GNN kept for this question"]
    if len(rows) == 2 or (len(rows) == 3 and rows[1] is None):
        labels = [labels[0], labels[2]]
    out = []
    for n, lab in zip(rows, labels):
        w = max(3, 100 * math.log10(max(n, 1)) / top)
        out.append(f"<div class='fr'><b>{n:,}</b><span>{lab}</span><div class='fb'><i style='width:{w:.0f}%'></i></div></div>")
    return "<div class='funnel'>" + "".join(out) + "</div>"


def ask(text, v):
    hist = st.session_state.setdefault("chat", {}).setdefault(pid, [])
    hist.append({"role": "user", "text": text})
    it = RV.intent_of(text)
    if it and v:
        hist.append({"role": "guide", "text": RV.explain(it, v, score_note)})
        return
    res = data.chat(pid, text)
    if res["trace"] and res["mode"] == "live":
        hist.append({"role": "guide", "text": "I ran the agent live on your question. Watch it work on the left."})
        st.session_state.update(qid=None, live_trace=res["trace"], play=True)
    elif res["trace"] and res["qid"] == qid:
        hist.append({"role": "guide", "text": "That's the question on screen. Hover the answer to trace each claim."})
    elif res["trace"]:
        hist.append({"role": "guide", "qid": res["qid"], "text": f"The closest verified run is “{res['trace']['question']}”"})
    else:
        hist.append({"role": "guide", "text": "I only answer from verified runs. Ask about the score, the drugs, the "
                     "evidence or what the verifier removed, or pick one of the questions above."})


with left:
    if not t:
        st.markdown("<p class='q' style='color:#5d596b'>" + ("There's no verified run for this step and patient yet."
                    if qid else "Pick a question above to run the agent on this patient.") + "</p>",
                    unsafe_allow_html=True)
    else:
        demo = "<span class='demo'>Demo data: placeholder IDs and citations</span>" if src == "mock" else ""
        st.markdown(f"<div id='runtop'></div>{demo}<div class='q'>{esc(t['question'])}</div>", unsafe_allow_html=True)

if V and play:
    replay(V)
    st.rerun()

if V and run_key in played:
    v = V
    with left:
        graph = RV.graph_svg(v)
        st.markdown(RV.strip_html(v, -1, len(RV.STAGES)), unsafe_allow_html=True)
        if p := v["calls"].get("predict_patient"):
            st.markdown(RV.prediction_html(p), unsafe_allow_html=True)
        st.markdown("<div class='sub-h'>Verified answer<span>Hover a sentence to see its evidence</span></div>"
                    + RV.hover_css(v) + RV.answer_html(v), unsafe_allow_html=True)
        sup, hyp, rem = (v["claims"][k] for k in ("supported", "hypothesis", "removed"))
        st.markdown(f"<div class='tally'><div><b>{len(sup)}</b>claims backed by evidence</div>"
                    f"<div><b>{len(hyp)}</b>kept as hypotheses</div><div><b>{len(rem)}</b>removed by the verifier</div>"
                    "</div>", unsafe_allow_html=True)

        def catch(c, as_hyp=False):
            why = (c.get("reason") or "Plausible but untested, so it is kept and labeled.") if as_hyp else RV.why_removed(c)
            txt = esc(RV.strip_ids(c["text"])) if as_hyp else f"<span class='what'>{esc(RV.strip_ids(c['text']))}</span>"
            return (f"<div class='catch{' hyp' if as_hyp else ''}'><span class='tag'>"
                    f"{'Kept as hypothesis' if as_hyp else 'Removed'}</span>{txt}<div class='why'>{esc(why)}</div></div>")

        if rem:
            st.markdown(catch(rem[0]), unsafe_allow_html=True)
        more = rem[1:] + hyp
        if more:
            with st.expander(f"Everything else the verifier flagged ({len(more)})"):
                st.markdown("".join(catch(c) for c in rem[1:]) + "".join(catch(c, True) for c in hyp),
                            unsafe_allow_html=True)
        with st.expander("See the agent's draft before verification"):
            st.markdown(RV.draft_html(v, "verify"), unsafe_allow_html=True)

        sub = v["calls"].get("retrieve_subgraph")
        st.markdown("<div class='sub-h'>What the GNN retrieved<span>Hover a line to find the sentence that cites it"
                    "</span></div>", unsafe_allow_html=True)
        if sub:
            cand = sub.get("meta", {}).get("n_candidates")
            rows = [RV.PRIMEKG_EDGES] + ([cand] if isinstance(cand, int) else []) + [len(sub.get("nodes", []))]
            st.markdown(funnel(rows), unsafe_allow_html=True)
        st.markdown(graph, unsafe_allow_html=True)
        legend = "".join(f"<span><i style='border-color:{ROLE[r][2]};{'border-top-style:dashed' if r == 'observed' else ''}'>"
                         f"</i>{LABEL[r]}</span>" for r in ROLE)
        st.markdown(f"<div class='legend' style='margin-top:.3rem'>{legend}<span>Colored lines are cited in the answer, "
                    "grey ones were retrieved but not used. A bar at the end of a drug line means it inhibits its target."
                    "</span></div>", unsafe_allow_html=True)
        st.markdown("<div class='sub-h'>Sources</div>" + RV.sources_html(v), unsafe_allow_html=True)
        st.button("Replay the run", key="replay", on_click=lambda: st.session_state.update(play=True))
elif V:
    with left:
        st.markdown(RV.strip_html(V, -1, 0), unsafe_allow_html=True)
        st.markdown("<p class='gcap'>Press Run to watch the agent work through this question. It replays a verified, "
                    "cached run and takes about 15 seconds.</p>", unsafe_allow_html=True)
        st.button("Run", type="primary", key="run", on_click=lambda: st.session_state.update(play=True))

# the guide: narration of the run, then follow-up questions answered from this run only
with right:
    msgs = []
    if V and run_key in played:
        msgs = [bubble(txt, step=f"{i + 1} · {dict(RV.STAGES)[k]}") for i, (k, txt) in enumerate(RV.narration(V))]
        msgs.append(bubble(RV.outro()))
    else:
        msgs.append(bubble("Hi. Pick a question and press Run, and I'll walk you through each step: what we measured "
                           "in this patient, what the GNN pulled from the graph, and what the verifier cut."))
    hist = st.session_state.setdefault("chat", {}).get(pid, [])
    msgs += [bubble(m["text"], "user" if m["role"] == "user" else "bot") for m in hist]
    guide(msgs)
    for i, m in enumerate(hist):
        if m.get("qid") and i == len(hist) - 1:
            if st.button("Open that run", key=f"open_{pid}_{i}"):
                st.session_state.update(qid=m["qid"], live_trace=None, play=True)
                st.rerun()
    if V and run_key in played:
        sugg = [("What does the score mean?", "score"), ("Why was a claim removed?", "removed"),
                ("Which drugs, and why?", "drugs"), ("Where does the evidence come from?", "evidence")]
        sc = st.columns(2)
        for j, (label, _) in enumerate(sugg):
            if sc[j % 2].button(label, key=f"sg_{j}", use_container_width=True):
                ask(label, V)
                st.rerun()
    with st.form(f"chat_{pid}", clear_on_submit=True):
        q = st.text_input("Ask", placeholder="Ask about this patient", label_visibility="collapsed")
        sent = st.form_submit_button("Ask the agent" if data.LIVE else "Ask")
    if sent and q.strip():
        ask(q.strip(), V if V and run_key in played else None)
        st.rerun()


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
