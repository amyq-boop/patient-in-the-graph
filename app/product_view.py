"""Product panels: cohort map, subgroup card, patient brief, drug targets and evidence gaps.

Inputs are the shared JSON formats agreed on Nov 7 (clusters.json, briefs/<patient_id>.json) and the agent trace's
run view (app.run_view.build). Every number and name shown comes from those files; nothing is computed into a claim.
"""
from __future__ import annotations

import html

from app.run_view import DRUG_RELS, strip_ids, why_removed

# subgroup hues, validated for colour-blind separation on the page surface (dataviz validator, light mode);
# kept apart from the evidence colours (eosin = observed, violet = known biology, amber = drug)
CLUSTER = ["#0e8f82", "#3567c9", "#d07a1f", "#8b5fb3"]


def esc(s):
    return html.escape(str(s))


def ccolor(c):
    return CLUSTER[(int(c) - 1) % len(CLUSTER)]


def patient_cluster(cl, pid):
    return next((p["cluster"] for p in (cl or {}).get("patients", []) if p["patient_id"] == pid), None)


def cluster_info(cl, c):
    return next((x for x in (cl or {}).get("clusters", []) if x["cluster"] == c), None)


def cohort_svg(cl, pid):
    pts = cl.get("patients", [])
    if not pts:
        return ""
    xs, ys = [p["x"] for p in pts], [p["y"] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    W, H, pad = 760, 360, 28
    X = lambda v: pad + (v - x0) / ((x1 - x0) or 1) * (W - 2 * pad)  # noqa: E731
    Y = lambda v: H - pad - (v - y0) / ((y1 - y0) or 1) * (H - 2 * pad)  # noqa: E731
    names = {c["cluster"]: c.get("name", f"Subgroup {c['cluster']}") for c in cl.get("clusters", [])}
    out = [f"<svg class='cmap' viewBox='0 0 {W} {H}' width='100%' role='img' aria-label='Cohort map: "
           f"{len(pts)} patients in {len(names)} molecular subgroups, the selected patient ringed'>"]
    me = None
    for p in pts:
        if p["patient_id"] == pid:
            me = p
            continue
        out.append(f"<circle class='dot c{p['cluster']}' cx='{X(p['x']):.1f}' cy='{Y(p['y']):.1f}' r='5.5' "
                   f"fill='{ccolor(p['cluster'])}' stroke='#fbfafc' stroke-width='1.5'><title>{esc(p['patient_id'])}: "
                   f"{esc(names.get(p['cluster'], p['cluster']))}</title></circle>")
    if me:
        cx, cy = X(me["x"]), Y(me["y"])
        lx = cx + 16 if cx < W - 160 else cx - 16
        anchor = "start" if cx < W - 160 else "end"
        out.append(f"<circle class='me' cx='{cx:.1f}' cy='{cy:.1f}' r='9' fill='{ccolor(me['cluster'])}' stroke='#1b1530' "
                   f"stroke-width='2.5'><title>{esc(pid)}: {esc(names.get(me['cluster'], ''))}</title></circle>"
                   f"<text x='{lx:.1f}' y='{cy + 5:.1f}' text-anchor='{anchor}' font-size='15' font-weight='600' "
                   f"fill='#1b1530'>{esc(pid)}</text>")
    out.append("</svg>")
    return "".join(out)


def cohort_legend(cl):
    return "<div class='clegend'>" + "".join(
        f"<span class='cl c{c['cluster']}'><i style='background:{ccolor(c['cluster'])}'></i>{esc(c.get('name', c['cluster']))}"
        f"<small>{c.get('n', '')} patients</small></span>" for c in cl.get("clusters", [])) + "</div>"


def cohort_css(cl):
    """Hovering a legend entry or a dot of one subgroup dims the others."""
    cs = [c["cluster"] for c in cl.get("clusters", [])]
    rules = [f".stApp:has(.clegend .c{c}:hover) .cmap .dot:not(.c{c}), .stApp:has(.cmap .dot.c{c}:hover) .cmap "
             f".dot:not(.c{c}) {{ opacity:.12 }}" for c in cs]
    return "<style>" + "\n".join(rules) + "</style>"


def subgroup_html(cl, pid):
    c = patient_cluster(cl, pid)
    info = cluster_info(cl, c)
    if not info:
        return "<div class='sgcard'><p class='gcap'>This patient isn't in the subgroup file yet.</p></div>"
    sig = "".join(f"<span class='sg'>{esc(s['symbol'])} {'↑' if s.get('direction') == 'up' else '↓'}"
                  + (f"<small>{s['effect']:+.1f}</small>" if isinstance(s.get("effect"), (int, float)) else "")
                  + "</span>" for s in info.get("signature_proteins", [])[:8])
    def pw_line(p):
        bits = []
        if p.get("edge_ids"):
            bits.append(", ".join(esc(e) for e in p["edge_ids"]))
        if isinstance(p.get("overlap"), int) and isinstance(p.get("pathway_size"), int):
            bits.append(f"{p['overlap']} of {p['pathway_size']} genes")
        if isinstance(p.get("q"), (int, float)):
            bits.append(f"q = {p['q']:.2g}")
        return f"<li>{esc(p['name'])} <span class='kind'>({'; '.join(bits) or 'no test result'})</span></li>"
    pws = "".join(pw_line(p) for p in info.get("pathways", [])[:4])
    has_edges = any(p.get("edge_ids") for p in info.get("pathways", []))
    comp = cl.get("comparison") or {}
    pr, gn = comp.get("proteomics") or {}, comp.get("gnn_embedding") or {}
    num = lambda x: isinstance(x, (int, float))  # noqa: E731
    stab = []
    if num(cl.get("stability_ari")):
        stab.append(f"Bootstrap stability ARI {cl['stability_ari']:.2f}"
                    + (f" ± {cl['stability_sd']:.2f}" if num(cl.get("stability_sd")) else ""))
    if num(info.get("jaccard")):
        stab.append(f"this subgroup reproduces at Jaccard {info['jaccard']:.2f}")
    if num(cl.get("silhouette")):
        stab.append(f"silhouette {cl['silhouette']:.2f}")
    stab = (", ".join(stab) + f". k = {cl.get('k')} from {esc(cl.get('features') or 'proteomics')}.") if stab else ""
    if num(pr.get("stability_ari")) and num(gn.get("stability_ari")):
        vs = f" Proteomics-only clusters {pr['stability_ari']:.2f}, GNN-embedding clusters {gn['stability_ari']:.2f}."
    elif comp.get("status"):
        vs = " GNN-embedding comparison: pending."
    else:
        vs = ""
    ph = cl.get("post_hoc") or {}
    post = (f" Checked after clustering: stage differs between subgroups (χ² p = {ph['y_stage']['chi2_p']:.2g}), "
            "but stage was never used to build them." if isinstance(ph, dict) and num((ph.get("y_stage") or {}).get("chi2_p"))
            else " Stage and grade were not used to build them.")
    return (f"<div class='sgcard' style='--cc:{ccolor(c)}'><div class='k'>Subgroup</div><div class='sgname'>{esc(info.get('name', c))}"
            f"</div><div class='gcap'>{info.get('n', '?')} of {len(cl.get('patients', []))} patients</div>"
            f"<div class='k'>Distinguishing proteins (mean difference vs the rest)</div><div>{sig or 'none listed'}</div>"
            f"<div class='k'>{'Pathways, linked by graph edges' if has_edges else 'Enriched pathways (Reactome, FDR q)'}</div>"
            f"<ul class='sources'>{pws or '<li>none listed</li>'}</ul>"
            f"<div class='sgfoot'>{stab}{vs}{post} Exploratory molecular subgroups, not clinical subtypes.</div></div>")


def brief_html(brief, info, pid):
    """Patient brief from briefs/<pid>.json when it exists, else from the get_patient result."""
    b = brief or {}

    def chips(xs, arrow=""):
        return "".join(f"<span class='ic obs' title='{esc(x.get('id') or x.get('edge_id', ''))}'>"
                       f"{esc(x.get('symbol') or x.get('name'))}{' ' + arrow if arrow else ''}"
                       + (f"<small>{x['z']:+.1f}</small>" if isinstance(x.get("z"), (int, float)) else "") + "</span>"
                       for x in xs) or "<span class='gcap'>none</span>"
    up = b.get("proteins_up") or info.get("proteins_up", [])
    down = b.get("proteins_down") or info.get("proteins_down", [])
    muts = b.get("mutations") or info.get("mutations", [])
    finds = b.get("findings") or info.get("phenotypes", [])
    missing = b.get("missing") or []
    summary = b.get("summary") or info.get("clinical_summary", "")
    ref = {"normal": "matched normal tissue", "cohort": "the rest of the cohort"}.get(b.get("reference"), "reference")
    mchips = "".join(f"<span class='ic obs{' drv' if m.get('known_driver') else ''}' title='{esc(m.get('id') or m.get('edge_id', ''))}'>"
                     f"{esc(m.get('symbol'))}<small>{esc(m.get('protein_changes') or '')}</small></span>"
                     for m in muts[:8]) or "<span class='gcap'>none</span>"
    miss = ("<ul class='miss'>" + "".join(f"<li>{esc(m)}</li>" for m in missing) + "</ul>") if missing else \
        "<span class='gcap'>none listed</span>"
    return (f"<div class='intake brief'><div><div class='k'>Patient</div><div class='pid'>{esc(pid)}</div>"
            f"<div class='sum'>{esc(summary)}</div></div>"
            f"<div><div class='k'>Tumor proteins vs {ref} (z-score)</div>{chips(up, '↑')}{chips(down, '↓') if down else ''}"
            f"<div class='k' style='margin-top:.6rem'>Somatic mutations</div>{mchips}</div>"
            f"<div><div class='k'>On record</div>{chips(finds)}<div class='k' style='margin-top:.6rem'>Missing</div>{miss}</div>"
            "</div>")


def targets(v):
    """Drug, target, evidence ID, cited? Built only from drug edges and drug rankings the tools returned."""
    rows, seen = [], set()
    for k, e in v["edges"].items():
        if e["rel"] in DRUG_RELS and not k.startswith(("pt:", "rank:", "contrib:")):
            d, t = (e["src"], e["dst"]) if v["nodes"][e["src"]]["type"] == "drug" else (e["dst"], e["src"])
            if (d, t) in seen:
                continue
            seen.add((d, t))
            rows.append({"drug": v["nodes"][d]["name"], "target": v["nodes"][t]["name"].rstrip(" ↑↓"), "id": k,
                         "cited": k in v["cited"]})
    ranked = (v["calls"].get("rank_for_patient") or {})
    if ranked.get("node_type") == "drug":
        named = {r["drug"] for r in rows}
        for it in ranked.get("items", [])[:5]:
            if it["name"] not in named:
                rows.append({"drug": it["name"], "target": "ranked by the GNN for this patient",
                             "id": it.get("evidence_id", ""), "cited": it.get("evidence_id") in v["cited"]})
    return sorted(rows, key=lambda r: not r["cited"])


def targets_html(v):
    rows = targets(v)
    if not rows:
        return "<p class='gcap'>No drug edges came back for this question.</p>"
    body = "".join(f"<tr><td><b>{esc(r['drug'])}</b></td><td>{esc(r['target'])}</td><td class='kind'>{esc(r['id'])}</td>"
                   f"<td>{'cited' if r['cited'] else 'retrieved'}</td></tr>" for r in rows)
    return ("<table class='ttab'><tr><th>Drug</th><th>Acts on</th><th>Evidence</th><th>Status</th></tr>" + body +
            "</table><p class='gcap'>A drug-target edge is a reason to test, not evidence that this patient will "
            "respond.</p>")


def gaps(v, brief):
    out = [("Missing data", m) for m in (brief or {}).get("missing", [])]
    out += [("Needs evidence", f"“{strip_ids(c['text'])}” {why_removed(c)}") for c in v["claims"]["removed"]]
    out += [("Needs testing", f"“{strip_ids(c['text'])}” Plausible, not yet backed by evidence.")
            for c in v["claims"]["hypothesis"]]
    for n in (v["trace"].get("verifier") or {}).get("non_claims", [])[:3]:
        out.append(("Not scored", f"“{strip_ids(n.get('text', ''))}” ({n.get('reason', 'caveat')})"))
    return out


def gaps_html(v, brief):
    g = gaps(v, brief)
    if not g:
        return "<p class='gcap'>No gaps flagged for this run.</p>"
    return "<ul class='gaps'>" + "".join(f"<li><span class='gtag'>{esc(k)}</span>{esc(t)}</li>" for k, t in g) + "</ul>"


def subgroup_explain(cl, pid):
    c = patient_cluster(cl, pid)
    info = cluster_info(cl, c)
    if not info:
        return "This patient isn't in the subgroup file yet."
    sig = ", ".join(f"{s['symbol']} {'up' if s.get('direction') == 'up' else 'down'}" for s in info.get("signature_proteins", [])[:4])
    pws = ", ".join(p["name"] for p in info.get("pathways", [])[:3])
    stab = (f" The grouping holds up at a bootstrap stability of {cl['stability_ari']:.2f}."
            if isinstance(cl.get("stability_ari"), (int, float)) else "")
    return (f"{pid} sits in {info.get('name', c)}, {info.get('n', '?')} of {len(cl.get('patients', []))} patients. "
            f"What sets it apart: {sig or 'no signature listed'}. Linked pathways: {pws or 'none listed'}.{stab} "
            "These are exploratory molecular subgroups, not clinical subtypes, and stage and grade were not used to build them.")
