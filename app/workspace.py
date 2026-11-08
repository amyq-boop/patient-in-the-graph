"""Workspace: patient in, insight out. One screen, panels that fill in as the analysis arrives, linked by gene name.

Before Analyze, the grid shows empty panels. Pressing Analyze replays the patient's verified run: patient-level panels
first (fingerprint, cohort position, subgroup), then the pipeline (GNN retrieval -> graph, agent draft -> verifier ->
insight), then targets, the verifier verdict and evidence gaps. Hovering a gene in any panel highlights it in all of them.

Data: Anikat's briefs and clusters (real), Kaitlin's cached traces in cache/demo/ (real when present). Mock content is
labeled. Nothing on screen is computed into a new claim: every value comes from those files.
"""
from __future__ import annotations

import html
import math
import time

import streamlit as st

from app import chapters as CH
from app import data
from app import product_view as PV
from app import run_view as RV
from app.run_view import sym_cls

PACE = 1.0
INK, BODY, MUTED, RULE = "#1b1530", "#332e47", "#5d596b", "#e6e2ee"
PRIMARY_Q = ("P01", "B01")


def esc(s):
    return html.escape(str(s))


def num(x):
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def panel(label, body, kicker="", cls=""):
    k = f"<span class='pk'>{kicker}</span>" if kicker else ""
    return f"<div class='pn {cls}'><div class='ph'><span>{esc(label)}</span>{k}</div>{body}</div>"


def skeleton(label, note="Waiting for analysis"):
    return (f"<div class='pn sk'><div class='ph'><span>{esc(label)}</span></div>"
            f"<div class='skb'><i></i><i></i><i></i></div><div class='skn'>{esc(note)}</div></div>")


# ------------------------------------------------------------------ panels
def fingerprint(brief):
    up = brief.get("proteins_up", [])[:6]
    dn = brief.get("proteins_down", [])[:3]
    rows = up + dn
    top = max([abs(r["z"]) for r in rows if num(r.get("z"))] + [1])
    bars = "".join(
        f"<div class='fp hl {sym_cls(r['symbol'])}'><b>{esc(r['symbol'])}</b><span class='ft'><i class='{'u' if r.get('z', 0) > 0 else 'd'}' "
        f"style='width:{100 * abs(r['z']) / top:.0f}%'></i></span><em>{r['z']:+.1f}</em></div>"
        for r in rows if num(r.get("z")))
    drivers = [m for m in brief.get("mutations", []) if m.get("known_driver")]
    others = [m for m in brief.get("mutations", []) if not m.get("known_driver")][:2]
    muts = "".join(f"<span class='mt hl {sym_cls(m['symbol'])}{' drv' if m.get('known_driver') else ''}'>{esc(m['symbol'])}"
                   f"<small>{esc(m.get('protein_changes') or '')}</small></span>" for m in drivers + others)
    ref = {"normal": "vs normal kidney", "cohort": "vs other tumors"}.get(brief.get("reference"), "")
    return panel("Molecular fingerprint", f"<div class='muts2'>{muts or '<span class=gcap>no mutations recorded</span>'}</div>"
                 f"<div class='fps'>{bars}</div>", kicker=f"protein z-score {ref}")


def mini_map(cl, pid):
    pts = cl.get("patients", [])
    if not pts:
        return ""
    W, H, M = 520, 330, 22
    xs, ys = [p["x"] for p in pts], [p["y"] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    X = lambda v: M + (v - x0) / ((x1 - x0) or 1) * (W - 2 * M)  # noqa: E731
    Y = lambda v: H - M - (v - y0) / ((y1 - y0) or 1) * (H - 2 * M)  # noqa: E731
    out = [f"<svg viewBox='0 0 {W} {H}' width='100%' role='img' aria-label='Cohort map with this tumor ringed'>"]
    for c in cl.get("clusters", []):
        mem = [(X(p["x"]), Y(p["y"])) for p in pts if p["cluster"] == c["cluster"]]
        if len(mem) >= 3:
            cx, cy = sum(a for a, _ in mem) / len(mem), sum(b for _, b in mem) / len(mem)
            core = sorted(mem, key=lambda q: (q[0] - cx) ** 2 + (q[1] - cy) ** 2)[: max(3, int(len(mem) * .85))]
            out.append(f"<path d='{CH._smooth(CH._pad(CH._hull(core), cx, cy, 14))}' fill='{PV.ccolor(c['cluster'])}' fill-opacity='.08'/>")
    me = None
    for p in pts:
        if p["patient_id"] == pid:
            me = p
            continue
        out.append(f"<circle cx='{X(p['x']):.1f}' cy='{Y(p['y']):.1f}' r='3' fill='{PV.ccolor(p['cluster'])}' fill-opacity='.75'/>")
    if me:
        mx, my = X(me["x"]), Y(me["y"])
        out.append(f"<circle class='pulse' cx='{mx:.1f}' cy='{my:.1f}' r='16' fill='{PV.ccolor(me['cluster'])}' fill-opacity='.18'/>"
                   f"<circle cx='{mx:.1f}' cy='{my:.1f}' r='6' fill='{PV.ccolor(me['cluster'])}' stroke='{INK}' stroke-width='2'/>")
    out.append("</svg>")
    # legend drawn inside the svg, one row, so the page's markdown can't re-flow it
    x, items = 4, []
    for c in cl.get("clusters", []):
        lab = f"{c['name']} ({c['n']})"
        items.append(f"<circle cx='{x + 5}' cy='{H + 14}' r='5' fill='{PV.ccolor(c['cluster'])}'/>"
                     f"<text x='{x + 16}' y='{H + 19}' font-size='14' fill='{BODY}'>{esc(lab)}</text>")
        x += 30 + int(7.4 * len(lab))
    svg = "".join(out).replace(f"viewBox='0 0 {W} {H}'", f"viewBox='0 0 {W} {H + 30}'").replace("</svg>", "".join(items) + "</svg>")
    return panel("Cohort position", svg, kicker=f"{len(pts)} tumors, proteomic map")


def subgroup(cl, pid):
    c = PV.patient_cluster(cl, pid)
    info = PV.cluster_info(cl, c)
    if not info:
        return panel("Subgroup", "<p class='gcap'>Not in the subgroup file.</p>")
    co = next((p for p in cl["patients"] if p["patient_id"] == pid), {}).get("co_assignment")
    sig = info.get("signature_proteins", [])
    up = [x for x in sig if x.get("direction") == "up"][:3]
    dn = [x for x in sig if x.get("direction") == "down"][:3]
    top = max([abs(x["effect"]) for x in up + dn if num(x.get("effect"))] + [1e-9])
    rows = "".join(f"<div class='fp hl {sym_cls(x['symbol'])}'><b>{esc(x['symbol'])}</b><span class='ft'><i class='{'u' if x['direction'] == 'up' else 'd'}' "
                   f"style='width:{100 * abs(x['effect']) / top:.0f}%'></i></span><em>{x['effect']:+.2f}</em></div>"
                   for x in up + dn if num(x.get("effect")))
    warn = num(co) and co < PV.UNSTABLE
    big = (f"<div class='big{' warn' if warn else ''}'>{co:.0%}<span>co-clustered in reruns"
           + (", unstable" if warn else "") + "</span></div>") if num(co) else ""
    return panel("Subgroup", f"<div class='sgn' style='--cc:{PV.ccolor(c)}'><i></i>{esc(info['name'])}<small>{info['n']} tumors</small></div>"
                 f"{big}<div class='fps'>{rows}</div>", kicker="exploratory, built without stage")


def graph_panel(v):
    svg = RV.graph_svg(v)
    return panel("Evidence graph", RV.hover_css(v) + svg + "<div class='gleg'><span><i class='o'></i>measured in this tumor</span>"
                 "<span><i class='k'></i>known biology</span><span><i class='g'></i>drug</span><span>grey: retrieved, not cited</span></div>",
                 kicker="what the GNN retrieved", cls="wide")


def funnel_panel(v):
    s = v["calls"].get("retrieve_subgraph") or {}
    cand = (s.get("meta") or {}).get("n_candidates")
    rows = [(RV.PRIMEKG_EDGES, "connections in the graph")] + ([(cand, "near this patient")] if isinstance(cand, int) else []) + \
        [(len(s.get("nodes", [])), "kept by the GNN")]
    top = math.log10(rows[0][0])
    body = "".join(f"<div class='fr2'><b>{n:,}</b><span>{lab}</span><div class='fb2'><i style='width:{max(3, 100 * math.log10(max(n, 1)) / top):.0f}%'></i></div></div>"
                   for n, lab in rows)
    return panel("Evidence graph", f"<div class='funnel2'>{body}</div>", kicker="GNN retrieval running", cls="wide")


def _n_src(c):
    n = len(c["support"].get("edge_ids", [])) + len(c["support"].get("pmids", []))
    return f"{n} source{'' if n == 1 else 's'}"


def insight(v):
    sup = v["claims"]["supported"][:4]
    rem = v["claims"]["removed"][:1]
    items = "".join(f"<li><span class='ok'>✓</span>{esc(RV.strip_ids(c['text']))}"
                    f"<small>{_n_src(c)}</small></li>"
                    for c in sup)
    cut = "".join(f"<div class='cut'><span>Not supported</span><s>{esc(RV.strip_ids(c['text']))}</s></div>" for c in rem)
    return panel("Insight", f"<p class='iq'>{esc(v['trace']['question'])}</p><ul class='ins'>{items}</ul>{cut}",
                 kicker="verified answer")


def drafting(v, k, phase):
    body = RV.draft_html(v, "verify") if phase == "verify" else RV.draft_partial(v, k)
    return panel("Insight", f"<p class='iq'>{esc(v['trace']['question'])}</p><div class='dbox'>{body}</div>",
                 kicker="verifier checking" if phase == "verify" else "agent drafting")


def targets_panel(v):
    rows = PV.targets(v)[:4]
    cards = "".join(f"<div class='tc hl {sym_cls(r['target'])}'><b>{esc(r['drug'])}</b><span>acts on {esc(r['target'])}</span>"
                    f"<em>{esc(r['id'])}</em></div>" for r in rows) or "<p class='gcap'>No drug edges in this run.</p>"
    return panel("Drug targets", f"<div class='tcs'>{cards}</div><p class='gcap'>A reason to test, not proof of response.</p>",
                 kicker="linked by graph edges")


def verdict_panel(v):
    s, h, r = (len(v["claims"][k]) for k in ("supported", "hypothesis", "removed"))
    n = s + h + r or 1
    seg = (f"<div class='vbar'><i class='s' style='width:{100 * s / n:.0f}%'></i><i class='h' style='width:{100 * h / n:.0f}%'></i>"
           f"<i class='r' style='width:{100 * r / n:.0f}%'></i></div>")
    nums = (f"<div class='vnums'><div><b>{s}</b>kept</div><div><b>{h}</b>hypotheses</div><div><b class='rr'>{r}</b>cut</div></div>")
    return panel("Verifier", seg + nums + "<p class='gcap'>Kept only if its citation came back from a tool in this run.</p>",
                 kicker=f"{n} claims checked")


def gaps_panel(v, brief):
    g = PV.gaps(v, brief)[:4]

    def short(t):
        return t if len(t) <= 64 else t[:62].rsplit(" ", 1)[0] + "…"
    items = "".join(f"<li title='{esc(t)}'><span class='gtag'>{esc(k)}</span>{esc(short(t))}</li>" for k, t in g)
    return panel("Evidence gaps", f"<ul class='gaps2'>{items or '<li>none flagged</li>'}</ul>", kicker="what would say more")


def pending(label):
    return skeleton(label, "Needs a cached agent run for this tumor")


def link_css(symbols):
    """Hover a gene anywhere and it lights up in every panel."""
    r = []
    for s in sorted(symbols):
        r.append(f".stApp:has(.{s}:hover) .hl.{s} {{ background:#ece7fb !important; box-shadow:0 0 0 2px #4f3fbf; border-radius:5px; }}")
        r.append(f".stApp:has(.{s}:hover) .egraph g.{s} rect {{ stroke:#1b1530; stroke-width:2.5; }}")
        r.append(f".stApp:has(.{s}:hover) .egraph g.{s} {{ opacity:1 !important; }}")
    return "<style>" + "\n".join(r) + "</style>"


CSS = f"""
.wsbar {{ display:flex; align-items:center; justify-content:space-between; gap:1rem; margin:.2rem 0 .8rem; }}
.case {{ display:flex; align-items:baseline; gap:1.2rem; flex-wrap:wrap; margin:.6rem 0 .2rem; }}
.case .cid {{ font-family:"Newsreader",serif; font-size:2.8rem; line-height:1; color:{INK}; }}
.case span {{ color:{MUTED}; font-size:1rem; }}
.pn {{ background:#fff; border:1px solid {RULE}; border-radius:12px; padding:1rem 1.15rem 1.05rem; height:100%; min-height:19.5rem;
       animation: arrive .5s ease-out both; }}
@keyframes arrive {{ from {{ opacity:0; transform:translateY(8px) }} }}
.ph {{ display:flex; justify-content:space-between; align-items:baseline; gap:.6rem; margin-bottom:.7rem; }}
.ph span:first-child {{ font-weight:600; font-size:.86rem; color:{INK}; letter-spacing:.01em; }}
.pk {{ font-size:.76rem; color:{MUTED}; text-align:right; }}
.pn.sk {{ background:#fbfafc; border-style:dashed; animation:none; }}
.pn.wide {{ min-height:26rem; }}
.skb {{ display:flex; flex-direction:column; gap:.6rem; margin-top:.6rem; }}
.skb i {{ display:block; height:10px; border-radius:5px; background:linear-gradient(90deg,#efecf4,#f7f5fa,#efecf4);
          background-size:200% 100%; animation: shim 1.6s linear infinite; }}
.skb i:nth-child(2) {{ width:80% }} .skb i:nth-child(3) {{ width:55% }}
@keyframes shim {{ from {{ background-position:200% 0 }} to {{ background-position:-200% 0 }} }}
.skn {{ font-size:.8rem; color:{MUTED}; margin-top:.9rem; }}
.fps {{ display:flex; flex-direction:column; gap:.38rem; }}
.fp {{ display:grid; grid-template-columns:4.6rem 1fr 2.8rem; gap:.55rem; align-items:center; font-size:.9rem; padding:0 .15rem; cursor:default; }}
.fp b {{ color:{INK}; font-weight:600; }} .fp em {{ font-style:normal; text-align:right; color:{MUTED}; font-size:.8rem; font-variant-numeric:tabular-nums; }}
.ft {{ height:7px; background:#f1eef7; border-radius:4px; overflow:hidden; }}
.ft i {{ display:block; height:7px; border-radius:4px; animation: grow2 .7s ease-out both; }}
.ft i.u {{ background:#d6457a; }} .ft i.d {{ background:#4f3fbf; }}
@keyframes grow2 {{ from {{ width:0 }} }}
.muts2 {{ display:flex; flex-wrap:wrap; gap:.4rem; margin-bottom:.85rem; }}
.mt {{ font-size:.88rem; background:#f1eef7; color:{INK}; border-radius:6px; padding:.2rem .55rem; cursor:default; }}
.mt.drv {{ background:#fbe3ea; color:#a3304f; font-weight:600; }} .mt small {{ margin-left:.35rem; font-weight:400; opacity:.8; }}
.mleg span {{ display:inline-flex !important; align-items:center; white-space:nowrap; }}
.mleg {{ display:flex; gap:1rem; flex-wrap:wrap; font-size:.82rem; color:{BODY}; margin-top:.2rem; }}
.mleg i {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:.35rem; }} .mleg small {{ color:{MUTED}; }}
.pulse {{ animation: pulse2 2s ease-in-out infinite; transform-origin:center; transform-box:fill-box; }}
@keyframes pulse2 {{ 50% {{ transform:scale(1.35); opacity:.5 }} }}
.sgn {{ display:flex; align-items:baseline; flex-wrap:wrap; gap:.2rem .5rem; font-family:"Newsreader",serif; font-size:1.45rem; line-height:1.2; color:{INK}; }}
.sgn small {{ white-space:nowrap; }}
.sgn i {{ width:12px; height:12px; border-radius:50%; background:var(--cc); }} .sgn small {{ font-family:"Public Sans",sans-serif; font-size:.82rem; color:{MUTED}; margin-left:.3rem; }}
.big {{ font-family:"Newsreader",serif; font-size:2.6rem; line-height:1; color:{INK}; margin:.7rem 0 .9rem; }}
.big span {{ display:block; font-family:"Public Sans",sans-serif; font-size:.8rem; color:{MUTED}; margin-top:.3rem; }}
.big.warn {{ color:#a3304f; }}
.gleg {{ display:flex; gap:1.1rem; flex-wrap:wrap; font-size:.8rem; color:{MUTED}; margin-top:.3rem; }}
.gleg i {{ display:inline-block; width:18px; vertical-align:middle; margin-right:.35rem; border-top:2px solid; }}
.gleg i.o {{ border-color:#d6457a; border-top-style:dashed; }} .gleg i.k {{ border-color:#4f3fbf; }} .gleg i.g {{ border-color:#b88a00; }}
.funnel2 {{ display:flex; flex-direction:column; gap:.9rem; padding:1.4rem .4rem; }}
.fr2 {{ display:grid; grid-template-columns:8rem 1fr; column-gap:1rem; align-items:center; animation: arrive .45s ease-out both; }}
.fr2 b {{ font-family:"Newsreader",serif; font-weight:400; font-size:1.8rem; text-align:right; color:{INK}; }}
.fr2 span {{ font-size:.86rem; color:{MUTED}; }} .fb2 {{ grid-column:2; height:6px; background:#efecf4; border-radius:3px; }}
.fb2 i {{ display:block; height:6px; border-radius:3px; background:#4f3fbf; animation: grow2 .8s ease-out both; }}
.fr2:last-child .fb2 i {{ background:#d6457a; }}
.iq {{ font-family:"Newsreader",serif; font-size:1.15rem; line-height:1.3; color:{INK}; margin:0 0 .7rem; }}
.ins {{ list-style:none; padding:0 !important; margin:0 !important; }}
.ins li {{ display:grid; grid-template-columns:1.2rem 1fr; column-gap:.4rem; padding:.42rem 0; border-bottom:1px solid {RULE};
           font-size:.92rem; color:{BODY}; line-height:1.4; margin:0 !important; animation: arrive .4s ease-out both; }}
.ins li small {{ grid-column:2; color:{MUTED}; font-size:.76rem; }} .ins .ok {{ color:#0e8f82; font-weight:700; }}
.cut {{ margin-top:.7rem; background:#fbe3ea; border-radius:8px; padding:.55rem .7rem; font-size:.9rem; animation: flag 1.2s ease-out both; }}
.cut span {{ display:block; font-size:.72rem; font-weight:600; color:#a3304f; margin-bottom:.15rem; }}
.cut s {{ color:#a3304f; text-decoration-thickness:2px; }}
.dbox .answer {{ font-size:.95rem !important; line-height:1.5 !important; max-width:none !important; }}
.tcs {{ display:flex; flex-direction:column; gap:.5rem; }}
.tc {{ display:grid; grid-template-columns:auto 1fr; column-gap:.8rem; align-items:baseline; background:#fdf8ea; border-radius:8px;
       padding:.55rem .75rem; cursor:default; animation: arrive .4s ease-out both; }}
.tc b {{ color:#6b5100; font-size:1rem; }} .tc span {{ color:{BODY}; font-size:.88rem; }}
.tc em {{ grid-column:1 / 3; font-style:normal; font-size:.74rem; color:{MUTED}; }}
.vbar {{ display:flex; height:14px; border-radius:7px; overflow:hidden; gap:2px; margin:.4rem 0 .9rem; background:#fff; }}
.vbar i {{ display:block; height:14px; animation: grow2 .9s ease-out both; }}
.vbar .s {{ background:#0e8f82; }} .vbar .h {{ background:#c9c4d6; }} .vbar .r {{ background:#d6457a; }}
.vnums {{ display:grid; grid-template-columns:repeat(3,1fr); gap:.6rem; }}
.vnums div {{ font-size:.8rem; color:{MUTED}; }} .vnums b {{ display:block; font-family:"Newsreader",serif; font-weight:400; font-size:2rem; color:{INK}; line-height:1.05; }}
.vnums b.rr {{ color:#a3304f; }}
.gaps2 {{ list-style:none; padding:0 !important; margin:0 !important; font-size:.88rem; color:{BODY}; }}
.gaps2 li {{ padding:.42rem 0; border-bottom:1px solid {RULE}; line-height:1.4; margin:0 !important; }}
.askbar {{ margin-top:1.4rem; padding-top:1.1rem; border-top:1px solid {RULE}; }}
.answer2 {{ background:#fff; border:1px solid {RULE}; border-left:3px solid #4f3fbf; border-radius:8px; padding:.7rem .9rem;
            font-size:.95rem; color:{BODY}; line-height:1.5; margin:.6rem 0; animation: arrive .35s ease-out both; }}
.answer2 b {{ display:block; font-size:.8rem; color:{MUTED}; font-weight:600; margin-bottom:.2rem; }}
@media (prefers-reduced-motion: reduce) {{ .pn, .ft i, .fr2, .fb2 i, .ins li, .cut, .tc, .vbar i, .pulse, .skb i, .answer2 {{ animation:none !important; }} }}
"""


# ------------------------------------------------------------------ page
def render(score_note=""):
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.5, 1, .8])
    cancer = c1.radio("Cancer", list(data.CANCERS), format_func=data.CANCERS.get, horizontal=True,
                      label_visibility="collapsed", key="ws_cancer")
    pids = data.patients(cancer)
    if st.query_params.get("dev") == "1":  # rehearsal: include mock patients
        pids += [p for p, _, c in data._trace_index() if c == cancer and "MOCK" in p and p not in pids]
    if not pids:
        st.markdown("<p class='gcap'>No patients for this cancer yet.</p>", unsafe_allow_html=True)
        return
    pid = c2.selectbox("Patient", pids, label_visibility="collapsed", key=f"ws_pid_{cancer}")
    qs = data.qids(pid)
    qid = st.session_state.get(f"ws_q_{pid}") or next((q for q in PRIMARY_Q if q in qs), qs[0] if qs else None)
    t, src = data.trace(pid, qid) if qid else (None, "missing")
    run_key = f"{pid}|{qid}|{(t or {}).get('run_id', '')}"
    done = st.session_state.setdefault("ws_done", set())
    if c3.button("Analyze patient", type="primary", use_container_width=True, key=f"go_{pid}"):
        st.session_state["ws_play"] = True
        done.discard(run_key)
    play = st.session_state.pop("ws_play", False)

    mock_pt = "MOCK" in pid
    CL, cl_origin = data.product(cancer, "clusters.json", mock_only=mock_pt)
    BRIEF, b_origin = data.product(cancer, f"briefs/{pid}.json", mock_only=mock_pt)
    BRIEF = BRIEF or {}
    V = RV.build(t) if t else None
    demo_tag = "<span class='demo'>Demo data</span> " if "mock" in (src, cl_origin, b_origin) else ""
    raw_demo = CH._demo(BRIEF.get("summary", ""))
    st.markdown(f"{demo_tag}<div class='case'><div class='cid'>{esc(pid)}</div><span>{esc(raw_demo)}</span></div>", unsafe_allow_html=True)

    syms = set()
    for r in BRIEF.get("proteins_up", [])[:6] + BRIEF.get("proteins_down", [])[:3] + BRIEF.get("mutations", []):
        syms.add(sym_cls(r["symbol"]))
    if CL and (ci := PV.cluster_info(CL, PV.patient_cluster(CL, pid))):
        syms |= {sym_cls(x["symbol"]) for x in ci.get("signature_proteins", [])}
    if V:
        syms |= {sym_cls(n["name"]) for n in V["nodes"].values()}
    st.markdown(link_css({s for s in syms if s}), unsafe_allow_html=True)

    strip_ph = st.empty()
    r1 = st.columns(3, gap="medium")
    r2 = st.columns([2, 1], gap="medium")
    r3 = st.columns(3, gap="medium")
    slots = {"fp": r1[0].empty(), "map": r1[1].empty(), "sub": r1[2].empty(), "graph": r2[0].empty(),
             "ins": r2[1].empty(), "tgt": r3[0].empty(), "ver": r3[1].empty(), "gap": r3[2].empty()}
    labels = {"fp": "Molecular fingerprint", "map": "Cohort position", "sub": "Subgroup", "graph": "Evidence graph",
              "ins": "Insight", "tgt": "Drug targets", "ver": "Verifier", "gap": "Evidence gaps"}

    def show(k, h):
        slots[k].markdown(h, unsafe_allow_html=True)

    def final():
        show("fp", fingerprint(BRIEF) if BRIEF else pending(labels["fp"]))
        show("map", mini_map(CL, pid) if CL else pending(labels["map"]))
        show("sub", subgroup(CL, pid) if CL else pending(labels["sub"]))
        if V:
            strip_ph.markdown(RV.strip_html(V, -1, len(RV.STAGES)), unsafe_allow_html=True)
            show("graph", graph_panel(V))
            show("ins", insight(V))
            show("tgt", targets_panel(V))
            show("ver", verdict_panel(V))
            show("gap", gaps_panel(V, BRIEF))
        else:
            for k in ("graph", "ins", "tgt", "ver", "gap"):
                show(k, pending(labels[k]))

    if play:
        done.add(run_key)
        for k, lab in labels.items():
            show(k, skeleton(lab, "Analyzing"))
        st.components.v1.html("<script>window.parent.scrollTo({top:0,behavior:'smooth'});</script>", height=0)
        nap = lambda s_: time.sleep(s_ * PACE)  # noqa: E731
        nap(.5)
        show("fp", fingerprint(BRIEF) if BRIEF else pending(labels["fp"]))
        nap(.8)
        show("map", mini_map(CL, pid) if CL else pending(labels["map"]))
        nap(.8)
        show("sub", subgroup(CL, pid) if CL else pending(labels["sub"]))
        nap(1.0)
        if V:
            for i in range(len(RV.STAGES)):
                strip_ph.markdown(RV.strip_html(V, i, i), unsafe_allow_html=True)
                key = RV.STAGES[i][0]
                if key == "retrieve":
                    show("graph", funnel_panel(V))
                    nap(1.6)
                    show("graph", panel("Evidence graph", RV.graph_svg(V, intro=True), kicker="what the GNN retrieved", cls="wide"))
                    nap(1.6)
                elif key == "draft":
                    n = RV.draft_sentences(V)
                    for k in range(1, n + 1):
                        show("ins", drafting(V, k, "draft"))
                        nap(min(.45, 3.2 / max(n, 1)))
                elif key == "verify":
                    show("ins", drafting(V, 0, "verify"))
                    nap(2.6)
                else:
                    nap(.7)
            strip_ph.markdown(RV.strip_html(V, -1, len(RV.STAGES)), unsafe_allow_html=True)
            show("graph", graph_panel(V))
            show("ins", insight(V))
            nap(.4)
            show("tgt", targets_panel(V))
            nap(.4)
            show("ver", verdict_panel(V))
            nap(.4)
            show("gap", gaps_panel(V, BRIEF))
        else:
            for k in ("graph", "ins", "tgt", "ver", "gap"):
                show(k, pending(labels[k]))
        st.rerun()
    elif run_key in done:
        final()
    else:
        for k, lab in labels.items():
            show(k, skeleton(lab, "Press Analyze patient"))

    if run_key in done and V:
        _ask_bar(pid, qid, qs, V, CL, score_note)


def _ask_bar(pid, qid, qs, v, cl, score_note):
    st.markdown("<div class='askbar'></div>", unsafe_allow_html=True)
    chips = [("What defines this subgroup?", "subgroup"), ("Why was a claim cut?", "removed"),
             ("Which drugs, and why?", "drugs"), ("Where does the evidence come from?", "evidence")]
    cols = st.columns(len(chips) + 1)
    hist = st.session_state.setdefault("ws_chat", {}).setdefault(pid, [])
    for i, (lab, it) in enumerate(chips):
        if cols[i].button(lab, key=f"ws_chip_{pid}_{i}", use_container_width=True):
            ans = PV.subgroup_explain(cl, pid) if it == "subgroup" and cl else RV.explain(it, v, score_note)
            hist.append((lab, ans))
            st.rerun()
    others = [q for q in qs if q != qid]
    if others and cols[-1].button("Next question", key=f"ws_next_{pid}", use_container_width=True):
        st.session_state[f"ws_q_{pid}"] = others[0]
        st.session_state["ws_play"] = True
        st.rerun()
    runs = data.cached_questions(pid)  # only questions with a verified run for this patient: each one replays
    if runs:
        ids = [r["qid"] for r in runs]
        text = {r["qid"]: r["question"] for r in runs}
        c_pick, c_go = st.columns([5, 1], vertical_alignment="bottom")
        pick = c_pick.selectbox("Ask a verified question", ids, index=ids.index(qid) if qid in ids else 0,
                                format_func=text.get, key=f"ws_pick_{pid}_{qid}")
        if c_go.button("Ask", type="primary", key=f"ws_ask_{pid}", use_container_width=True):
            st.session_state[f"ws_q_{pid}"] = pick
            st.session_state["ws_play"] = True
            hist.append((text[pick], "Playing the verified run for this question."))
            st.rerun()
    if hist:
        q, a = hist[-1]
        st.markdown(f"<div class='answer2'><b>{esc(q)}</b>{esc(a)}</div>", unsafe_allow_html=True)
