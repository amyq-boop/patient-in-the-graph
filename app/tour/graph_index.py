"""A read-only index of real knowledge-graph rows for the tour's network views.

Two backends, chosen automatically:
* `full_graph`: data/processed/nodes.parquet + edges.parquet (+ evidence.parquet) when the data kit is installed.
* `executed_retrievals`: every node and edge returned by real (non-mock) `retrieve_subgraph` calls saved in
  cache/demo/ and eval/traces/, plus the committed PrimeKG extracts. Edge IDs (E<row>) and DisMech PMIDs/quotes
  are kept exactly as the tools returned them. PrimeKG extract rows have no E-ID (they predate the merged graph),
  so they are labelled `kg.csv` rows and are never offered to the verifier as citations.

No edge is invented: if a gene has no row in the active backend, the UI says so.
"""
from __future__ import annotations

import csv
import json
import re
import threading
from collections import Counter, defaultdict
from pathlib import Path

from common.contracts import P, ROOT

MOCK_EDGE = re.compile(r"^E9000\d\d$")
EXTRACTS = ROOT / "data" / "primekg_extracts"
TRACE_DIRS = (P.demo_cache, P.traces)
TYPE_MAP = {"gene/protein": "gene", "effect/phenotype": "phenotype"}
PAD7 = {"MONDO", "HPO", "GO", "UBERON"}
PREFIX = {"NCBI": "NCBIGene", "DrugBank": "DRUGBANK", "MONDO": "MONDO", "HPO": "HP", "GO": "GO",
          "REACTOME": "REACT", "UBERON": "UBERON", "CTD": "MESH"}

_lock = threading.Lock()
_index = None


def _kg_curie(src: str, raw: str) -> str | None:
    if not raw or "_" in raw or src not in PREFIX:  # grouped MONDO ids ("13924_12592") are skipped here
        return None
    return f"{PREFIX[src]}:{raw.zfill(7) if src in PAD7 else raw}"


def _is_real_trace(t: dict) -> bool:
    if not isinstance(t, dict):
        return False
    pid = str(t.get("patient_id") or "")
    run = json.dumps(t.get("run_info") or {}).lower()
    return "MOCK" not in pid.upper() and '"mock_mode": "1"' not in run and t.get("mode") != "mock"


class GraphIndex:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.adj: dict[str, list[str]] = defaultdict(list)   # curie -> edge keys
        self.edges: dict[str, dict] = {}
        self.symbol: dict[str, str] = {}
        self.mechanism_chains: list[dict] = []
        self.meta: dict = {}
        self._full = None
        if P.nodes.exists() and P.edges.exists():
            try:
                self._load_full()
                return
            except Exception as exc:  # noqa: BLE001 - fall back to the executed-retrieval index, and say why
                self.meta["full_graph_error"] = f"{type(exc).__name__}: {exc}"
        self._load_executed()

    # ------------------------------------------------------------------ backends
    def _load_full(self):
        import pandas as pd

        nodes = pd.read_parquet(P.nodes, columns=["gidx", "curie", "type", "name", "origin"])
        edges = pd.read_parquet(P.edges, columns=["edge_row", "src", "dst", "rel", "origin", "dm_row"])
        self._full = {"nodes": nodes.set_index("gidx"), "edges": edges,
                      "by_curie": dict(zip(nodes.curie, nodes.gidx)),
                      "ev": pd.read_parquet(P.evidence).set_index("dm_row") if P.evidence.exists() else None}
        genes = nodes[nodes.type == "gene"]
        self.symbol = {str(n).upper(): c for n, c in zip(genes.name, genes.curie)}
        self.meta.update(source="full_graph", n_nodes=int(len(nodes)), n_edges=int(len(edges)),
                         description="Merged PrimeKG + DisMech graph from data/processed (edges.parquet).")
        self._load_mechanisms_from_traces()

    def _load_executed(self):
        files = [f for d in TRACE_DIRS if d.exists() for f in sorted(d.rglob("*.json")) if not f.name.startswith("_")]
        used = 0
        for f in files:
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not _is_real_trace(t):
                continue
            hit = False
            for c in t.get("tool_calls") or []:
                r = c.get("result")
                if not isinstance(r, dict) or r.get("error"):
                    continue
                if c.get("name") == "retrieve_subgraph" and not (r.get("meta") or {}).get("mock"):
                    hit = True
                    for n in r.get("nodes", []):
                        self.nodes.setdefault(n["curie"], {"curie": n["curie"], "name": n.get("name") or n["curie"],
                                                           "type": n.get("type") or "unknown"})
                    for e in r.get("edges", []):
                        eid = str(e.get("edge_id", ""))
                        if not re.fullmatch(r"E\d+", eid) or MOCK_EDGE.match(eid) or eid in self.edges:
                            continue
                        self._add_edge(eid, {"edge_id": eid, "src": e["src_curie"], "dst": e["dst_curie"],
                                             "rel": e.get("rel"), "origin": e.get("origin", "primekg"),
                                             "pmids": e.get("pmids") or [], "quote": e.get("quote"),
                                             "citable": True, "provenance": "merged graph edge (executed retrieval)"})
                elif c.get("name") == "get_mechanism":
                    self._take_mechanism(r)
            used += hit
        n_trace_edges = len(self.edges)
        self._load_extracts()
        for curie, n in self.nodes.items():
            if n["type"] == "gene":
                self.symbol.setdefault(str(n["name"]).upper(), curie)
        self.meta.update(source="executed_retrievals", n_trace_files=used, n_nodes=len(self.nodes),
                         n_edges=len(self.edges), n_merged_graph_edges=n_trace_edges,
                         n_extract_rows=len(self.edges) - n_trace_edges,
                         by_origin=dict(Counter(e["origin"] for e in self.edges.values())),
                         description=("Union of every node and edge returned by real retrieve_subgraph runs "
                                      "(cache/demo, eval/traces) plus committed PrimeKG extract rows. "
                                      "A subset of the 8.56M-edge graph; install data/processed for the full graph."))

    def _load_extracts(self):
        files = ["primekg_ccrcc_direct_edges.csv", "primekg_ccrcc_neighbour_edges.csv", "primekg_sample_1pct.csv"]
        for name in files:
            path = EXTRACTS / name
            if not path.exists():
                continue
            with path.open(newline="", encoding="utf-8") as fh:
                for i, r in enumerate(csv.DictReader(fh), start=2):
                    a, b = _kg_curie(r["x_source"], r["x_id"]), _kg_curie(r["y_source"], r["y_id"])
                    if not a or not b:
                        continue
                    for c, typ, nm in ((a, r["x_type"], r["x_name"]), (b, r["y_type"], r["y_name"])):
                        if typ == "gene/protein":
                            self.symbol.setdefault(nm.upper(), c)
                    if name == "primekg_sample_1pct.csv":
                        continue  # the 1% sample is used for symbol resolution and on-demand lookups only
                    self._add_extract(name, i, r, a, b)
        self._sample = EXTRACTS / "primekg_sample_1pct.csv"

    def _add_extract(self, name, line, r, a, b):
        for c, typ, nm in ((a, r["x_type"], r["x_name"]), (b, r["y_type"], r["y_name"])):
            self.nodes.setdefault(c, {"curie": c, "name": nm, "type": TYPE_MAP.get(typ, typ)})
        key = f"kg:{name.replace('primekg_', '').replace('.csv', '')}:{line}"
        self._add_edge(key, {"edge_id": None, "row_ref": key, "src": a, "dst": b, "rel": r["relation"],
                             "display_relation": r["display_relation"], "origin": "primekg", "pmids": [],
                             "quote": None, "citable": False,
                             "provenance": f"PrimeKG kg.csv row (data/primekg_extracts/{name}, line {line})"})

    def extract_rows_for(self, curies: set[str], limit_per: int = 25) -> int:
        """Pull 1%-sample rows touching these curies into the index (on demand, still real kg.csv rows)."""
        if self._full is not None or not getattr(self, "_sample", None) or not self._sample.exists():
            return 0
        added, per = 0, Counter()
        with self._sample.open(newline="", encoding="utf-8") as fh:
            for i, r in enumerate(csv.DictReader(fh), start=2):
                if r["relation"] in ("anatomy_protein_present", "anatomy_protein_absent", "drug_drug"):
                    continue
                a, b = _kg_curie(r["x_source"], r["x_id"]), _kg_curie(r["y_source"], r["y_id"])
                hit = a in curies and a or b in curies and b
                if not hit or not a or not b or per[hit] >= limit_per:
                    continue
                per[hit] += 1
                before = len(self.edges)
                self._add_extract("primekg_sample_1pct.csv", i, r, a, b)
                added += len(self.edges) - before
        return added

    def _take_mechanism(self, r: dict):
        for ch in r.get("chains") or []:
            steps = [{"label": s.get("label"), "mechanism": s.get("mechanism"), "edge_id": s.get("edge_id"),
                      "pmids": s.get("pmids") or [], "quote": (s.get("quotes") or [None])[0]}
                     for s in ch.get("steps") or []]
            key = (r.get("disease"), tuple(s["label"] for s in steps))
            if steps and key not in {(m["disease"], tuple(s["label"] for s in m["steps"])) for m in self.mechanism_chains}:
                self.mechanism_chains.append({"disease": r.get("disease"), "disease_name": r.get("name"),
                                              "chain_id": ch.get("chain_id"), "steps": steps})

    def _load_mechanisms_from_traces(self):
        for d in TRACE_DIRS:
            for f in sorted(d.glob("*__*.json")) if d.exists() else []:
                try:
                    t = json.loads(f.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for c in t.get("tool_calls") or [] if _is_real_trace(t) else []:
                    if c.get("name") == "get_mechanism" and isinstance(c.get("result"), dict):
                        self._take_mechanism(c["result"])

    def _add_edge(self, key, e):
        self.edges[key] = e
        self.adj[e["src"]].append(key)
        self.adj[e["dst"]].append(key)

    # ------------------------------------------------------------------ queries
    def node(self, curie: str) -> dict | None:
        if self._full is not None:
            g = self._full["by_curie"].get(curie)
            if g is None:
                return None
            r = self._full["nodes"].loc[g]
            return {"curie": curie, "name": str(r["name"]), "type": str(r["type"]), "origin": str(r["origin"])}
        return self.nodes.get(curie)

    def resolve(self, symbol: str) -> str | None:
        return self.symbol.get(str(symbol).upper()) if symbol else None

    def neighbours(self, curie: str, limit: int = 30) -> list[dict]:
        if self._full is not None:
            return self._full_neighbours(curie, limit)
        keys = self.adj.get(curie, [])
        # merged-graph edges (citable IDs) first, DisMech first among those
        keys = sorted(keys, key=lambda k: (not self.edges[k]["citable"], self.edges[k]["origin"] != "dismech", k))
        return [self.edges[k] for k in keys[:limit]]

    def _full_neighbours(self, curie, limit):
        from common.contracts import edge_id
        F = self._full
        g = F["by_curie"].get(curie)
        if g is None:
            return []
        e = F["edges"]
        rows = e[(e.src == g) & ~e.rel.isin(["anatomy_protein_present", "anatomy_protein_absent", "drug_drug"])]
        rows = rows.sort_values(["origin", "edge_row"]).head(limit)  # 'dismech' < 'primekg'
        out = []
        for r in rows.itertuples():
            dst = F["nodes"].loc[r.dst]
            pmids, quote = [], None
            if r.origin == "dismech" and F["ev"] is not None:
                try:
                    from agent.tools import _evidence_for
                    pmids, quotes = _evidence_for(int(r.dm_row), F["ev"])
                    quote = quotes[0] if quotes else None
                except Exception:  # noqa: BLE001
                    pass
            out.append({"edge_id": edge_id(r.edge_row), "src": curie, "dst": str(dst["curie"]), "rel": r.rel,
                        "origin": r.origin, "pmids": pmids, "quote": quote, "citable": True,
                        "provenance": "merged graph edge (edges.parquet)",
                        "_dst_node": {"curie": str(dst["curie"]), "name": str(dst["name"]), "type": str(dst["type"])}})
        return out

    def describe(self) -> dict:
        return dict(self.meta)

    # ------------------------------------------------------------------ the stage graph for chapters 1-4
    def stage_graph(self, focus: list[str], cap: int = 720, per_focus: int = 10) -> dict:
        """A connected, real neighbourhood: every DisMech edge in the index, the focus genes' edges, and the
        merged-graph edges that connect them. Selection is by rule (no scoring, nothing synthesized)."""
        chosen_e: dict[str, dict] = {}
        nodes: dict[str, dict] = {}

        def take(e):
            for c in (e["src"], e["dst"]):
                if c not in nodes:
                    n = self.node(c) or e.get("_dst_node") or {"curie": c, "name": c, "type": "unknown"}
                    nodes[c] = n
            chosen_e[e.get("edge_id") or e["row_ref"]] = e

        if self._full is None:
            for k, e in self.edges.items():
                if e["origin"] == "dismech":
                    take(e)
        for c in focus:  # focus entities are always present, even when the index has no edge for them
            n = self.node(c)
            if n and c not in nodes:
                nodes[c] = n
        for c in focus:
            for e in self.neighbours(c, per_focus):
                if len(nodes) >= cap:
                    break
                take(e)
        if self._full is None and len(nodes) < cap:  # connectors: index edges between already-chosen nodes
            for k, e in self.edges.items():
                key = e.get("edge_id") or e["row_ref"]
                if key not in chosen_e and e["src"] in nodes and e["dst"] in nodes:
                    chosen_e[key] = e
            # breadth: one-hop growth from the best-connected chosen nodes until the cap
            deg = Counter()
            for e in chosen_e.values():
                deg[e["src"]] += 1
                deg[e["dst"]] += 1
            for c, _ in deg.most_common(40):
                for k in self.adj.get(c, [])[:12]:
                    if len(nodes) >= cap:
                        break
                    e = self.edges[k]
                    if e["citable"]:
                        take(e)
        return {"nodes": list(nodes.values()),
                "edges": [{k: v for k, v in e.items() if not k.startswith("_")} for e in chosen_e.values()]}


def get_index() -> GraphIndex:
    global _index
    with _lock:
        if _index is None:
            _index = GraphIndex()
        return _index
