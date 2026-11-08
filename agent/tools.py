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
                              driver_split, pred_id, rank_id, triple_line, variant_mask)

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


# ---------------------------------------------------------------- cancer cohorts

def _cohort_paths(cancer):
    """Patient table paths for a cohort. ccRCC uses the contract paths (common.contracts.P) unchanged."""
    if cancer == "ccrcc":
        return {"patients": P.patients, "phenotypes": P.patient_phenotypes, "proteins": P.patient_proteins,
                "mutations": P.patient_mutations, "drugs": P.patient_drugs}
    d = C.CANCERS[cancer]["patients_dir"]
    return {"patients": d / "patients.parquet", "phenotypes": d / "patient_phenotypes.parquet",
            "proteins": d / "patient_proteins.parquet", "mutations": d / "patient_mutations.parquet",
            "drugs": d / "patient_drugs.parquet"}


@lru_cache(maxsize=1)
def _cohort_index():
    """patient_id -> cancer, from each cohort's patients.parquet that exists."""
    out = {}
    for cancer in C.CANCERS:
        f = _cohort_paths(cancer)["patients"]
        if f.exists():
            for pid in pd.read_parquet(f, columns=["patient_id"]).patient_id.astype(str):
                out.setdefault(pid, cancer)
    return out


def cancer_of(patient_id):
    """The cohort a patient belongs to, or None if no cohort table lists them."""
    if not patient_id:
        return None
    try:
        return _cohort_index().get(_pid(patient_id))
    except Exception:
        return None


def _cancer(patient_id):
    return cancer_of(patient_id) or "ccrcc"


@lru_cache(maxsize=4)
def _patient_tables(cancer="ccrcc"):
    f = _cohort_paths(cancer)
    return (pd.read_parquet(_need(f["patients"])), pd.read_parquet(_need(f["phenotypes"])),
            pd.read_parquet(_need(f["proteins"])))


@lru_cache(maxsize=4)
def _patient_extras(cancer="ccrcc"):
    """Somatic mutations and baseline medications (WS2). Optional: empty tables if a kit predates them."""
    cols = ["patient_id", "curie", "name", "source_field", "source_text", "weight"]
    load = lambda p: pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=cols)  # noqa: E731
    f = _cohort_paths(cancer)
    return load(f["mutations"]), load(f["drugs"])


def get_patient(patient_id):
    pid = _pid(patient_id)
    cancer = _cancer(pid)
    pts, phe, pro = _patient_tables(cancer)
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

    out = {
        "patient_id": pid,
        "curie": patient_curie(pid),
        "clinical_summary": str(row.get("clinical_summary", "")),
        "phenotypes": [{"curie": r.curie, "name": phen_name(r), "edge_id": patient_edge_id(pid, "has_phenotype", r.curie)}
                       for r in ph.itertuples()],
        "proteins_up": prot(pr[pr.direction == "up"], "protein_up"),
        "proteins_down": prot(pr[pr.direction == "down"], "protein_down"),
        **_mutations_and_meds(pid, cancer),
    }
    if cancer != "ccrcc":  # ccRCC output stays byte-identical (E3 / demo runs)
        out["cancer"] = cancer
        if "z_ref" in pr and set(pr.z_ref.astype(str)) == {"cohort"}:
            out["protein_reference"] = ("cohort: no matched normal tissue, so protein changes are relative to other "
                                        "tumors in this cohort, and programs most tumors share look average")
    return out


def _mutations_and_meds(pid, cancer="ccrcc"):
    mut, med = _patient_extras(cancer)
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
                    f"not {C.CANCERS[cancer]['short']} treatment"}


@lru_cache(maxsize=1)
def _predictions():
    return pd.read_parquet(_need(P.predictions))


def predict_patient(patient_id):
    pid = _pid(patient_id)
    cancer = _cancer(pid)
    if cancer != "ccrcc":
        return {"error": f"no GNN-A risk model for {C.CANCERS[cancer]['short']}: the outcome head was trained on the "
                         "ccRCC cohort only, so there is no risk estimate for this patient"}
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
    for x in top:
        x["driver"] = "clinical" if (x["type"] == "phenotype" or x["curie"].startswith("HP:")) else "tumor"
    out = {"patient_id": pid, "label": C.PRIMARY_LABEL, "risk": round(float(p.risk), 4), "model": "gnn",
           "fold": int(p.fold), "evidence_id": pred_id(pid), "top_contributing": top}
    out.update(driver_split(top))
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
    cancer = _cancer(pid)
    _, phe, pro = _patient_tables(cancer)
    tumor_only = C.RANK_BASIS == "tumor"
    neigh, w = patient_neighbours(pid, phe.iloc[0:0] if tumor_only else phe, pro)
    if not neigh:
        return {"error": f"no {'protein' if tumor_only else 'phenotype or protein'} neighbours for {patient_id!r}"}
    out = {"patient_id": pid, "node_type": node_type,
           "basis": "tumor_proteins" if tumor_only else "phenotypes+proteins"}
    if node_type == "drug" and C.RANK_DRUGS == "cohort":
        rows, filtered = _rank_drugs_cohort(pid, neigh, w, int(k), cancer)
        out.update(scoring="cohort_relative",
                   filter="drugs with an indication edge" if filtered else "none (this variant has no indication edges)",
                   note="score = how far above the rest of the cohort this drug scores for this patient (SD units); "
                        "model_score is the raw GNN score. A model suggestion, not evidence that the drug works.")
    else:
        df = _gnn_a(C.GNN_A_TAG).rank_for_patient_rel(neigh, node_type, k=int(k), weights=w)
        rows = [{"curie": r.curie, "name": r.name, "score": round(float(r.score), 4)} for r in df.itertuples()]
        out["scoring"] = "model_score"
    out["items"] = [{**r, "rank": i + 1, "evidence_id": rank_id(pid, node_type, r["curie"])} for i, r in enumerate(rows)]
    return out


# ---------------------------------------------------------------- cohort-relative drug ranking

def _standardize(s):
    """z-score one patient's scores across all candidates, so patients on different scales are comparable."""
    import numpy as np
    sd = np.nanstd(s)
    return (s - np.nanmean(s)) / sd if sd > 0 else s * np.nan


def cohort_lift(R, i, own):
    """`own` (standardized scores) minus the mean of the other patients' rows of R (leave-one-out when i is set)."""
    import numpy as np
    others = R if i is None else np.delete(R, i, axis=0)
    return own - np.nanmean(others, axis=0)


def _patient_scores(g, pid, node_type, basis, phe, pro):
    import numpy as np
    neigh, w = patient_neighbours(pid, phe.iloc[0:0] if basis == "tumor" else phe, pro)
    if not neigh:
        return np.full(len(g.gidx_by_type[node_type]), np.nan)
    return np.asarray(g.relation_scores(neigh, node_type, w), dtype=float)


@lru_cache(maxsize=8)
def _cohort_scores(tag, node_type, basis, cancer="ccrcc"):
    """(patient IDs, standardized GNN-A scores: patients x candidates) for ONE cohort, so a patient is only ever
    compared with patients of the same cancer. Built once per process and cohort, a few seconds."""
    import numpy as np
    g = _gnn_a(tag)
    _, phe, pro = _patient_tables(cancer)
    pids = sorted(set(pro.patient_id) | (set() if basis == "tumor" else set(phe.patient_id)))
    return pids, np.vstack([_standardize(_patient_scores(g, p, node_type, basis, phe, pro)) for p in pids])


@lru_cache(maxsize=4)
def _approved_drugs(variant):
    """gidx of drugs with at least one indication edge visible in `variant`, our stand-in for approved drugs.
    Uses the variant mask, so a drug known only through E4's hidden ccRCC edges can't slip back in."""
    flags = list(GRAPH_VARIANTS[variant])
    import pyarrow.parquet as pq
    have = set(pq.read_schema(_need(P.edges)).names)
    if set(flags) - have:
        raise Missing(f"edges.parquet has no {sorted(set(flags) - have)} column(s) for variant {variant!r}; get the newer data kit")
    e = pd.read_parquet(P.edges, columns=["src", "dst", "rel", "src_type", "dst_type", "origin", *flags])
    e = e[variant_mask(e, variant) & (e.rel == "indication")]
    return frozenset(e.src[e.src_type == "drug"].tolist()) | frozenset(e.dst[e.dst_type == "drug"].tolist())


def _rank_drugs_cohort(pid, neigh, w, k, cancer="ccrcc"):
    import numpy as np
    g = _gnn_a(C.GNN_A_TAG)
    pids, R = _cohort_scores(C.GNN_A_TAG, "drug", C.RANK_BASIS, cancer)
    raw = np.asarray(g.relation_scores(neigh, "drug", w), dtype=float)
    lift = cohort_lift(R, pids.index(pid) if pid in pids else None, _standardize(raw))
    cand = np.asarray(g.gidx_by_type["drug"])
    approved = _approved_drugs(C.VARIANT)  # empty on variants that drop all indication edges (bifo_strict, bifo_flow)
    ok = (np.isin(cand, list(approved)) if approved else np.ones(len(cand), bool)) & np.isfinite(lift)
    lift = np.where(ok, lift, -np.inf)
    order = [j for j in np.argsort(-lift, kind="stable")[:k] if ok[j]]
    return [{"curie": str(g.nodes.curie.values[cand[j]]), "name": str(g.nodes.name.values[cand[j]]),
             "score": round(float(lift[j]), 3), "model_score": round(float(raw[j]), 4)} for j in order], bool(approved)


# ---------------------------------------------------------------- drug-target overlap

MUTATION_WEIGHT = 5.0  # a somatically mutated target counts like a strongly up-regulated one (top-50 z is ~2-15)
# PrimeKG's drug_protein mixes therapeutic targets with metabolizing enzymes, transporters and carriers, and
# edges.parquet doesn't keep the distinction (kg.csv's display_relation). So genes in the standard drug-handling
# (ADME) families are not counted as targets: otherwise "Cisplatin -> ABCC3" and "Tamoxifen -> CYP2A6" ranked first.
ADME_PREFIXES = ("CYP", "UGT", "SULT", "ABC", "SLC", "CES", "FMO", "GST", "AKR1", "ADH", "EPHX", "ATP7")
ADME_GENES = {"ALB", "ORM1", "ORM2", "SERPINA6", "SERPINA7", "AOX1", "TPMT", "NAT1", "NAT2", "APOD", "TTR", "SHBG",
              "NQO1", "NQO2", "POR", "MAOA", "MAOB", "COMT", "XDH", "DPYD"}


def is_adme(symbol):
    s = str(symbol).upper()
    return s in ADME_GENES or s.startswith(ADME_PREFIXES)


@lru_cache(maxsize=8)
def _drug_edges(rel, variant):
    """drug -> X edges of one relation visible in `variant` (edge_row, drug gidx, other gidx), one direction only."""
    flags = list(GRAPH_VARIANTS[variant])
    import pyarrow.parquet as pq
    have = set(pq.read_schema(_need(P.edges)).names)
    if set(flags) - have:
        raise Missing(f"edges.parquet has no {sorted(set(flags) - have)} column(s) for variant {variant!r}; get the newer data kit")
    e = pd.read_parquet(P.edges, columns=["edge_row", "src", "dst", "rel", "src_type", "origin", *flags])
    e = e[(e.rel.astype(str) == rel) & (e.src_type.astype(str) == "drug")]
    e = e[variant_mask(e, variant)]
    return e[["edge_row", "src", "dst"]].reset_index(drop=True)


@lru_cache(maxsize=8)
def _cancer_indications(variant, cancer):
    """drug gidx -> [(edge_id, disease name)]: indication edges to diseases whose name matches the cancer."""
    df, _, _ = _nodes()
    e = _drug_edges("indication", variant)
    kw = re.compile("|".join(re.escape(k) for k in C.CANCERS[cancer]["indication_keywords"]), re.I)
    out = defaultdict(list)
    for row, drug, dis in e.itertuples(index=False):
        name = str(df.name.at[dis])
        if kw.search(name):
            out[drug].append((edge_id(row), name, "indication"))
    # DisMech's curated treatment edges (drug -> treats or studied to treat -> disease). These carry approvals that
    # PrimeKG's 2022 indication data predates (alpelisib, PARP inhibitors, elacestrant for breast carcinoma). Only
    # get_drug_targets reads this function, so rank_for_patient's approved-drug filter is unchanged.
    for row, drug, dis in _drug_edges("dm_treats_or_applied_or_studied_to_treat", variant).itertuples(index=False):
        name = str(df.name.at[dis])
        if kw.search(name) and str(df.type.at[dis]) == "disease":
            out[drug].append((edge_id(row), name, "dismech_treats_or_studied"))
    return dict(out)


def get_drug_targets(patient_id, k=15):
    """Approved drugs whose PrimeKG drug_protein targets are among the patient's up-regulated tumor proteins or
    somatically mutated genes. Every link is citable: the drug-target edge (E...) and the patient edge (pt:...)."""
    pid = _pid(patient_id)
    cancer = _cancer(pid)
    pts, _, pro = _patient_tables(cancer)
    if not (pts.patient_id.astype(str) == pid).any():
        return {"error": f"unknown patient_id {patient_id!r}"}
    mut, _ = _patient_extras(cancer)
    df, cur2g, _ = _nodes()

    targets = defaultdict(list)  # gene gidx -> what the patient has at that gene
    for r in pro[(pro.patient_id == pid) & (pro.direction == "up")].itertuples():
        g = cur2g.get(r.curie)
        if g is not None:
            targets[g].append({"basis": "protein_up", "symbol": r.symbol, "curie": r.curie, "z": round(float(r.z), 2),
                               "patient_edge_id": patient_edge_id(pid, "protein_up", r.curie), "w": abs(float(r.z))})
    for cur, gm in mut[mut.patient_id == pid].groupby("curie", sort=False):
        g = cur2g.get(cur)
        if g is not None:
            change = ", ".join(t for t in gm.source_text.astype(str).str.strip() if t and t != "nan")
            targets[g].append({"basis": "somatic_mutation", "symbol": str(gm.name.iloc[0]), "curie": cur,
                               "protein_change": change, "w": MUTATION_WEIGHT,
                               "patient_edge_id": patient_edge_id(pid, "somatic_mutation", cur)})
    if not targets:
        return {"error": f"no up-regulated proteins or mutated genes in the graph for {patient_id!r}"}

    dp_all = _drug_edges("drug_protein", C.VARIANT)
    adme = _adme_gidx()
    dp_all = dp_all[~dp_all.dst.isin(adme)]
    n_total = dp_all.groupby("src").size()  # each drug's therapeutic-target count, for the promiscuity penalty
    dp = dp_all[dp_all.dst.isin([g for g in targets if g not in adme])]
    approved = _approved_drugs(C.VARIANT)
    indic = _cancer_indications(C.VARIANT, cancer)

    drugs = []
    for drug, grp in dp.groupby("src", sort=False):
        hits = []
        for row, _, gene in grp.itertuples(index=False):
            best = max(targets[gene], key=lambda x: x["w"])
            for t in targets[gene]:
                hit = {k2: v for k2, v in t.items() if k2 != "w"}
                hit["drug_target_edge_id"] = edge_id(row)
                hits.append((best["w"], hit))
        hits.sort(key=lambda x: -x[0])
        raw = sum(max(targets[g], key=lambda x: x["w"])["w"] for g in set(grp.dst))
        tot = int(n_total.get(drug, len(set(grp.dst))))
        ind = indic.get(drug, [])
        drugs.append({"curie": str(df.curie.at[drug]), "name": str(df.name.at[drug]),
                      "indicated_for_cancer": bool(ind), "has_indication_edge": drug in approved or bool(ind),
                      "indication": [{"disease": n, "edge_id": e, "source": src} for e, n, src in ind[:2]],
                      "n_patient_targets": len(set(grp.dst)), "n_targets_total": tot,
                      "score": round(raw / tot ** 0.5, 2),  # specific drugs first: 1 hit of 1 target beats 3 of 300
                      "targets": [h for _, h in hits[:5]]})
    drugs.sort(key=lambda d: (not d["indicated_for_cancer"], -d["score"], d["name"]))
    main = [d for d in drugs if d["has_indication_edge"]] if approved else drugs
    # Drugs with no indication edge are mostly lab compounds (crystallography ligands, nucleotide analogues). Keep
    # only those that hit a mutated gene and carry a drug-like (single-word) name: e.g. alpelisib -> PIK3CA, which
    # PrimeKG's 2022 indication data predates.
    extra = [d for d in drugs if approved and not d["has_indication_edge"]
             and any(t["basis"] == "somatic_mutation" for t in d["targets"])
             and re.fullmatch(r"[A-Za-z]{6,}", d["name"])][:5]
    return {"patient_id": pid, "cancer": cancer,
            "basis": "the patient's top up-regulated tumor proteins and somatically mutated genes "
                     "(down-regulated proteins are not used)",
            "filter": "items: drugs with an indication edge (our approval proxy) or a DisMech treatment edge to this "
                      "cancer. other_drugs_on_mutated_genes: drugs with neither that target a mutated gene. "
                      "Drug-handling genes (CYP, UGT, ABC, SLC, ...) are not counted as targets.",
            "n_patient_targets_in_graph": len(targets), "n_drugs": len(main),
            "items": main[: int(k)],
            "other_drugs_on_mutated_genes": extra,
            "note": "drug_protein edges are PrimeKG drug-target links. A shared target is a rationale to investigate, "
                    "not evidence that the drug works for this patient. indicated_for_cancer = an indication edge (PrimeKG) or a "
                    "treats-or-studied-to-treat edge (DisMech; may be a trial, not an approval) to a disease of this "
                    "cancer type exists (see indication[].source); score = summed target weight / sqrt(the drug's target count)."}


@lru_cache(maxsize=1)
def _adme_gidx():
    df, _, _ = _nodes()
    genes = df[df.type.astype(str) == "gene"]
    return frozenset(genes.gidx[genes.name.map(is_adme)].tolist())


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
    "rank_for_patient": rank_for_patient, "get_drug_targets": get_drug_targets, "retrieve_subgraph": retrieve_subgraph, "get_mechanism": get_mechanism,
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
