"""Editorial layout for the case: numbered chapters, each a question and a one-line answer, then its exhibit.

01 The cohort   where this tumor sits among the others (map + subgroup spec)
02 The patient  what was measured in it (drivers, protein changes, record)
03 The run      the agent working through one question (replay + guide, in streamlit_app.py)
04 The evidence what backs the answer (graph, targets, gaps, in streamlit_app.py)

Every sentence here is assembled from fields in the clusters/brief files; nothing is inferred beyond them.
"""
from __future__ import annotations

import html
import math

from app.product_view import UNSTABLE, ccolor, cluster_info, patient_cluster

INK, BODY, MUTED, RULE, PAPER = "#1b1530", "#332e47", "#5d596b", "#e6e2ee", "#fbfafc"


def esc(s):
    return html.escape(str(s))


def num(x):
    return isinstance(x, (int, float)) and not (isinstance(x, float) and math.isnan(x))


def chapter(n, total, label, question, answer=""):
    return (f"<div class='chap'><div class='chap-n'><b>{n:02d}</b> / {total:02d}<span>{esc(label)}</span></div>"
            f"<h2 class='chap-q'>{esc(question)}</h2>" + (f"<p class='chap-a'>{answer}</p>" if answer else "") + "</div>")


# ------------------------------------------------------------------ 01 the cohort
def cohort_answer(cl, pid, cancer_word):
    c = patient_cluster(cl, pid)
    info = cluster_info(cl, c)
    if not info:
        return f"{esc(pid)} isn't in the subgroup file yet."
    co = next((p for p in cl["patients"] if p["patient_id"] == pid), {}).get("co_assignment")
    tail = ("" if not num(co) else f" Stable in <b>{co:.0%}</b> of reruns." if co >= UNSTABLE
            else f" <b class='warn'>Unstable: {co:.0%} of reruns.</b>")
    return f"{esc(info['name'])}, with {info['n'] - 1} others.{tail}"


def _hull(pts):
    """Convex hull (monotone chain) of (x, y) points."""
    pts = sorted(set(pts))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lo, up = [], []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def _pad(hull, cx, cy, r):
    out = []
    for x, y in hull:
        dx, dy = x - cx, y - cy
        n = math.hypot(dx, dy) or 1
        out.append((x + dx / n * r, y + dy / n * r))
    return out


def _smooth(pts):
    """Closed Catmull-Rom curve through the points, as cubic Beziers: a soft region, not a polygon."""
    n = len(pts)
    if n < 3:
        return ""
    d = [f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(n):
        p0, p1, p2, p3 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return " ".join(d) + "Z"


def cohort_map(cl, pid):
    """Quiet map: a soft region per subgroup (its core 85% of tumors), small dots, the selected tumor called out."""
    pts = cl.get("patients", [])
    if not pts:
        return ""
    W, H, L, R, T, B = 1120, 430, 40, 40, 64, 48
    xs, ys = [p["x"] for p in pts], [p["y"] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    X = lambda v: L + (v - x0) / ((x1 - x0) or 1) * (W - L - R)  # noqa: E731
    Y = lambda v: H - B - (v - y0) / ((y1 - y0) or 1) * (H - T - B)  # noqa: E731
    out = [f"<svg class='cmap2' viewBox='0 0 {W} {H}' width='100%' role='img' aria-label='Cohort map: {len(pts)} tumors "
           f"in {len(cl.get('clusters', []))} molecular subgroups, the selected tumor called out'>"]
    labels = []
    for c in cl.get("clusters", []):
        k = c["cluster"]
        mem = [(X(p["x"]), Y(p["y"])) for p in pts if p["cluster"] == k]
        if len(mem) < 3:
            continue
        cx, cy = sum(a for a, _ in mem) / len(mem), sum(b for _, b in mem) / len(mem)
        core = sorted(mem, key=lambda q: (q[0] - cx) ** 2 + (q[1] - cy) ** 2)[: max(3, int(len(mem) * .85))]
        hull = _pad(_hull(core), cx, cy, 22)
        d = _smooth(hull)
        col = ccolor(k)
        out.append(f"<path class='reg c{k}' d='{d}' fill='{col}' fill-opacity='.075'/>")
        top = min(hull, key=lambda q: q[1])
        labels.append((k, top[0], max(T - 6, top[1] - 26), c.get("name", f"Subgroup {k}"), c.get("n")))
    me = None
    for p in pts:
        if p["patient_id"] == pid:
            me = p
            continue
        out.append(f"<circle class='dot c{p['cluster']}' cx='{X(p['x']):.1f}' cy='{Y(p['y']):.1f}' r='3.6' "
                   f"fill='{ccolor(p['cluster'])}' fill-opacity='.8'><title>{esc(p['patient_id'])}</title></circle>")
    for k, x, y, name, n in labels:
        out.append(f"<g class='lab c{k}'><circle cx='{x - 2:.1f}' cy='{y - 5:.1f}' r='5' fill='{ccolor(k)}'/>"
                   f"<text x='{x + 8:.1f}' y='{y:.1f}' font-size='17' font-weight='600' fill='{INK}'>{esc(name)}</text>"
                   f"<text x='{x + 8:.1f}' y='{y + 19:.1f}' font-size='14' fill='{MUTED}'>{n} tumors</text></g>")
    if me:
        mx, my = X(me["x"]), Y(me["y"])
        right = mx < W - 260
        tx, anc = (mx + 18, "start") if right else (mx - 18, "end")
        halo = f"stroke='{PAPER}' stroke-width='5' paint-order='stroke' stroke-linejoin='round'"
        out.append(f"<circle cx='{mx:.1f}' cy='{my:.1f}' r='15' fill='{ccolor(me['cluster'])}' fill-opacity='.2'/>"
                   f"<circle cx='{mx:.1f}' cy='{my:.1f}' r='6.5' fill='{ccolor(me['cluster'])}' stroke='{INK}' stroke-width='2.2'>"
                   f"<title>{esc(pid)}</title></circle>"
                   f"<text x='{tx:.1f}' y='{my - 2:.1f}' text-anchor='{anc}' font-size='18' font-weight='600' fill='{INK}' {halo}>"
                   f"{esc(pid)}</text><text x='{tx:.1f}' y='{my + 17:.1f}' text-anchor='{anc}' font-size='14' fill='{MUTED}' "
                   f"{halo}>this tumor</text>")
    out.append(f"<text x='0' y='{H - 12}' font-size='13' fill='{MUTED}'>Proteomic map: the two main axes of variation "
               f"across {len(pts)} tumors. Nearby tumors have similar protein profiles.</text></svg>")
    return "".join(out)


def cohort_css(cl):
    cs = [c["cluster"] for c in cl.get("clusters", [])]
    return "<style>" + "\n".join(
        f".stApp:has(.cmap2 .c{c}:hover) .cmap2 :is(.dot, .reg, .lab):not(.c{c}) {{ opacity:.15 }}" for c in cs) + "</style>"


def _bars(items, key, fmt, side):
    """Diverging bar list: symbol, a bar sized to |value| against the list's largest, the value."""
    if not items:
        return "<p class='gcap'>none listed</p>"
    top = max(abs(i[key]) for i in items if num(i.get(key))) or 1
    rows = []
    for i in items:
        v = i.get(key)
        w = 100 * abs(v) / top if num(v) else 0
        rows.append(f"<div class='br {side}'><span class='bs'>{esc(i['symbol'])}</span><span class='bt'><i style='width:{w:.0f}%'>"
                    f"</i></span><span class='bv'>{fmt(v) if num(v) else ''}</span></div>")
    return "<div class='bars2'>" + "".join(rows) + "</div>"


def how(text):
    """Method notes, folded away: one click for the judge who asks, nothing for everyone else."""
    return f"<details class='how'><summary>How this was computed</summary><p>{text}</p></details>"


def subgroup_spec(cl, pid):
    c = patient_cluster(cl, pid)
    info = cluster_info(cl, c)
    if not info:
        return ""
    co = next((p for p in cl["patients"] if p["patient_id"] == pid), {}).get("co_assignment")
    ph = cl.get("post_hoc") or {}
    stage_p = (ph.get("y_stage") or {}).get("chi2_p") if isinstance(ph, dict) else None
    stats = [(f"{info['n']}", "tumors", False)]
    if num(co):
        stats.append((f"{co:.0%}", "co-clustered in reruns", co < UNSTABLE))
    if num(info.get("jaccard")):
        stats.append((f"{info['jaccard']:.2f}", "Jaccard reproducibility", False))
    if num(stage_p):
        stats.append((f"p {stage_p:.2g}", "stage link, post hoc", False))
    stat_html = "".join(f"<div class='st{' warn' if w else ''}'><b>{v}</b><span>{l}</span></div>" for v, l, w in stats)
    sig = info.get("signature_proteins", [])
    up = [x for x in sig if x.get("direction") == "up"][:3]
    dn = [x for x in sig if x.get("direction") == "down"][:3]
    pws = "".join(f"<li>{esc(p['name'])}</li>" for p in info.get("pathways", [])[:3])
    stab = (f"Whole-partition stability {cl['stability_ari']:.2f}" + (f" ± {cl['stability_sd']:.2f}" if num(cl.get("stability_sd")) else "")
            + " (bootstrap ARI, 100 reruns). " if num(cl.get("stability_ari")) else "")
    ks = cl.get("k_selection") or []
    ksel = ", ".join(f"k={x['k']}: {x['silhouette']:.3f}" for x in ks[:3]) if ks else ""
    tests = [k for k, v in (ph.items() if isinstance(ph, dict) else []) if isinstance(v, dict) and num(v.get("chi2_p"))]
    adj = (f" Stage p = {stage_p:.2g} unadjusted; {len(tests)} outcomes tested ({', '.join(t.replace('y_', '') for t in tests)}), "
           f"so Bonferroni-adjusted p = {min(1, stage_p * len(tests)):.2g}." if num(stage_p) and tests else "")
    note = (f"Clustering: {esc(cl.get('method') or '')}, on proteomics only. k chosen by silhouette across k = 2 to 6"
            + (f" ({ksel}; k = 2 and 3 are close)" if ksel and len(ks) > 1 and abs(ks[0]['silhouette'] - ks[1]['silhouette']) < .01
               else (f" ({ksel})" if ksel else "")) + f". {stab}"
            "Co-clustered in reruns: across 100 refits on 80% subsamples, how often this tumor clusters with its subgroup "
            "mates. Jaccard reproducibility: overlap of this subgroup with its best match in each refit (above 0.75 stable). "
            "Signatures and pathways come from the same proteins used to cluster, so they describe the subgroups rather "
            "than validate them. Protein values: mean difference in standardized abundance. Pathways: Reactome enrichment, "
            f"FDR q under 0.05.{adj} Stage was tested only after clustering and never used to build subgroups. "
            "GNN-embedding clustering comparison: " + ("pending." if (cl.get("comparison") or {}).get("status") else "see results.")
            + " Exploratory molecular subgroups, not clinical subtypes.")
    return (f"<div class='spec'><div class='stats'>{stat_html}</div>"
            f"<div class='spec-grid'><div><h4>Higher</h4>{_bars(up, 'effect', lambda v: f'{v:+.2f}', 'up')}</div>"
            f"<div><h4>Lower</h4>{_bars(dn, 'effect', lambda v: f'{v:+.2f}', 'dn')}</div>"
            f"<div><h4>Enriched pathways</h4><ul class='pwl'>{pws or '<li>none</li>'}</ul></div></div>{how(note)}</div>")


def _demo(raw_summary):
    parts = [p.strip() for p in (raw_summary or "").split(";")]
    keep = [p for p in parts if p and not p.lower().startswith(("comorbidities", "medications"))]
    return ", ".join(keep).rstrip(".")


def patient_answer(brief, ref_word):
    drivers = [m for m in brief.get("mutations", []) if m.get("known_driver")]
    up = brief.get("proteins_up", [])[:2]
    bits = []
    if drivers:
        bits.append(f"<b>{esc(drivers[0]['symbol'])}</b> driver mutation" + (f" (+{len(drivers) - 1} more)" if len(drivers) > 1 else "") + ".")
    if up:
        bits.append(f"{' and '.join(esc(u['symbol']) for u in up)} highest vs {ref_word}.")
    return " ".join(bits) or "No driver mutation or large protein change recorded."


def patient_spec(brief, raw):
    raw = raw or {}
    muts = brief.get("mutations", [])
    drivers = [m for m in muts if m.get("known_driver")]
    others = [m for m in muts if not m.get("known_driver")]
    mut_rows = "".join(f"<li><b>{esc(m['symbol'])}</b> <span>{esc(m.get('protein_changes') or '')}</span><em>driver</em></li>"
                       for m in drivers)
    mut_rows += "".join(f"<li>{esc(m['symbol'])} <span>{esc(m.get('protein_changes') or '')}</span></li>" for m in others[:3])
    up = [{"symbol": x["symbol"], "z": x.get("z")} for x in brief.get("proteins_up", [])[:4]]
    dn = [{"symbol": x["symbol"], "z": x.get("z")} for x in brief.get("proteins_down", [])[:2]]
    ref = {"normal": "normal kidney", "cohort": "other tumors"}.get(brief.get("reference"), "reference")
    finds = "".join(f"<span class='tag'>{esc(f.get('name'))}</span>" for f in brief.get("findings", [])) or "<span class='gcap'>none</span>"
    meds = raw.get("medications", [])
    miss = brief.get("missing", [])
    note = (f"Patient: {esc(_demo(brief.get('summary', '')))}. Protein values are z-scores against {ref}. "
            + (f"{len(meds)} medications on record: {esc(', '.join(m.get('name', '') for m in meds))}. " if meds else "")
            + ("Not available: " + esc(" ".join(miss)) + " " if miss else "")
            + "Stage, grade and outcome are never shown or used.")
    return (f"<div class='spec pspec'><div class='spec-grid p3'>"
            f"<div><h4>Mutations</h4><ul class='muts'>{mut_rows or '<li>none recorded</li>'}</ul></div>"
            f"<div><h4>Proteins vs {esc(ref)}</h4>{_bars(up, 'z', lambda v: f'{v:+.1f}', 'up')}{_bars(dn, 'z', lambda v: f'{v:+.1f}', 'dn')}</div>"
            f"<div><h4>On record</h4><div class='tags'>{finds}</div></div></div>{how(note)}</div>")


def glance(items):
    """The model's outputs for this tumor, one tile each: (label, value, sub, tone)."""
    return "<div class='glance'>" + "".join(
        f"<div class='gl {tone}'><span class='gll'>{esc(l)}</span><b>{v}</b><span class='gls'>{s}</span></div>"
        for l, v, s, tone in items) + "</div>"


CSS = f"""
.chap {{ margin:3.4rem 0 1.1rem; max-width:62rem; }}
.chap [data-testid="stHeaderActionElements"], .chap h2 a {{ display:none !important; }}
.chap-n {{ font-size:.9rem; color:{MUTED}; letter-spacing:.02em; display:flex; gap:.9rem; align-items:baseline; }}
.chap-n b {{ font-family:"Newsreader",serif; font-weight:400; font-size:1.05rem; color:{INK}; }}
.chap-n span {{ border-left:1px solid {RULE}; padding-left:.9rem; }}
.chap-q {{ font-size:clamp(2rem,3.4vw,2.85rem) !important; line-height:1.08; margin:.55rem 0 .7rem !important; }}
div[data-testid='stMarkdownContainer'] p.chap-a {{ font-family:"Newsreader",serif; font-size:1.4rem; line-height:1.45; color:{BODY}; max-width:58ch; margin:0; }}
.chap-a b {{ font-weight:500; color:{INK}; }} .chap-a b.warn, .st.warn b {{ color:#a3304f; }}
.cmap2 {{ display:block; margin:.4rem 0 0; overflow:visible; }}
.cmap2 .dot, .cmap2 .reg, .cmap2 .lab {{ transition: opacity .18s; }}
.cmap2 .reg, .cmap2 .lab {{ cursor:default; }}
.spec {{ border-top:1px solid {INK}; padding-top:1.2rem; margin-top:1.2rem; }}
.stats {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1.5rem; padding-bottom:1rem; border-bottom:1px solid {RULE}; }}
.st b {{ display:block; font-family:"Newsreader",serif; font-weight:400; font-size:2.3rem; line-height:1; color:{INK}; }}
.st span {{ display:block; font-size:.88rem; color:{MUTED}; margin-top:.45rem; line-height:1.4; max-width:22ch; }}
.spec-grid {{ display:grid; grid-template-columns: 1fr 1fr 1.5fr; gap:2.6rem; padding:1.3rem 0 .4rem; }}
.spec-grid.p3 {{ grid-template-columns: 1fr 1.25fr 1.15fr; }}
.spec h4 {{ font-family:"Public Sans",sans-serif !important; font-size:.84rem !important; font-weight:600 !important;
           color:{MUTED} !important; margin:0 0 .75rem !important; letter-spacing:.01em; }}
.spec h4 + * + h4, .spec .rec + h4, .spec ul + h4 {{ margin-top:1.3rem !important; }}
.bars2 {{ display:flex; flex-direction:column; gap:.5rem; margin-bottom:.9rem; }}
.br {{ display:grid; grid-template-columns: 5.6rem 1fr 3.2rem; align-items:center; gap:.7rem; font-size:.95rem; }}
.br .bs {{ font-weight:600; color:{INK}; }} .br .bv {{ text-align:right; color:{MUTED}; font-variant-numeric:tabular-nums; font-size:.88rem; }}
.br .bt {{ height:8px; background:#efecf4; border-radius:4px; overflow:hidden; }}
.br .bt i {{ display:block; height:8px; border-radius:4px; background:#d6457a; }} .br.dn .bt i {{ background:#4f3fbf; }}
.pw table {{ width:100%; border-collapse:collapse; font-size:.92rem; border:none !important; }}
.pw th, .pw td {{ border-left:none !important; border-right:none !important; border-top:none !important; background:transparent !important; }}
.pw th {{ text-align:left; font-weight:500; color:{MUTED}; font-size:.8rem; padding:0 0 .4rem; border-bottom:1px solid {RULE}; }}
.pw td {{ padding:.5rem 0; border-bottom:1px solid {RULE}; color:{BODY}; line-height:1.35; vertical-align:top; }}
.pw .n {{ text-align:right; white-space:nowrap; padding-left:.8rem; font-variant-numeric:tabular-nums; color:{MUTED}; }}
.foot2 {{ font-size:.84rem; color:{MUTED}; line-height:1.55; margin:.9rem 0 0; max-width:80ch; }}
div[data-testid='stMarkdownContainer'] p.demog {{ font-size:1.05rem; color:{BODY}; margin:0 0 .2rem; }}
.muts, .miss2 {{ list-style:none; padding:0 !important; margin:0 !important; }} .miss2 li {{ margin:0 0 .3rem; }} .muts li {{ margin:0 !important; }}
.muts li {{ padding:.45rem 0; border-bottom:1px solid {RULE}; font-size:.98rem; color:{BODY}; display:flex; gap:.6rem; align-items:baseline; flex-wrap:wrap; }}
.muts li b {{ color:{INK}; }} .muts li span {{ color:{MUTED}; font-size:.88rem; font-variant-numeric:tabular-nums; }}
.muts li em {{ font-style:normal; font-size:.74rem; font-weight:600; color:#a3304f; background:#fbe3ea; border-radius:4px; padding:.05rem .4rem; }}
.rec {{ font-size:.95rem; line-height:1.5; color:{BODY}; margin:0 0 .4rem; }} .mutedp {{ color:{MUTED}; font-size:.88rem; }}
.miss2 {{ margin:0; padding-left:1rem; font-size:.9rem; color:{BODY}; line-height:1.5; }}
.empty {{ border:1px dashed #c9c4d6; border-radius:10px; padding:2rem 2.2rem; max-width:44rem; }}
.empty b {{ font-family:"Newsreader",serif; font-weight:400; font-size:1.45rem; color:{INK}; display:block; margin-bottom:.4rem; }}
.empty p {{ margin:0; color:{BODY}; line-height:1.55; }}
.how {{ margin:.9rem 0 0; font-size:.85rem; color:{MUTED}; }}
.how summary {{ cursor:pointer; color:{MUTED}; list-style:none; }} .how summary::before {{ content:"+ "; }}
.how[open] summary::before {{ content:"− "; }} .how p {{ margin:.5rem 0 0; line-height:1.55; max-width:80ch; }}
.pwl {{ list-style:none; padding:0 !important; margin:0 !important; }}
.pwl li {{ padding:.42rem 0; border-bottom:1px solid {RULE}; font-size:.94rem; color:{BODY}; line-height:1.35; margin:0 !important; }}
.tags {{ display:flex; flex-wrap:wrap; gap:.4rem; }}
.tag {{ font-size:.86rem; background:#f1eef7; color:{BODY}; border-radius:5px; padding:.15rem .5rem; }}
.glance {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1px; background:{RULE}; border:1px solid {RULE};
          border-radius:10px; overflow:hidden; margin:1.2rem 0 .4rem; }}
.gl {{ background:#fff; padding:1rem 1.1rem 1.05rem; display:flex; flex-direction:column; gap:.2rem; min-height:6.4rem; }}
.gl .gll {{ font-size:.78rem; font-weight:600; color:{MUTED}; letter-spacing:.02em; }}
.gl b {{ font-family:"Newsreader",serif; font-weight:400; font-size:1.65rem; line-height:1.15; color:{INK}; }}
.gl .gls {{ font-size:.82rem; color:{MUTED}; line-height:1.35; }}
.gl.warn b {{ color:#a3304f; }} .gl.off b {{ color:{MUTED}; font-size:1.25rem; }}
@media (max-width: 860px) {{ .glance {{ grid-template-columns:1fr 1fr; }} }}
.toolbar {{ display:flex; align-items:center; gap:1rem; padding:.2rem 0 .4rem; }}
@media (max-width: 860px) {{ .stats {{ grid-template-columns:1fr 1fr; }} .spec-grid, .spec-grid.p3 {{ grid-template-columns:1fr; }} }}
"""
