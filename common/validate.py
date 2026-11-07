"""Check produced files against CONTRACTS.md.

    python -m common.validate all
    python -m common.validate graph patients gnn e1 e2 e3 traces questions

Missing files are reported as SKIP, so this works at any stage. WARN lines are worth a look but don't fail.
"""
from __future__ import annotations

import json
import sys

import pandas as pd

from common.contracts import (
    EDGE_FLAGS, EDGE_ORIGINS, LABEL_COLUMNS, LEAKY_COLUMN_RE, LEAKY_PHENOTYPE_TERMS, NODE_TYPES, P,
)

FAIL: list[str] = []


def _ok(m): print(f"  OK    {m}")
def _skip(m): print(f"  SKIP  {m}")
def _warn(m): print(f"  WARN  {m}")
def _fail(m):
    print(f"  FAIL  {m}")
    FAIL.append(m)


def _rel(path):
    try:
        return path.relative_to(P.processed.parents[1])
    except ValueError:
        return path


def _read(path, name, columns=None):
    if not path.exists():
        _skip(f"{name}: {_rel(path)} not found")
        return None
    if path.suffix == ".parquet":
        return pd.read_parquet(path, columns=columns)
    return pd.read_csv(path)


def _cols(df, name, required):
    missing = [c for c in required if c not in df.columns]
    if missing:
        _fail(f"{name}: missing columns {missing}")
        return False
    _ok(f"{name}: columns present ({len(df):,} rows)")
    return True


# ------------------------------------------------------------------ graph (WS1 outputs)
def check_graph():
    print("graph")
    nodes = _read(P.nodes, "nodes")
    if nodes is not None and _cols(nodes, "nodes", ["gidx", "curie", "type", "lidx", "name", "origin", "aliases"]):
        if list(nodes.gidx) != list(range(len(nodes))):
            _fail("nodes: gidx is not 0..N-1 in row order")
        if nodes.curie.duplicated().any():
            _fail(f"nodes: {int(nodes.curie.duplicated().sum())} duplicate curies")
        extra = set(nodes.type) - set(NODE_TYPES)
        if extra:
            _fail(f"nodes: node types not in contract {sorted(extra)} (add them to NODE_TYPES if intended)")
        for t, g in nodes.groupby("type"):
            if sorted(g.lidx) != list(range(len(g))):
                _fail(f"nodes: lidx for {t} is not 0..{len(g) - 1}")
    edges = _read(P.edges, "edges", columns=None)
    if edges is not None and _cols(edges, "edges", ["edge_row", "src", "dst", "src_type", "rel", "dst_type",
                                                   "origin", "dm_row", *EDGE_FLAGS]):
        if list(edges.edge_row[:1000]) != list(range(min(1000, len(edges)))):
            _fail("edges: edge_row must equal the row number")
        bad = set(edges.origin.astype(str)) - set(EDGE_ORIGINS)
        if bad:
            _fail(f"edges: unknown origin {sorted(bad)}")
        if nodes is not None:
            n = len(nodes)
            if (edges.src.max() >= n) or (edges.dst.max() >= n):
                _fail("edges: src/dst gidx out of range")
            else:
                nt = nodes.type.values
                wrong = (nt[edges.src.values] != edges.src_type.astype(str).values).sum()
                if wrong:
                    _fail(f"edges: {wrong:,} rows whose src_type disagrees with nodes.type")
        dm = edges.origin.astype(str) == "dismech"
        if (edges.dm_row[dm] < 0).any():
            _fail("edges: dismech edges without dm_row")
        print("        " + ", ".join(f"{f}={int(edges[f].sum()):,}" for f in EDGE_FLAGS))
    ev = _read(P.evidence, "evidence")
    if ev is not None:
        _cols(ev, "evidence", ["dm_row"])
    if P.e1_split.exists():
        s = json.loads(P.e1_split.read_text())
        missing = {"train", "test"} - set(s)
        if missing:
            _fail(f"e1_split.json: missing {missing}")
        else:
            first = (s["test"] or [{}])[0]
            need = {"disease", "name", "targets_gene", "targets_go", "seeds_phenotype"} - set(first)
            (_fail if need else _ok)(f"e1_split.json: {len(s['train'])} train / {len(s['test'])} test"
                                     + (f", entries missing {need}" if need else ""))
    else:
        _skip("e1_split.json not found")


# ------------------------------------------------------------------ patients (WS2)
def check_patients():
    print("patients")
    known = None
    if P.nodes.exists():
        nodes = pd.read_parquet(P.nodes, columns=["curie", "aliases", "type"])
        known = set(nodes.curie) | {a for al in nodes.aliases.fillna("") for a in al.split("|") if a}
    pts = _read(P.patients, "patients")
    if pts is not None and _cols(pts, "patients", ["patient_id", "curie", "clinical_summary"]):
        leaky = [c for c in pts.columns if LEAKY_COLUMN_RE.search(c)]
        if leaky:
            _fail(f"patients: label-like columns must not be here: {leaky}")
    ph = _read(P.patient_phenotypes, "patient_phenotypes")
    if ph is not None and _cols(ph, "patient_phenotypes", ["patient_id", "curie", "source_field", "source_text"]):
        bad_fields = sorted({s for s in ph.source_field.dropna() if LEAKY_COLUMN_RE.search(s)})
        if bad_fields:
            _fail(f"patient_phenotypes: built from label fields {bad_fields}")
        txt = ph.source_text.fillna("").str.lower()
        hits = sorted({t for t in txt if any(term in t for term in LEAKY_PHENOTYPE_TERMS)})
        if hits:
            _fail(f"patient_phenotypes: leaky terms present {hits[:5]}")
        if known is not None:
            miss = ~ph.curie.isin(known)
            if miss.any():
                _fail(f"patient_phenotypes: {int(miss.sum())} curies not in nodes.parquet, e.g. {ph.curie[miss].iloc[0]}")
        n = ph.groupby("patient_id").size()
        n_pts = len(pts) if pts is not None else n.size
        print(f"        phenotypes/patient: median {n.median():.0f}, max {n.max()}, {n_pts - n.size} patients with none "
              f"(expected: median ~3, ~15 with none)")
    pr = _read(P.patient_proteins, "patient_proteins")
    if pr is not None and _cols(pr, "patient_proteins", ["patient_id", "curie", "symbol", "z", "direction", "rank", "z_ref"]):
        if known is not None:
            miss = ~pr.curie.isin(known)
            if miss.any():
                _warn(f"patient_proteins: {int(miss.sum())} curies not in the graph (GnnA will skip them)")
        n = pr.groupby(["patient_id", "direction"]).size()
        if (n != 50).any():
            _fail(f"patient_proteins: {int((n != 50).sum())} patient/direction groups don't have 50 rows")
        if ((pr.direction == "up") != (pr.z > 0)).any():
            _fail("patient_proteins: direction disagrees with the sign of z")
    lab = _read(P.labels, "labels")
    if lab is not None:
        _cols(lab, "labels", ["patient_id", *LABEL_COLUMNS])
    folds = _read(P.folds, "folds")
    if folds is not None and _cols(folds, "folds", ["patient_id", "fold"]):
        if set(folds.fold) - set(range(5)):
            _fail("folds: fold must be 0-4")
        if pts is not None and set(folds.patient_id) != set(pts.patient_id):
            _fail("folds: patient set differs from patients.parquet")


# ------------------------------------------------------------------ GNN outputs
def check_gnn():
    print("gnn")
    for tag in ("main", "no_dismech"):
        (_ok if P.emb(tag).exists() else _skip)(f"emb_{tag}.pt {'present' if P.emb(tag).exists() else 'not found'}")
    pred = _read(P.predictions, "predictions")
    if pred is not None and _cols(pred, "predictions", ["patient_id", "model", "fold", "risk"]):
        if not pred.risk.between(0, 1).all():
            _fail("predictions: risk must be in [0, 1]")
        if P.folds.exists():
            f = pd.read_parquet(P.folds).set_index("patient_id").fold
            wrong = pred.fold.values != f.reindex(pred.patient_id).values
            if wrong.any():
                _fail(f"predictions: {int(wrong.sum())} rows whose fold != the patient's test fold (must be out-of-fold)")


def check_e1():
    print("e1")
    r = _read(P.results / "e1.csv", "e1.csv")
    if r is not None:
        _cols(r, "e1.csv", ["disease", "kind", "method", "n_targets", "recall50", "recall500", "mrr"])


def check_e2():
    print("e2")
    r = _read(P.results / "e2.csv", "e2.csv")
    if r is not None:
        _cols(r, "e2.csv", ["model", "fold", "auroc"])


def check_e3():
    print("e3")
    r = _read(P.results / "e3.csv", "e3.csv")
    if r is not None:
        _cols(r, "e3.csv", ["mode", "qid", "n_claims", "n_supported", "n_hypothesis", "n_removed"])


# ------------------------------------------------------------------ traces / questions
TRACE_KEYS = {"contract_version", "run_id", "qid", "mode", "patient_id", "question", "model",
              "tool_calls", "evidence", "draft", "claims", "answer", "verifier"}


def check_trace(trace: dict, name: str):
    missing = TRACE_KEYS - set(trace)
    if missing:
        _fail(f"{name}: missing keys {sorted(missing)}")
        return
    ev_e = set(trace["evidence"].get("edge_ids", []))
    ev_p = set(trace["evidence"].get("pmids", []))
    for c in trace["claims"]:
        sup = c.get("support", {})
        grounded = bool(set(sup.get("edge_ids", [])) & ev_e or set(sup.get("pmids", [])) & ev_p)
        if c.get("status") == "supported" and not grounded:
            _fail(f"{name}: claim {c.get('claim_id')} marked supported but cites nothing in evidence")


def check_traces():
    print("traces")
    files = list(P.traces.rglob("*.json")) + list(P.demo_cache.glob("*.json"))
    if not files:
        _skip("no traces yet")
    before = len(FAIL)
    for f in files:
        check_trace(json.loads(f.read_text()), str(_rel(f)))
    if files and len(FAIL) == before:
        _ok(f"{len(files)} traces")


def check_questions():
    print("questions")
    if not P.questions.exists():
        _skip("eval/questions.jsonl not found")
        return
    rows = [json.loads(l) for l in P.questions.read_text().splitlines() if l.strip()]
    for q in rows:
        missing = {"qid", "kind", "patient_id", "disease", "question", "expected"} - set(q)
        if missing:
            _fail(f"questions {q.get('qid')}: missing {sorted(missing)}")
    kinds = pd.Series([q.get("kind") for q in rows]).value_counts().to_dict()
    _ok(f"{len(rows)} questions {kinds} (target 10 patient + 10 disease)")


CHECKS = {"graph": check_graph, "patients": check_patients, "gnn": check_gnn, "e1": check_e1,
          "e2": check_e2, "e3": check_e3, "traces": check_traces, "questions": check_questions}

if __name__ == "__main__":
    which = sys.argv[1:] or ["all"]
    for name in (CHECKS if "all" in which else which):
        CHECKS[name]()
    print(f"\n{'FAILED: ' + str(len(FAIL)) + ' problem(s)' if FAIL else 'All present files match the contract.'}")
    sys.exit(1 if FAIL else 0)
