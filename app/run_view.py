"""Run view: turns one contract trace into the replayed run, the guide's narration and the hover-linked evidence.

Everything here is computed from the trace itself (tool results, claims, verifier stats). The guide never invents
facts: each line it says is a count or a name the tools returned, or a sentence from the verified answer.
Hover linking is pure CSS (:has selectors), so the graph and the answer stay in the main page with no iframe.
"""
from __future__ import annotations

import html
import re

from app.evidence import ID_RE, MODEL_PREFIXES, REL_WORDS, claim_edges, evidence, source_label

PRIMEKG_EDGES = 4_050_000

# Color carries meaning, the same way on the site, graph and slides:
#   observed in this patient = eosin pink, curated knowledge = hematoxylin violet, drug = amber
ROLE = {"observed": ("#fbe3ea", "#a3304f", "#d6457a"), "known": ("#e9e6f7", "#3b2f8f", "#4f3fbf"),
        "drug": ("#f6edd2", "#6b5100", "#b88a00")}
LABEL = {"observed": "Observed in this patient", "known": "Known biology", "drug": "Drug and its target"}
DRUG_RELS = {"drug_protein", "indication", "drug_effect", "treats", "off-label use", "contraindication"}
STAGES = [("patient", "Patient record"), ("score", "Stage score"), ("retrieve", "GNN retrieval"),
          ("lookup", "Mechanism"), ("draft", "Agent draft"), ("verify", "Verifier")]


def esc(s):
    return html.escape(str(s))


def _norm_ref(r):
    return r.replace("PMID: ", "PMID:").strip()


def refs_in(text):
    return [_norm_ref(r) for m in ID_RE.finditer(text) for r in re.split(r"\s*[,;]\s*", m.group(1))]


def strip_ids(text):
    return re.sub(r"\s+([.,;])", r"\1", ID_RE.sub("", text)).replace("**", "").strip()


def _plain(s):
    return re.sub(r"[^a-z0-9]+", " ", strip_ids(s).lower()).strip()


# ------------------------------------------------------------------ evidence roles
def edge_role(key, e):
    if key.startswith(("pt:", "contrib:", "rank:")):
        return "observed"
    return "drug" if e["rel"] in DRUG_RELS else "known"


def node_role(n, observed_nodes):
    if n["type"] == "patient" or n["curie"] in observed_nodes:
        return "observed"
    return "drug" if n["type"] == "drug" else "known"


def ref_targets(ref, edges, steps):
    """Edge keys and node CURIEs one citation points at. PMIDs and mechanism-step IDs map onto the mechanism chain."""
    if ref in edges:
        e = edges[ref]
        return [ref], {e["src"], e["dst"]}
    hit = {s["mechanism"] for s in steps if ref in s.get("pmids", []) or s.get("edge_id") == ref}
    if not hit:
        return [], set()
    chain = [k for k, e in edges.items() if e["rel"] == "causes" and e["src"] in hit and e["dst"] in hit]
    chain = chain or [k for k, e in edges.items() if e["rel"] == "causes" and e["dst"] in hit]
    return chain, hit | {n for k in chain for n in (edges[k]["src"], edges[k]["dst"])}


# ------------------------------------------------------------------ answer text -> sentences
SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"'“])")


def blocks(text):
    """Answer text -> [(kind, [sentence, ...])], kind in h | p | li. Drops the verifier's footer line."""
    out = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("_Verifier"):
            continue
        m = re.match(r"#+\s*(.+)", s)
        if m:
            out.append(("h", [m.group(1).strip("* ")]))
            continue
        m = re.match(r"(?:[-*•]|\d+[.)])\s+(.+)", s)
        out.append(("li" if m else "p", [x for x in SENT.split(m.group(1) if m else s) if x.strip()]))
    return out


def chipify(s, order):
    def sub(m):
        nums = []
        for ref in re.split(r"\s*[,;]\s*", m.group(1)):
            ref = _norm_ref(ref)
            if ref not in order:
                order.append(ref)
            nums.append(order.index(ref) + 1)
        return "".join(f'<a class="chip" href="#src-{n}">{n}</a>' for n in nums)

    t = ID_RE.sub(sub, html.escape(s, quote=False))
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    return re.sub(r"^Hypothesis \(unverified\):", "<span class='hyptag'>Hypothesis, unverified</span>", t)


def why_removed(c):
    if c.get("reason"):
        return c["reason"]
    if c.get("mismatched_ids"):
        return (f"It cites {', '.join(c['mismatched_ids'])}, but that evidence is about something else: "
                "the names in it don't match the sentence.")
    if c.get("unmatched_ids"):
        return f"It cites {', '.join(c['unmatched_ids'])}, which no tool returned in this run."
    return "It doesn't cite any edge or PubMed paper the tools returned."


# ------------------------------------------------------------------ the view model
def build(trace):
    nodes, edges, steps = evidence(trace)
    claims = trace.get("claims", [])
    by = {s: [c for c in claims if c.get("status") == s] for s in ("supported", "hypothesis", "removed")}
    calls = {}
    for tc in trace.get("tool_calls", []):
        if isinstance(tc.get("result"), dict) and "error" not in tc["result"]:
            calls.setdefault(tc["name"], tc["result"])

    order, sents, ans = [], [], []
    for kind, ss in blocks(trace.get("answer", "")):
        row = []
        for s in ss:
            ks, ns = [], set()
            for r in refs_in(s):
                k, n = ref_targets(r, edges, steps)
                ks += k
                ns |= n
            sid = len(sents)
            sents.append({"sid": sid, "keys": list(dict.fromkeys(ks)), "nodes": ns, "text": s})
            row.append((sid, chipify(s, order), bool(ks or ns)))
        ans.append((kind, row))

    removed_q = [_plain(c.get("quote") or c["text"]) for c in by["removed"]]
    hyp_q = [_plain(c.get("quote") or c["text"]) for c in by["hypothesis"]]

    def match(s, qs):
        p = _plain(s)
        return any(q and len(q) > 12 and (q in p or p in q) for q in qs)

    draft, dorder = [], []
    for kind, ss in blocks(trace.get("draft") or trace.get("answer", "")):
        row = []
        for s in ss:
            st = "removed" if match(s, removed_q) else ("hypothesis" if match(s, hyp_q) else "kept")
            row.append((chipify(re.sub(r"^Hypothesis \(unverified\):\s*", "", s), dorder), st))
        draft.append((kind, row))
    if by["removed"] and not any(st == "removed" for _, row in draft for _, st in row):
        # removed claims whose text never reached the draft verbatim: show them at the end, so the catch is visible
        draft.append(("p", [(esc(c["text"]), "removed") for c in by["removed"]]))

    sub = calls.get("retrieve_subgraph")
    cited = [k for s in sents for k in s["keys"]]
    cited += [k for c in by["supported"] for k in claim_edges(c, edges, steps)]
    keys = list(dict.fromkeys([k for k in edges if k.startswith("pt:")]
                              + ([e["edge_id"] for e in sub["edges"] if e["edge_id"] in edges] if sub else [])
                              + [k for k in cited if k in edges]))
    return {"trace": trace, "nodes": nodes, "edges": edges, "steps": steps, "claims": by, "calls": calls,
            "order": order, "sents": sents, "answer": ans, "draft": draft, "keys": keys,
            "cited": set(k for k in cited if k in edges),
            "hot_nodes": set().union(*[s["nodes"] for s in sents]) if sents else set()}


# ------------------------------------------------------------------ graph
def graph_svg(v, limit=18, intro=False):
    """Layered evidence graph: patient | measured | known biology | drugs. Classes drive the hover linking."""
    nodes, edges, keys, cited = v["nodes"], v["edges"], v["keys"], v["cited"]
    observed = {e["dst"] for k, e in edges.items() if k.startswith("pt:")}
    keep = list(dict.fromkeys(n for k in keys for n in (edges[k]["src"], edges[k]["dst"])))
    hot = v["hot_nodes"] | {n for k in cited for n in (edges[k]["src"], edges[k]["dst"])}
    if len(keep) > limit:
        keep = ([n for n in keep if n in hot] + [n for n in keep if n not in hot])[:limit]
    keepset = set(keep)
    keys = [k for k in keys if edges[k]["src"] in keepset and edges[k]["dst"] in keepset]
    v["kidx"] = {k: f"ek{i}" for i, k in enumerate(keys)}
    v["nidx"] = {c: f"nk{i}" for i, c in enumerate(keep)}

    def col(c):
        n = nodes[c]
        if n["type"] == "patient":
            return 0
        if c in observed:
            return 1
        return 3 if n["type"] == "drug" else 2

    cols = {i: [c for c in keep if col(c) == i] for i in range(4)}
    W, X = 900, [95, 310, 560, 805]
    rows = max(len(c) for c in cols.values()) or 1
    H = 56 + rows * 58
    pos = {}
    for i, cs in cols.items():
        top = (H - len(cs) * 58) / 2 + 40
        for j, c in enumerate(cs):
            pos[c] = (X[i], top + j * 58)
    out = [f'<svg class="egraph{" intro" if intro else ""}" viewBox="0 0 {W} {H}" width="100%" role="img" '
           'aria-label="Evidence graph: the facts the tools returned. Hover a line or a sentence to link them." '
           'style="font-family:Public Sans,sans-serif">']
    for k in sorted(keys, key=lambda k: k in cited):
        e = edges[k]
        (x1, y1), (x2, y2) = pos[e["src"]], pos[e["dst"]]
        role = edge_role(k, e)
        hit = k in cited
        c = ROLE[role][2]
        if abs(x1 - x2) < 1:  # same column (mechanism chain): bow out to the right
            d = f"M{x1 + 118},{y1} C{x1 + 175},{y1} {x2 + 175},{y2} {x2 + 118},{y2}"
        else:
            mx = (x1 + x2) / 2
            d = f"M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}"
        dash = ' stroke-dasharray="5 4"' if role == "observed" else ""
        bar = ""
        if role == "drug":  # inhibition bar at the target end
            tx = x2 + (18 if x1 > x2 else -18)
            bar = f'<line class="vis" x1="{tx}" y1="{y2 - 8}" x2="{tx}" y2="{y2 + 8}" stroke-width="3"/>'
        title = f"{nodes[e['src']]['name']} {REL_WORDS.get(e['rel'], e['rel'])} {nodes[e['dst']]['name']} [{k}]"
        out.append(f'<g class="g-e {v["kidx"][k]}{" cited" if hit else ""}" style="--c:{c}"><title>{esc(title)}</title>'
                   f'<path class="vis" d="{d}" fill="none"{dash}/>{bar}'
                   f'<path class="hit" d="{d}" fill="none" stroke="transparent" stroke-width="14"/></g>')
    for i, (cc, (x, y)) in enumerate(pos.items()):
        n = nodes[cc]
        fill, ink, _ = ROLE[node_role(n, observed)]
        label = n["name"] if len(n["name"]) <= 22 else n["name"][:21] + "…"
        w = min(230, max(54, 9.6 * len(label) + 26))
        out.append(f'<g class="g-n {v["nidx"][cc]}{" hot" if cc in hot else ""}" style="--d:{.04 * i:.2f}s">'
                   f'<title>{esc(n["name"])} ({esc(cc)})</title><rect x="{x - w / 2}" y="{y - 17}" width="{w}" '
                   f'height="34" rx="7" fill="{fill}"/><text x="{x}" y="{y + 6}" text-anchor="middle" font-size="16.5" '
                   f'font-weight="500" fill="{ink}">{esc(label)}</text></g>')
    for i, t in enumerate(["Patient", "Measured or on record", "Known biology", "Drugs"]):
        if cols[i]:
            out.append(f'<text x="{X[i]}" y="20" text-anchor="middle" font-size="14.5" fill="#5d596b">{t}</text>')
    out.append("</svg>")
    return "".join(out)


def hover_css(v):
    """Sentence hover lights its evidence; edge or node hover lights the sentences that cite it."""
    kidx, nidx, edges = v.get("kidx", {}), v.get("nidx", {}), v["edges"]
    r = [".stApp:has(.sn.cite:hover) .g-e, .stApp:has(.egraph .g-e:hover) .g-e, "
         ".stApp:has(.egraph .g-n:hover) .g-e { opacity:.12 }",
         ".stApp:has(.sn.cite:hover) .g-n, .stApp:has(.egraph .g-e:hover) .g-n, "
         ".stApp:has(.egraph .g-n:hover) .g-n { opacity:.28 }"]
    lit = "{ opacity:1 !important }"
    glow = "{ stroke:var(--c) !important; stroke-width:4px !important; opacity:1 }"
    by_key, by_node = {}, {}
    for s in v["sents"]:
        ek = [kidx[k] for k in s["keys"] if k in kidx]
        nk = [nidx[n] for n in s["nodes"] if n in nidx]
        for k in s["keys"]:
            by_key.setdefault(k, []).append(s["sid"])
        for n in s["nodes"]:
            by_node.setdefault(n, []).append(s["sid"])
        if ek or nk:
            r.append(f".stApp:has(#s{s['sid']}:hover) :is({', '.join('.' + x for x in ek + nk)}) {lit}")
        if ek:
            r.append(f".stApp:has(#s{s['sid']}:hover) :is({', '.join('.' + x for x in ek)}) .vis {glow}")
    for k, cls in kidx.items():
        e = edges[k]
        ends = [nidx[n] for n in (e["src"], e["dst"]) if n in nidx]
        r.append(f".stApp:has(.egraph .{cls}:hover) :is(.{cls}, {', '.join('.' + x for x in ends)}) {lit}")
        r.append(f".stApp:has(.egraph .{cls}:hover) .{cls} .vis {glow}")
        if by_key.get(k):
            r.append(f".stApp:has(.egraph .{cls}:hover) :is({', '.join(f'#s{i}' for i in by_key[k])}) "
                     "{ background:var(--hl); box-shadow:0 0 0 3px var(--hl) }")
    for n, cls in nidx.items():
        touch = [kidx[k] for k in kidx if n in (edges[k]["src"], edges[k]["dst"])]
        nbrs = {nidx[m] for k in kidx if n in (edges[k]["src"], edges[k]["dst"])
                for m in (edges[k]["src"], edges[k]["dst"]) if m in nidx}
        if touch:
            r.append(f".stApp:has(.egraph .{cls}:hover) :is({', '.join('.' + x for x in touch + sorted(nbrs))}) {lit}")
            r.append(f".stApp:has(.egraph .{cls}:hover) :is({', '.join('.' + x for x in touch)}) .vis {glow}")
        sids = sorted(set(by_node.get(n, [])) | {i for k in kidx if n in (edges[k]["src"], edges[k]["dst"])
                                                 for i in by_key.get(k, [])})
        if sids:
            r.append(f".stApp:has(.egraph .{cls}:hover) :is({', '.join(f'#s{i}' for i in sids)}) "
                     "{ background:var(--hl); box-shadow:0 0 0 3px var(--hl) }")
    return "<style>" + "\n".join(r) + "</style>"


# ------------------------------------------------------------------ html pieces
def answer_html(v):
    out = []
    for kind, row in v["answer"]:
        inner = " ".join(f"<span class='sn{' cite' if c else ''}' id='s{sid}'>{h}</span>" for sid, h, c in row)
        out.append(f"<h4>{inner}</h4>" if kind == "h" else f"<li>{inner}</li>" if kind == "li" else f"<p>{inner}</p>")
    html_ = "".join(out)
    html_ = re.sub(r"((?:<li>.*?</li>)+)", r"<ul>\1</ul>", html_)
    return f"<div class='answer'>{html_}</div>"


def draft_html(v, phase):
    """phase 'draft': plain draft. phase 'verify': removed sentences struck, hypotheses labeled."""
    out = []
    for kind, row in v["draft"]:
        parts = []
        for h, st in row:
            if phase == "verify" and st == "removed":
                parts.append(f"<span class='dr strike'>{h}</span>")
            elif phase == "verify" and st == "hypothesis":
                parts.append(f"<span class='dr hyp'><span class='hyptag'>Hypothesis, unverified</span>{h}</span>")
            else:
                parts.append(f"<span class='dr'>{h}</span>")
        out.append(f"<{'h4' if kind == 'h' else 'p'}>{' '.join(parts)}</{'h4' if kind == 'h' else 'p'}>")
    return f"<div class='answer draft'>{''.join(out)}</div>"


def draft_sentences(v):
    return sum(len(row) for _, row in v["draft"])


def draft_partial(v, n):
    out, i = [], 0
    for kind, row in v["draft"]:
        parts = []
        for h, _ in row:
            if i < n:
                parts.append(f"<span class='dr{' new' if i == n - 1 else ''}'>{h}</span>")
            i += 1
        if parts:
            out.append(f"<{'h4' if kind == 'h' else 'p'}>{' '.join(parts)}</{'h4' if kind == 'h' else 'p'}>")
    return f"<div class='answer draft'>{''.join(out)}<span class='caret'></span></div>"


def sources_html(v):
    items = []
    for i, ref in enumerate(v["order"], 1):
        e = v["edges"].get(ref)
        if ref.startswith("pt:"):
            kind = "observed in this patient"
        elif ref.startswith(MODEL_PREFIXES):
            kind = "model output"
        elif ref.startswith("PMID"):
            kind = "PubMed"
        elif e:
            kind = LABEL[edge_role(ref, e)].lower()
        else:
            kind = "mechanism step" if any(s.get("edge_id") == ref for s in v["steps"]) else "citation"
        label = source_label(ref, v["nodes"], v["edges"])
        if not ref.startswith("PMID"):
            label = esc(label.rsplit(" · ", 1)[0])
            step = next((s for s in v["steps"] if s.get("edge_id") == ref), None)
            if step:
                label = esc(step.get("label", ref))
        else:
            m = re.match(r"\[(.+?)\]\((.+?)\)", label)
            label = f"<a href='{m.group(2)}' target='_blank'>{esc(m.group(1))}</a>" if m else esc(label)
        items.append(f"<li id='src-{i}'>{label} <span class='kind'>({kind}, {esc(ref)})</span></li>")
    return "<ol class='sources'>" + "".join(items) + "</ol>" if items else ""


def stage_metrics(v):
    t, calls, by = v["trace"], v["calls"], v["claims"]
    m = {}
    if p := calls.get("get_patient"):
        n = sum(len(p.get(k, [])) for k in ("phenotypes", "proteins_up", "proteins_down"))
        m["patient"] = f"{n} measurements"
    if p := calls.get("predict_patient"):
        m["score"] = f"{p['risk']:.2f}"
    else:
        m["score"] = "not used" if t.get("cancer") == "brca" else "skipped"
    if s := calls.get("retrieve_subgraph"):
        m["retrieve"] = f"{len(s.get('nodes', []))} facts kept"
    if g := calls.get("get_mechanism"):
        n = sum(len(c.get("steps", [])) for c in g.get("chains", []))
        m["lookup"] = f"{n} curated steps"
    elif calls:
        m["lookup"] = f"{len(t.get('tool_calls', []))} tool calls"
    m["draft"] = f"{t.get('verifier', {}).get('n_claims', sum(len(x) for x in by.values()))} claims"
    m["verify"] = f"{len(by['supported'])} backed, {len(by['removed'])} removed"
    return m


def strip_html(v, active, done):
    m = stage_metrics(v)
    out = []
    for i, (key, name) in enumerate(STAGES):
        state = "done" if i < done else ("active" if i == active else "")
        val = m.get(key, "") if i < done else ("running" if i == active else "")
        out.append(f"<div class='stg {state}'><span class='dot'></span><div class='nm'>{name}</div>"
                   f"<div class='mt'>{esc(val)}</div></div>")
    return "<div class='strip'>" + "".join(out) + "</div>"


def prediction_html(pred, shown=None):
    r = pred["risk"] if shown is None else shown
    side = "stage III–IV" if pred["risk"] >= .5 else "stage I–II"
    contrib = "".join(f"<span class='pc'>{esc(x['name'])}</span>" for x in pred.get("top_contributing", [])[:4])
    return (f"<div class='pred'><div class='pl'>Stage score</div><div class='pv'>{r:.2f}</div>"
            f"<div class='pt'><div class='trk'><i style='left:{100 * r:.1f}%'></i></div>"
            f"<div class='ends'><span>Looks like stage I–II</span><span>Looks like stage III–IV</span></div></div>"
            f"<div class='pn'>Leans {side}." + (f" Biggest contributors: {contrib}" if contrib else "")
            + "<br><span>Out-of-fold GNN score, uncalibrated. A ranking against other patients, not a probability, "
              "and not for clinical decisions.</span></div></div>")


def funnel_html(total, cand, kept):
    rows = [(f"{total:,}", "connections in the graph")]
    if cand:
        rows.append((f"{cand:,}", "candidates near this patient"))
    rows.append((f"{kept:,}", "facts the GNN kept for this question"))
    return "<div class='funnel'>" + "".join(f"<div><b>{a}</b>{esc(b)}</div>" for a, b in rows) + "</div>"


def chain_html(g):
    steps = [s for c in g.get("chains", []) for s in c.get("steps", [])][:5]
    if not steps:
        return ""
    parts = []
    for i, s in enumerate(steps):
        n = len(s.get("pmids", []))
        parts.append(f"<span class='ms' style='--d:{.25 * i:.2f}s'>{esc(s.get('label', s['mechanism']))}"
                     f"<small>{n} paper{'s' if n != 1 else ''}</small></span>")
    return "<div class='mchain'>" + "<span class='ar'>→</span>".join(parts) + "</div>"


# ------------------------------------------------------------------ the guide
def narration(v):
    """[(stage_key, text)] for the guide: one short line per stage, only from what this trace's tools returned."""
    t, calls, by = v["trace"], v["calls"], v["claims"]
    out = []
    if p := calls.get("get_patient"):
        ups = [x.get("symbol") or x["name"] for x in p.get("proteins_up", [])][:2]
        out.append(("patient", f"Loading {p['patient_id']}" + (f": {', '.join(ups)} up." if ups else ".")
                    + " No stage or outcome goes in."))
    else:
        out.append(("patient", "Loading the patient's record."))
    if p := calls.get("predict_patient"):
        out.append(("score", f"GNN stage score {p['risk']:.2f}, scored out of fold."))
    else:
        out.append(("score", "No stage score: the stage model is kidney-only." if t.get("cancer") == "brca"
                    else "No stage score for this question."))
    if s := calls.get("retrieve_subgraph"):
        out.append(("retrieve", f"{PRIMEKG_EDGES / 1e6:.2f}M connections down to {len(s.get('nodes', []))} facts."))
    else:
        out.append(("retrieve", "No graph search for this question."))
    if g := calls.get("get_mechanism"):
        n_pm = len({p for c in g.get("chains", []) for st in c.get("steps", []) for p in st.get("pmids", [])})
        out.append(("lookup", f"Curated mechanism pulled, {n_pm} PubMed papers."))
    else:
        out.append(("lookup", f"{len(t.get('tool_calls', []))} graph lookups."))
    out.append(("draft", "The agent drafts. Every sentence must cite a fact."))
    n_s, n_r = len(by["supported"]), len(by["removed"])
    msg = f"{n_s} claims kept, {n_r} cut."
    if by["removed"]:
        msg += f" Cut: “{strip_ids(by['removed'][0]['text'])}”"
    out.append(("verify", msg))
    return out


def outro():
    return "Done. Hover a sentence to see its evidence, or ask me below."


def explain(intent, v, score_note=""):
    """Short, deterministic follow-up answers for the guide, drawn only from the trace."""
    by, calls = v["claims"], v["calls"]
    if intent == "score":
        p = calls.get("predict_patient")
        if not p:
            return "No stage score in this run."
        top = [x["name"] for x in p.get("top_contributing", [])][:2]
        return (f"{p['risk']:.2f}: the GNN's out-of-fold score, stage III–IV vs I–II. A ranking, not a probability."
                + (f" Leans on {', '.join(top)}." if top else "") + (f" {score_note}" if score_note else ""))
    if intent == "removed":
        if not by["removed"]:
            return "Nothing was cut in this run."
        c = by["removed"][0]
        more = f" ({len(by['removed']) - 1} more cut.)" if len(by["removed"]) > 1 else ""
        return f"Cut “{strip_ids(c['text'])}” {why_removed(c)}{more}"
    if intent == "evidence":
        refs = v["order"]
        n_pt = sum(r.startswith("pt:") for r in refs)
        n_pm = sum(r.startswith("PMID") for r in refs)
        n_kg = len(refs) - n_pt - n_pm - sum(r.startswith(MODEL_PREFIXES) for r in refs)
        return f"{n_pt} patient measurements, {n_kg} graph facts, {n_pm} papers. All returned by tools in this run."
    if intent == "drugs":
        drug_names = {n["name"].lower() for n in v["nodes"].values() if n["type"] == "drug"}
        names = sorted({n["name"] for n in v["nodes"].values() if n["type"] == "drug"})[:3]
        return (f"{', '.join(names)}: each linked to this tumor's biology by a graph edge. A reason to test, not proof "
                "of response." if names and drug_names else "No drug in this run.")
    return ""


INTENTS = [("subgroup", r"subgroup|cluster|stratif|cohort|group"),
           ("score", r"score|risk|predict|stage|0\.\d|probab|calibrat"),
           ("removed", r"remov|verif|cut|strike|wrong|hallucin|why.*(claim|delet)"),
           ("drugs", r"drug|target|treat|therap|inhibit|sunitinib|belzutifan|cabozantinib|axitinib|pazopanib"),
           ("evidence", r"evidence|source|cite|citation|where.*(from|come)|trust|backed|graph")]


def intent_of(text):
    t = text.lower()
    return next((k for k, pat in INTENTS if re.search(pat, t)), None)
