"""Real agent tools (CONTRACTS.md §7), backed by the WS1 / WS2 / GNN files.

    from agent.tools import TOOLS, dispatch
    dispatch("get_patient", {"patient_id": "C3L-00004"})

BIOHACK_MOCK controls the backend:
    1     every call goes to agent/mock_tools.py
    0     real files only; a missing file comes back as {"error": ...}
    auto  (default) real where the backing file exists, otherwise the mock,
          tagged "_fallback": "<why>" so traces show it
Tools never raise: failures come back as {"error": "<message>"}.
"""
import hashlib
import json
import re
import time
from collections import defaultdict
from functools import lru_cache

import pandas as pd
import requests

from agent import config as C
from agent import mock_tools
from agent.schemas import TOOLS  # noqa: F401  (re-exported)
from common.contracts import (GRAPH_VARIANTS, P, contrib_id, edge_id, patient_curie, patient_edge_id, patient_neighbours,
                              pred_id, rank_id, triple_line, variant_mask)

NAMERES = "https://name-resolution-sri.renci.org/lookup"
NODENORM = "https://nodenormalization-sri.renci.org/get_normalized_nodes"
PMID_RE = re.compile(r"PMID:\s*(\d+)", re.I)


class Missing(Exception):
    """A backing file or module isn't there yet (eligible for mock fallback)."""


def _need(path):
    if not path.exists():
        try:
            shown = path.relative_to(C.ROOT)
        except ValueError:
            shown = path
        raise Missing(f"{shown} not found")
    return path


def _pid(x):
    x = str(x).strip()
    return x.split(":", 1)[1] if x.upper().startswith("PATIENT:") else x


# ---------------------------------------------------------------- nodes (WS1)

@lru_cache(maxsize=1)
def _nodes():
    """(df indexed by gidx, curie/alias -> gidx, lowercase name/alt name -> [curie])."""
    df = pd.read_parquet(_need(P.nodes))
    df = df.set_index("gidx", drop=False).sort_index()
    empty = pd.Series("", index=df.index)
    cur2g = dict(zip(df.curie, df.gidx))
    for g, al in zip(df.gidx, df.get("aliases", empty).fillna("")):
        for a in filter(None, str(al).split("|")):
            cur2g.setdefault(a, g)
    by_name = defaultdict(list)
    for c, n, alt in zip(df.curie, df.name.astype(str), df.get("alt_names", empty).fillna("")):
        by_name[n.lower()].append(c)
        for a in filter(None, str(alt).split("|")):
            by_name[a.lower()].append(c)
    return df, cur2g, by_name


def _canon(curie):
    """Canonical CURIE for a CURIE or alias, or None."""
    df, cur2g, _ = _nodes()
    g = cur2g.get(curie)
    return None if g is None else df.curie.at[g]


def _info(curie):
    """(name, type) for a CURIE; never raises."""
    try:
        df, cur2g, _ = _nodes()
        g = cur2g.get(curie)
        if g is not None:
            return str(df.name.at[g]), str(df.type.at[g])
    except Missing:
        pass
    return curie, ("patient" if str(curie).startswith("PATIENT:") else "unknown")


def _ref(curie, **kw):
    n, t = _info(curie)
    return {"curie": curie, "name": n, "type": t, **kw}


# ---------------------------------------------------------------- cached HTTP

def _cached(kind, key, fetch):
    d = C.CACHE / kind
    d.mkdir(parents=True, exist_ok=True)
    f = d / (hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:24] + ".json")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    val = fetch()
    f.write_text(json.dumps(val), encoding="utf-8")
    return val


def _nameres(text, limit=10):
    return _cached("nameres", {"s": text, "l": limit},
                   lambda: requests.get(NAMERES, params={"string": text, "limit": limit}, timeout=20).json())


def _nodenorm(curies):
    if not curies:
        return {}
    return _cached("nodenorm", sorted(curies), lambda: requests.post(
        NODENORM, json={"curies": sorted(curies), "conflate": True, "drug_chemical_conflate": True},
        timeout=30).json())


# ---------------------------------------------------------------- tools

def resolve_terms(texts):
    _, _, by_name = _nodes()
    out = []
    for t in texts:
        matches, seen = [], set()

        def add(c, score):
            if c and c not in seen:
                seen.add(c)
                matches.append(_ref(c, score=round(float(score), 3)))

        q = str(t).strip()
        add(_canon(q), 1.0)                       # already a CURIE / alias
        for c in by_name.get(q.lower(), []):      # exact name or alt name; works offline
            add(c, 1.0)
        r = {"text": t}
        try:
            hits = _nameres(q)
            hits = hits if isinstance(hits, list) else []
            norm = _nodenorm([h["curie"] for h in hits if "curie" in h])
            top = max([h.get("score", 1.0) for h in hits] or [1.0]) or 1.0
            for h in hits:
                eq = (norm.get(h["curie"]) or {}).get("equivalent_identifiers", [])
                cands = [h["curie"]] + [e["identifier"] for e in eq]
                add(next((c for c in map(_canon, cands) if c), None), 0.95 * h.get("score", top) / top)
        except Exception as e:
            r["warning"] = f"name resolution unavailable ({type(e).__name__}); local matches only"
        r["matches"] = matches[:5]
        out.append(r)
    return {"results": out}


@lru_cache(maxsize=1)
def _patient_tables():
    return (pd.read_parquet(_need(P.patients)), pd.read_parquet(_need(P.patient_phenotypes)),
            pd.read_parquet(_need(P.patient_proteins)))


@lru_cache(maxsize=1)
def _patient_extras():
    """Somatic mutations and baseline medications (WS2). Optional: empty tables if a kit predates them."""
    cols = ["patient_id", "curie", "name", "source_field", "source_text", "weight"]
    load = lambda p: pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=cols)  # noqa: E731
    return load(P.patient_mutations), load(P.patient_drugs)


def get_patient(patient_id):
    pid = _pid(patient_id)
    pts, phe, pro = _patient_tables()
    row = pts[pts.patient_id == pid]
    if row.empty:
        return {"error": f"unknown patient_id {patient_id!r}"}
    row = row.iloc[0]
    ph = phe[phe.patient_id == pid]
    pr = pro[pro.patient_id == pid].sort_values("rank")

    def prot(df, rel):
        return [{"curie": r.curie, "symbol": r.symbol, "z": round(float(r.z), 3),
                 "edge_id": patient_edge_id(pid, rel, r.curie)} for r in df.itertuples()]

    def phen_name(r):
        n = _info(r.curie)[0]
        return n if n != r.curie else str(getattr(r, "source_text", r.curie))

    return {
        "patient_id": pid,
        "curie": patient_curie(pid),
        "clinical_summary": str(row.get("clinical_summary", "")),
        "phenotypes": [{"curie": r.curie, "name": phen_name(r), "edge_id": patient_edge_id(pid, "has_phenotype", r.curie)}
                       for r in ph.itertuples()],
        "proteins_up": prot(pr[pr.direction == "up"], "protein_up"),
        "proteins_down": prot(pr[pr.direction == "down"], "protein_down"),
        **_mutations_and_meds(pid),
    }


def _mutations_and_meds(pid):
    mut, med = _patient_extras()
    mut, med = mut[mut.patient_id == pid], med[med.patient_id == pid]
    mutations = [{"curie": c, "symbol": str(g.name.iloc[0]), "type": "somatic",
                  "protein_change": ", ".join(t for t in g.source_text.astype(str).str.strip() if t and t != "nan"),
                  "edge_id": patient_edge_id(pid, "somatic_mutation", c)}
                 for c, g in mut.groupby("curie", sort=False)]
    medications = [{"curie": c, "name": str(g.name.iloc[0]), "source_text": str(g.source_text.iloc[0]),
                    "edge_id": patient_edge_id(pid, "takes_medication", c)}
                   for c, g in med.groupby("curie", sort=False)]
    return {"mutations": mutations, "medications": medications,
            "note": "mutations are somatic (tumor) calls, not germline status; medications are baseline, "
                    "not ccRCC treatment"}


@lru_cache(maxsize=1)
def _predictions():
    return pd.read_parquet(_need(P.predictions))


def predict_patient(patient_id):
    pid = _pid(patient_id)
    pred = _predictions()
    p = pred[(pred.patient_id == pid) & (pred.model == "gnn")]
    if p.empty:
        return {"error": f"no gnn prediction for {patient_id!r}"}
    p = p.iloc[0]
    top = []
    if C.CONTRIBUTIONS.exists():
        c = pd.read_parquet(C.CONTRIBUTIONS)
        c = c[c.patient_id == pid].sort_values("rank").head(10)
        top = [_ref(r.curie, score=round(float(r.score), 4), evidence_id=contrib_id(pid, r.curie)) for r in c.itertuples()]
    out = {"patient_id": pid, "label": C.PRIMARY_LABEL, "risk": round(float(p.risk), 4), "model": "gnn",
           "fold": int(p.fold), "evidence_id": pred_id(pid), "top_contributing": top}
    if not top:
        out["note"] = "no per-patient contributions file yet"
    return out


@lru_cache(maxsize=2)
def _gnn_a(tag):
    try:
        from models.gnn_a_api import GnnA
    except ImportError as e:
        raise Missing(f"models.gnn_a_api not importable ({e})")
    _need(P.emb(tag))
    return GnnA(str(P.processed), tag=tag)


def rank_for_patient(patient_id, node_type, k=10):
    pid = _pid(patient_id)
    if node_type not in ("gene", "pathway", "drug"):
        return {"error": f"node_type must be gene, pathway or drug; got {node_type!r}"}
    _, phe, pro = _patient_tables()
    tumor_only = C.RANK_BASIS == "tumor"
    neigh, w = patient_neighbours(pid, phe.iloc[0:0] if tumor_only else phe, pro)
    if not neigh:
        return {"error": f"no {'protein' if tumor_only else 'phenotype or protein'} neighbours for {patient_id!r}"}
    df = _gnn_a(C.GNN_A_TAG).rank_for_patient_rel(neigh, node_type, k=int(k), weights=w)
    return {"patient_id": pid, "node_type": node_type,
            "basis": "tumor_proteins" if tumor_only else "phenotypes+proteins",
            "items": [{"curie": r.curie, "name": r.name, "score": round(float(r.score), 4), "rank": i + 1,
                       "evidence_id": rank_id(pid, node_type, r.curie)}
                      for i, r in enumerate(df.itertuples())]}


def _to_dict(res):
    if isinstance(res, dict):
        return res
    for m in ("to_dict", "model_dump", "dict"):
        if hasattr(res, m):
            return getattr(res, m)()
    import dataclasses
    if dataclasses.is_dataclass(res):
        return dataclasses.asdict(res)
    raise TypeError(f"retrieve_subgraph returned {type(res).__name__}")


def retrieve_subgraph(question, seeds, budget=C.DEFAULT_BUDGET, retriever="gnn_pcst", variant=None):
    try:
        import models.retriever as _rmod  # GNN-B (WS4)
        _rs = _rmod.retrieve_subgraph
    except ImportError as e:
        raise Missing(f"models/retriever.py not importable ({e})")
    if C.BUDGET_MODE == "auto" and getattr(_rmod, "ADAPTIVE_BUDGET", False):
        budget = None  # the retriever sizes the subgraph itself (its own ceiling applies)
    import inspect
    extra = {"anchor": C.ANCHOR_EDGES} if "anchor" in inspect.signature(_rs).parameters else {}
    res = _to_dict(_rs(question=question, seeds=list(seeds), budget=None if budget is None else int(budget),
                       retriever=retriever, **extra,
                       variant=variant or C.VARIANT, target_types=C.TARGET_TYPES))
    res.setdefault("edge_ids", [e["edge_id"] for e in res.get("edges", [])])
    if not res.get("triples_text"):
        lines = []
        for e in res.get("edges", []):
            sn, st = _info(e["src_curie"])
            dn, dt = _info(e["dst_curie"])
            lines.append(triple_line(e["edge_id"], sn, st, e["rel"], dn, dt))
        res["triples_text"] = "\n".join(lines)
    return res


# ---------------------------------------------------------------- get_mechanism from edges + evidence

@lru_cache(maxsize=4)
def _dm_tables(variant="full"):
    """DisMech edges visible in `variant` (+ evidence). Under e1/e4 the hidden edges must not reach the agent,
    or get_mechanism would hand it the answer key that retrieve_subgraph is hiding."""
    flags = list(GRAPH_VARIANTS[variant])  # only this variant's flags: newer variants (bifo_*) may not be in every kit
    import pyarrow.parquet as pq
    have = set(pq.read_schema(_need(P.edges)).names)
    if set(flags) - have:
        raise Missing(f"edges.parquet has no {sorted(set(flags) - have)} column(s) for variant {variant!r}; get the newer data kit")
    e = pd.read_parquet(P.edges, columns=["edge_row", "src", "dst", "src_type", "rel", "dst_type", "origin", "dm_row", *flags])
    e = e[variant_mask(e, variant) & (e.origin == "dismech")].drop(columns=flags)
    ev = pd.read_parquet(_need(P.evidence)).set_index("dm_row")
    return e, ev


def _evidence_for(dm_row, ev):
    if dm_row is None or dm_row < 0 or dm_row not in ev.index:
        return [], []
    row = ev.loc[dm_row]
    vals = [v for v in row.values if isinstance(v, str) and v]
    pmids = list(dict.fromkeys(f"PMID:{m}" for v in vals for m in PMID_RE.findall(v)))
    quotes = [row[c] for c in ev.columns if re.search(r"support|quote|snippet|text", str(c), re.I)
              and isinstance(row[c], str) and row[c].strip()]
    return pmids, quotes[:2]


def _mechanism_from_graph(disease_curie, variant="full"):
    df, cur2g, _ = _nodes()
    g = cur2g.get(disease_curie)
    if g is None:
        return {"error": f"{disease_curie!r} not in nodes.parquet"}
    e, ev = _dm_tables(variant)
    fwd = e[~e.rel.str.endswith("_rev")]
    touch = fwd[(fwd.src == g) | (fwd.dst == g)]
    mech = (set(touch.loc[touch.src_type == "mechanism", "src"]) | set(touch.loc[touch.dst_type == "mechanism", "dst"]))
    if not mech:
        return {"error": f"no dismech mechanism nodes linked to {disease_curie}"}
    mm = fwd[(fwd.src_type == "mechanism") & (fwd.dst_type == "mechanism")]
    while True:  # mechanisms reachable through mechanism -> mechanism edges belong to the disease too
        grow = (set(mm.loc[mm.src.isin(mech), "dst"]) | set(mm.loc[mm.dst.isin(mech), "src"])) - mech
        if not grow:
            break
        mech |= grow
    link = {}  # mechanism gidx -> the edge tying it to the disease
    for r in touch.itertuples():
        link.setdefault(r.src if r.src in mech else r.dst, r)
    causal = fwd[fwd.src.isin(mech) & fwd.dst.isin(mech)]
    nxt, has_in = defaultdict(list), set()
    for r in causal.itertuples():
        nxt[r.src].append(r)
        has_in.add(r.dst)

    def step(m, via):
        pmids, quotes = _evidence_for(int(via.dm_row), ev) if via is not None else ([], [])
        return {"mechanism": df.curie.at[m], "label": str(df.name.at[m]),
                "edge_id": edge_id(via.edge_row) if via is not None else None, "pmids": pmids, "quotes": quotes}

    chains, used = [], set()
    for root in sorted(m for m in mech if m not in has_in) or sorted(mech)[:1]:
        steps, cur, via = [], root, link.get(root)
        while cur is not None and cur not in used:
            used.add(cur)
            steps.append(step(cur, via))
            nx = [r for r in nxt.get(cur, []) if r.dst not in used]
            cur, via = (nx[0].dst, nx[0]) if nx else (None, None)
        chains.append({"chain_id": f"chain-{len(chains) + 1}", "steps": steps})
    for m in sorted(mech - used):  # whatever is left (branches, cycles) as one-step chains
        chains.append({"chain_id": f"chain-{len(chains) + 1}", "steps": [step(m, link.get(m))]})

    def of_type(t):
        return sorted({df.curie.at[x] for r in touch.itertuples() for x in (r.src, r.dst) if x != g and df.type.at[x] == t})

    drug_evidence, seen = [], set()  # curated drug <-> disease edges, citable by edge ID or PMID
    for r in touch.itertuples():
        d = r.dst if r.src == g else r.src
        if df.type.at[d] != "drug" or d in seen:
            continue
        seen.add(d)
        pmids, quotes = _evidence_for(int(r.dm_row), ev)
        drug_evidence.append({"curie": df.curie.at[d], "name": str(df.name.at[d]), "relation": r.rel,
                              "edge_id": edge_id(r.edge_row), "pmids": pmids, "quotes": quotes[:1]})

    return {"disease": df.curie.at[g], "name": str(df.name.at[g]), "chains": chains,
            "genes": of_type("gene"), "drugs": of_type("drug"), "drug_evidence": drug_evidence}


@lru_cache(maxsize=1)
def _mech_file():
    out = {}
    if C.MECHANISMS.exists():
        for line in C.MECHANISMS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                m = json.loads(line)
                out[m["disease"]] = m
    return out


def get_mechanism(disease_curie):
    variant = C.VARIANT
    pre = _mech_file() if variant == "full" else {}  # mechanisms.jsonl ignores masks: only safe on the full graph
    if pre:
        m = pre.get(disease_curie) or pre.get(_canon(disease_curie) or "")
        return m or {"error": f"no dismech mechanism for {disease_curie!r}"}
    return _mechanism_from_graph(disease_curie, variant)


FUNCS = {
    "resolve_terms": resolve_terms, "get_patient": get_patient, "predict_patient": predict_patient,
    "rank_for_patient": rank_for_patient, "retrieve_subgraph": retrieve_subgraph, "get_mechanism": get_mechanism,
}


def dispatch(name, args):
    """Run a tool by name. Never raises (CONTRACTS.md §0.2)."""
    args = dict(args or {})
    if name == "retrieve_subgraph":  # the graph variant is a run setting, never the LLM's choice
        args["variant"] = C.VARIANT
        args.pop("retriever", None)
    if C.MOCK_MODE in ("1", "true", "yes"):
        return mock_tools.dispatch(name, args)
    f = FUNCS.get(name)
    if f is None:
        return {"error": f"unknown tool {name!r}"}
    try:
        return f(**args)
    except Missing as e:
        if C.MOCK_MODE == "auto":
            res = mock_tools.dispatch(name, args)
            if isinstance(res, dict):
                res["_fallback"] = str(e)
            return res
        return {"error": str(e)}
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"{name} failed: {type(e).__name__}: {e}"}


def timed_dispatch(name, args):
    t = time.perf_counter()
    res = dispatch(name, args)
    return res, int((time.perf_counter() - t) * 1000)
