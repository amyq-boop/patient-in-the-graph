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
import os
import re
import threading
import time
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

HUB_RELS = ("anatomy_protein_present", "anatomy_protein_absent", "drug_drug")
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
        self._load_executed()  # always: fast, real, and it seeds the opening network
        want = os.environ.get("TOUR_GRAPH", "auto").lower() != "executed"
        if want and P.nodes.exists() and P.edges.exists():
            try:
                self._load_full()
            except Exception as exc:  # noqa: BLE001 - keep the executed-retrieval index, and say why
                self._full = None
                self.meta["full_graph_error"] = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------ backends
    def _load_full(self):
        """Nodes + edges in compact form (categorical strings, numpy index sorted by src). DisMech evidence text is
        large, so it loads in a background thread; until then DisMech edges show without PMIDs and say so."""
        import numpy as np
        import pyarrow.parquet as pq

        t0 = time.time()
        nodes = pq.read_table(P.nodes, columns=["gidx", "curie", "type", "name"], read_dictionary=["type"]).to_pandas()
        nodes = nodes.sort_values("gidx").reset_index(drop=True)
        if not (nodes.gidx.to_numpy() == np.arange(len(nodes))).all():
            raise ValueError("nodes.gidx is not contiguous 0..N-1")
        have = set(pq.read_schema(P.edges).names)
        cols = [c for c in ("edge_row", "src", "dst", "rel", "origin", "dm_row") if c in have]
        edges = pq.read_table(P.edges, columns=cols, read_dictionary=[c for c in ("rel", "origin") if c in cols]).to_pandas()
        if "edge_row" not in edges:
            edges["edge_row"] = np.arange(len(edges))
        for c in ("edge_row", "src", "dst", "dm_row"):  # 32-bit halves memory; the live retriever holds its own copy
            if c in edges and len(edges) and edges[c].max() < 2**31 and edges[c].min() >= -2**31:
                edges[c] = edges[c].astype("int32")
        src = edges["src"].to_numpy()
        order = np.argsort(src, kind="stable")
        self._full = {"curie": nodes.curie.to_numpy(), "name": nodes.name.astype(str).to_numpy(),
                      "type": nodes.type.astype(str).to_numpy(), "by_curie": dict(zip(nodes.curie, nodes.gidx)),
                      "edges": edges, "order": order, "src_sorted": src[order], "ev": None, "ev_state": "loading"}
        genes = nodes[nodes.type.astype(str) == "gene"]
        for n, c in zip(genes.name, genes.curie):
            self.symbol[str(n).upper()] = c
        stage = {k: self.meta.get(k) for k in ("n_trace_files", "n_nodes", "n_edges")}
        self.meta.update(source="full_graph", n_nodes=int(len(nodes)), n_edges=int(len(edges)), stage_index=stage,
                         load_s=round(time.time() - t0, 1),
                         description=("Merged PrimeKG + DisMech graph from data/processed (edges.parquet). The opening "
                                      "network is drawn from this project's executed retrievals of the same graph."))
        threading.Thread(target=self._load_evidence, daemon=True).start()

    def _load_evidence(self):
        F = self._full
        if not P.evidence.exists():
            F["ev_state"] = "missing"
            return
        try:
            import pandas as pd
            F["ev"] = pd.read_parquet(P.evidence).set_index("dm_row")
            F["ev_state"] = "ready"
        except Exception as exc:  # noqa: BLE001
            F["ev_state"] = f"failed: {type(exc).__name__}"

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
        if curie in self.nodes:
            return self.nodes[curie]
        if self._full is not None:
            g = self._full["by_curie"].get(curie)
            if g is not None:
                F = self._full
                return {"curie": curie, "name": str(F["name"][g]), "type": str(F["type"][g])}
        return None

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
        import numpy as np
        from common.contracts import edge_id
        F = self._full
        g = F["by_curie"].get(curie)
        if g is None:
            return []
        lo, hi = np.searchsorted(F["src_sorted"], [g, g + 1])
        rows = F["edges"].iloc[F["order"][lo:hi]]
        rows = rows[~rows.rel.astype(str).isin(HUB_RELS)]
        rows = rows.assign(_dm=(rows.origin.astype(str) != "dismech")).sort_values(["_dm", "edge_row"]).head(limit)
        ev, out = F["ev"], []
        for r in rows.itertuples():
            d = int(r.dst)
            pmids, quote = [], None
            if str(r.origin) == "dismech" and ev is not None and hasattr(r, "dm_row"):
                try:
                    from agent.tools import _evidence_for
                    pmids, quotes = _evidence_for(int(r.dm_row), ev)
                    quote = quotes[0] if quotes else None
                except Exception:  # noqa: BLE001
                    pass
            prov = "merged graph edge (edges.parquet)"
            if str(r.origin) == "dismech" and ev is None:
                prov += f"; DisMech evidence {F['ev_state']}"
            out.append({"edge_id": edge_id(int(r.edge_row)), "src": curie, "dst": str(F["curie"][d]), "rel": str(r.rel),
                        "origin": str(r.origin), "pmids": pmids, "quote": quote, "citable": True, "provenance": prov,
                        "_dst_node": {"curie": str(F["curie"][d]), "name": str(F["name"][d]), "type": str(F["type"][d])}})
        return out

    def describe(self) -> dict:
        d = dict(self.meta)
        if self._full is not None:
            d["evidence"] = self._full["ev_state"]
        return d

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

        for k, e in self.edges.items():  # every DisMech edge from executed retrievals (real E-IDs, PMIDs, quotes)
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
        if len(nodes) < cap:  # connectors: index edges between already-chosen nodes
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
            t0 = time.time()
            _index = GraphIndex()
            print(f"  graph index ready in {time.time() - t0:.1f}s ({_index.meta.get('source')})", flush=True)
        return _index
