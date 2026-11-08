"""GNN-B retriever (WS4). Contract: CONTRACTS.md section 6 (signature) and section 7 (SubgraphResult).

    from models.retriever import retrieve_subgraph
    res = retrieve_subgraph("What mechanisms explain X, which presents with A, B?", ["MONDO:...", "HP:..."],
                            budget=50, retriever="gnn_pcst", variant="full")
    res["triples_text"]   # "[E123] VHL (gene) --disease_protein--> ... (disease)" lines for the LLM
    res["edge_ids"], res["nodes"], res["edges"], res["meta"]

Retrievers (same signature; E1 loops over them with variant="e1"):
  bfs        nodes nearest the seeds, by degree (1-hop first, then 2-hop); spanning-forest edges for citation
  text_pcst  PCST with prizes = question<->node text similarity (G-Retriever-style, no GNN)
  gnn_pcst   PCST with prizes = ppr * PageRank-from-seeds + beta * GNN-A relation-aware score + alpha * text
             - hub * degree rank (penalty for hub genes / generic GO terms)
             (weights tuned in eval/e1_retrieval.py -> models/results/gnn_b_best_weights_typed.json)
Prizes go only to `target_types` nodes (default genes + GO terms, i.e. "mechanism" nodes); other nodes can still be
PCST connectors. Candidates = 2-hop neighbourhood of the seeds, capped at `cap` by prize. PCST (pcst_fast, GW pruning)
on the induced subgraph, edge cost binary-searched so the tree has <= budget non-seed nodes; dismech edges cost less.
GNN signal: genes = mean sigmoid(DistMult(seed -> gene)) over disease/phenotype/gene seeds; GO = average of two-step
(via the top-200 genes) and direct disease->GO (dm_affects / dm_has_participant relations); "lift" subtracts each node's background score (hub / promiscuity correction).

NOTE pcst_fast: the PyPI wheel returns wrong trees under NumPy 2.x. Install from source:
    pip install --no-binary pcst_fast pcst_fast==1.0.10     (scripts/setup_brev.sh does this)
"""
from __future__ import annotations

import json
import time
from functools import lru_cache

import numpy as np
import pandas as pd
import scipy.sparse as sp
import torch
import torch.nn.functional as F
from scipy.stats import rankdata

from common.contracts import (DEFAULT_BUDGET, GRAPH_VARIANTS, P, RETRIEVERS, ROOT, TEXT_EMBED_MODEL, edge_id, error,
                              patient_neighbours, triple_line, variant_mask)
from models.gnn_a_api import GnnA

GO_T = ('biological_process', 'molecular_function', 'cellular_component')
MECHANISM_TYPES = ('gene',) + GO_T
GENE2GO_REL = {'biological_process': 'bioprocess_protein', 'molecular_function': 'molfunc_protein', 'cellular_component': 'cellcomp_protein'}
SEED2GENE_REL = {'disease': 'disease_protein', 'phenotype': 'phenotype_protein', 'gene': 'protein_protein'}
# direct disease -> GO relations learned by GNN-A from dismech (test MRR 0.79 / 0.58 / 0.71)
DIS2GO_KEY = {'biological_process': 'disease__dm_affects__biological_process',
              'molecular_function': 'disease__dm_affects__molecular_function',
              'cellular_component': 'disease__dm_has_participant__cellular_component'}
WEIGHTS_FILE = ROOT / 'models' / 'results' / 'gnn_b_best_weights_typed.json'
FALLBACK_WEIGHTS = {'text': 0.0, 'ppr': 1.0, 'gnn': 0.25, 'hub': 0.0, 'lift': True, 'direct_go': True}  # if the file is missing


def gnn_weights() -> dict:
    if WEIGHTS_FILE.exists():
        w = json.loads(WEIGHTS_FILE.read_text())
        return {**w['weights'], 'lift': w.get('lift', True), 'direct_go': w.get('direct_go', False)}
    return dict(FALLBACK_WEIGHTS)


class Retriever:
    def __init__(self, variant: str = 'full', tag: str | None = None, device: str | None = None, n_bg: int = 500, seed: int = 0):
        if variant not in GRAPH_VARIANTS:
            raise ValueError(f'unknown variant {variant!r}; expected one of {list(GRAPH_VARIANTS)}')
        t0 = time.time()
        self.variant = variant
        self.tag = tag or ('no_dismech' if variant == 'no_dismech' else 'main')
        self.dev = torch.device(device or ('cuda' if torch.cuda.is_available() else 'cpu'))
        self.g = GnnA(str(P.processed), self.tag)
        self.N = len(self.g.nodes)
        self.NT, self.LIDX = self.g.NT, self.g.LIDX
        self.names, self.curies = self.g.nodes.name.astype(str).values, self.g.nodes.curie.values
        self.cur2g, self.gidx_by_type = self.g.cur2g, self.g.gidx_by_type
        # retrieval graph = the contract's graph variant; one representative directed edge per undirected pair
        # (dismech preferred so it carries PMIDs)
        base = ['edge_row', 'src', 'dst', 'rel', 'origin', 'dm_row', 'hub', 'e1_holdout', 'e4_dismech', 'e4_full']
        # also load whatever mask flags the variant needs (e.g. bifo_dropped), or bifo variants raise KeyError
        E = pd.read_parquet(P.edges, columns=list(dict.fromkeys(base + list(GRAPH_VARIANTS[variant]))))
        E = E[variant_mask(E, variant).values]
        pr = pd.DataFrame({'a': np.minimum(E.src.values, E.dst.values), 'b': np.maximum(E.src.values, E.dst.values),
                           'src': E.src.values, 'dst': E.dst.values, 'rel': E.rel.astype(str).values,
                           'origin': E.origin.astype(str).values, 'edge_row': E.edge_row.values, 'dm_row': E.dm_row.values})
        pr = pr[pr.a != pr.b].sort_values(['origin', 'edge_row'])            # 'dismech' < 'primekg'
        self.pairs = pr.drop_duplicates(['a', 'b']).reset_index(drop=True)
        m = len(self.pairs)
        pa, pb = self.pairs.a.values, self.pairs.b.values
        self.A = sp.csr_matrix((np.concatenate([np.arange(1, m + 1)] * 2), (np.concatenate([pa, pb]), np.concatenate([pb, pa]))),
                               shape=(self.N, self.N))
        Ab = self.A.copy(); Ab.data = np.ones_like(Ab.data, dtype=np.float32)
        self.deg = np.asarray(Ab.sum(1)).ravel()
        self.PT = (sp.diags(1 / np.maximum(self.deg, 1)) @ Ab).T.tocsr().astype(np.float32)
        self.is_dm = self.pairs.origin.values == 'dismech'
        ft = torch.load(P.feat_text, weights_only=False)
        if ft.get('model') != TEXT_EMBED_MODEL:
            raise ValueError(f"feat_text.pt was built with {ft.get('model')!r}, contract says {TEXT_EMBED_MODEL!r}")
        self.X = F.normalize(ft['x'], dim=-1).to(self.dev)
        self._st = None
        self.h = {t: v.to(self.dev) for t, v in self.g.h.items()}
        self.rel = {k: v.to(self.dev) for k, v in self.g.rel.items()}
        rng = np.random.default_rng(seed)
        self.bg = {}
        for S, r in SEED2GENE_REL.items():
            key = f'{S}__{r}__gene'
            if key in self.rel:
                idx = torch.from_numpy(rng.choice(len(self.gidx_by_type[S]), min(n_bg, len(self.gidx_by_type[S])), replace=False)).to(self.dev)
                self.bg[('gene', S)] = torch.sigmoid((self.h[S][idx] * self.rel[key]) @ self.h['gene'].T).mean(0)
        di = torch.from_numpy(rng.choice(len(self.gidx_by_type['disease']), min(n_bg, len(self.gidx_by_type['disease'])), replace=False)).to(self.dev)
        for T, key in DIS2GO_KEY.items():
            if key in self.rel:
                self.bg[('go_direct', T)] = torch.sigmoid((self.h['disease'][di] * self.rel[key]) @ self.h[T].T).mean(0)
        gi = torch.from_numpy(rng.choice(len(self.gidx_by_type['gene']), 2000, replace=False)).to(self.dev)
        for T, r in GENE2GO_REL.items():
            key = f'gene__{r}__{T}'
            if key in self.rel:
                self.bg[('go', T)] = torch.sigmoid((self.h['gene'][gi] * self.rel[key]) @ self.h[T].T).mean(0)
        self._evidence = None
        print(f'Retriever[{variant}/{self.tag}]: {self.N} nodes, {m} undirected edges ({int(self.is_dm.sum())} dismech), '
              f'{time.time() - t0:.0f}s', flush=True)

    # ------------------------------------------------------------------ signals (length-N arrays)
    def encode(self, texts):
        if self._st is None:
            from sentence_transformers import SentenceTransformer
            self._st = SentenceTransformer(TEXT_EMBED_MODEL, device=str(self.dev))
        return self._st.encode(texts, convert_to_tensor=True, normalize_embeddings=True, show_progress_bar=False).float().to(self.dev)

    def text_signal(self, question):
        return (self.X @ self.encode([question])[0]).cpu().numpy()

    def ppr_signal(self, seeds, it=25, alpha=0.15):
        s = np.zeros(self.N, np.float32); s[seeds] = 1 / len(seeds); p = s.copy()
        for _ in range(it):
            p = (1 - alpha) * (self.PT @ p) + alpha * s
        return p

    @torch.no_grad()
    def gnn_signal(self, seeds, lift=True, top_genes=200, direct_go=True):
        out = np.zeros(self.N, np.float32)
        comps = []
        for S, r in SEED2GENE_REL.items():
            li = [self.LIDX[x] for x in seeds if self.NT[x] == S]
            key = f'{S}__{r}__gene'
            if li and key in self.rel:
                sc = torch.sigmoid((self.h[S][torch.tensor(li, device=self.dev)] * self.rel[key]) @ self.h['gene'].T).mean(0)
                if lift:
                    sc = sc - self.bg[('gene', S)]
                comps.append(sc)
        if not comps:
            return out
        gs = torch.stack(comps).mean(0)
        out[self.gidx_by_type['gene']] = gs.cpu().numpy()
        top = torch.topk(gs, min(top_genes, len(gs)))
        w = torch.softmax(top.values / (top.values.std() + 1e-6), 0)
        for T, r in GENE2GO_REL.items():
            key = f'gene__{r}__{T}'
            if key in self.rel:
                sc = (w[:, None] * torch.sigmoid((self.h['gene'][top.indices] * self.rel[key]) @ self.h[T].T)).sum(0)
                if lift:
                    sc = sc - self.bg[('go', T)]
                dkey = DIS2GO_KEY.get(T)
                dli = [self.LIDX[x] for x in seeds if self.NT[x] == 'disease']
                if direct_go and dli and dkey in self.rel:                  # direct disease -> GO, averaged in
                    dsc = torch.sigmoid((self.h['disease'][torch.tensor(dli, device=self.dev)] * self.rel[dkey]) @ self.h[T].T).mean(0)
                    if lift:
                        dsc = dsc - self.bg[('go_direct', T)]
                    sc = 0.5 * (sc + dsc)
                out[self.gidx_by_type[T]] = sc.cpu().numpy()
        return out

    # ------------------------------------------------------------------ candidates / prizes / subgraph
    def two_hop(self, seeds):
        h1 = np.unique(self.A[seeds].indices)
        h2 = np.unique(self.A[h1].indices) if len(h1) else np.array([], np.int64)
        return np.setdiff1d(np.union1d(h1, h2), seeds)

    @staticmethod
    def rank_norm(v):
        return 1.0 - (rankdata(-v, method='average') - 1) / max(len(v) - 1, 1)

    def combine(self, cand, signals, weights, target_types=MECHANISM_TYPES, per_type=False):
        """Rank-normalised weighted sum; only `target_types` nodes get prizes (ranked among themselves).
        per_type=True ranks each node type separately, so the best drug or phenotype competes with the best gene
        instead of being buried under thousands of genes/GO terms. Off by default: E1 numbers use the shared ranking."""
        prize = np.zeros(len(cand))
        sel = np.isin(self.NT[cand], list(target_types)) if target_types else np.ones(len(cand), bool)
        groups = [sel & (self.NT[cand] == t) for t in target_types] if (per_type and target_types) else [sel]
        depth = max([int(g.sum()) for g in groups] + [1])
        for g in groups:
            if not g.any():
                continue
            s = np.zeros(int(g.sum()))
            for name, w in weights.items():
                if w and name in signals:
                    s += w * self.rank_norm(signals[name][cand[g]])
            if weights.get('hub'):                                           # hub / generic-term penalty
                s -= weights['hub'] * self.rank_norm(self.deg[cand[g]])
            if per_type:  # i-th best of every type gets the same prize, so the top-K terminals split across types
                s = 1.0 - (rankdata(-s, method='ordinal') - 1) / depth
            prize[g] = s
        return prize

    def pcst(self, seeds, cand, prize, budget=DEFAULT_BUDGET, cap=3000, K=150, dm_discount=0.5, iters=14):
        from pcst_fast import pcst_fast
        order = np.argsort(-prize, kind='stable')[:cap]
        nodes = np.concatenate([np.asarray(seeds), cand[order]])
        n_seed = len(seeds)
        p = np.zeros(len(nodes))
        k_ = min(K, len(order))
        p[n_seed:n_seed + k_] = (k_ - np.arange(k_)) / k_
        sub = sp.triu(self.A[nodes][:, nodes], k=1).tocoo()
        if sub.nnz == 0:
            return nodes[n_seed:n_seed + min(budget, k_)], np.array([], np.int64)
        edges = np.stack([sub.row, sub.col], 1).astype(np.int64)
        pid = sub.data.astype(np.int64) - 1
        base = np.where(self.is_dm[pid], dm_discount, 1.0)
        lo, hi, best = -4.0, 2.0, None
        for _ in range(iters):
            mid = (lo + hi) / 2
            v, e = pcst_fast(edges, p, base * 10 ** mid, -1, 1, 'gw', 0)
            if (v >= n_seed).sum() <= budget:
                best, hi = (v, e), mid
            else:
                lo = mid
        v, e = best if best is not None else pcst_fast(edges, p, base * 10 ** hi, -1, 1, 'gw', 0)
        v, e = list(v), list(e)
        while sum(1 for x in v if x >= n_seed) > budget:             # trim lowest-prize non-seed leaves (rare)
            degc = {}
            for ei in e:
                for x in edges[ei]:
                    degc[x] = degc.get(x, 0) + 1
            leaves = [x for x in v if x >= n_seed and degc.get(x, 0) <= 1]
            drop = min(leaves or [x for x in v if x >= n_seed], key=lambda x: p[x])
            v.remove(drop); e = [ei for ei in e if drop not in edges[ei]]
        v = np.array(v, dtype=np.int64)
        return nodes[v[v >= n_seed]], (pid[np.array(e, dtype=np.int64)] if e else np.array([], np.int64))

    def anchor_edges(self, seeds, sel, prize, max_edges=30):
        """Edges tying the retrieved nodes back to the seeds, adding no nodes. PCST gives seeds no prize, so the tree
        usually floats free of them. Each seed (disease/phenotype seeds first, then genes) gets its best direct edge into
        the retrieved set: dismech edges first (they carry PMIDs), then by the neighbour's prize. At most `max_edges`."""
        seeds, sel = np.asarray(seeds, dtype=np.int64), np.asarray(sel, dtype=np.int64)
        if max_edges <= 0 or not len(seeds) or not len(sel):
            return np.array([], np.int64)
        sub = self.A[seeds][:, sel].tocoo()
        best = {}
        for r, c, d in zip(sub.row, sub.col, sub.data):
            pid = int(d) - 1
            key = (bool(self.is_dm[pid]), float(prize.get(int(sel[c]), 0.0)))
            if r not in best or key > best[r][0]:
                best[r] = (key, pid)
        order = sorted(best, key=lambda r: (self.NT[seeds[r]] == 'gene', not best[r][0][0], -best[r][0][1]))
        return np.array([best[r][1] for r in order[:max_edges]], dtype=np.int64)

    def bfs_order(self, seeds, target_types=MECHANISM_TYPES):
        h1 = np.setdiff1d(np.unique(self.A[seeds].indices), seeds)
        h2 = np.setdiff1d(np.unique(self.A[h1].indices) if len(h1) else np.array([], np.int64), np.concatenate([seeds, h1]))
        order = np.concatenate([h1[np.argsort(-self.deg[h1], kind='stable')], h2[np.argsort(-self.deg[h2], kind='stable')]])
        return order[np.isin(self.NT[order], list(target_types))] if target_types else order

    def spanning_edges(self, nodes, seeds):
        """Citable edges for a node set without a tree (bfs): a spanning forest of the induced subgraph, seed edges first."""
        nodes = np.asarray(nodes)
        sub = sp.triu(self.A[nodes][:, nodes], k=1).tocoo()
        if sub.nnz == 0:
            return np.array([], np.int64)
        pid = sub.data.astype(np.int64) - 1
        seedset = set(np.asarray(seeds).tolist())
        touches_seed = np.array([nodes[r] in seedset or nodes[c] in seedset for r, c in zip(sub.row, sub.col)])
        order = np.lexsort((~self.is_dm[pid], ~touches_seed))
        parent = list(range(len(nodes)))
        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]; x = parent[x]
            return x
        keep = []
        for i in order:
            ra, rb = find(sub.row[i]), find(sub.col[i])
            if ra != rb:
                parent[ra] = rb; keep.append(pid[i])
        return np.array(keep, dtype=np.int64)

    # ------------------------------------------------------------------ evidence + result
    def evidence(self):
        if self._evidence is None:
            self._evidence = pd.read_parquet(P.evidence, columns=['dm_row', 'publications', 'supporting_text']).set_index('dm_row')
        return self._evidence

    def resolve_seeds(self, seeds):
        """CURIEs (nodes.curie or aliases) and PATIENT:<id> (expanded via contracts.patient_neighbours)."""
        out, missing, notes = [], [], []
        for s in seeds:
            if s.startswith('PATIENT:'):
                try:
                    neigh, _ = patient_neighbours(s.split(':', 1)[1])
                    out += [self.cur2g[c] for c in neigh if c in self.cur2g]
                    notes.append(f'{s}: {len(neigh)} neighbours')
                except Exception as ex:  # patient tables not built yet
                    missing.append(s); notes.append(f'{s}: {type(ex).__name__}: {ex}')
            elif s in self.cur2g:
                out.append(self.cur2g[s])
            else:
                missing.append(s)
        return np.array(sorted(set(out)), dtype=np.int64), missing, notes

    def subgraph(self, question, seed_curies, budget=DEFAULT_BUDGET, retriever='gnn_pcst', target_types=MECHANISM_TYPES,
                 weights=None, cap=3000, K=150, dm_discount=0.5, per_type=False, anchor=0):
        if retriever not in RETRIEVERS:
            return error(f'unknown retriever {retriever!r}; expected one of {list(RETRIEVERS)}')
        seeds, missing, notes = self.resolve_seeds(list(seed_curies))
        if len(seeds) == 0:
            return error('no seed resolved to a graph node', missing_seeds=missing, notes=notes)
        meta = {'retriever': retriever, 'variant': self.variant, 'budget': int(budget), 'gnn_a_tag': self.tag,
                'target_types': list(target_types or []), 'per_type': bool(per_type), 'missing_seeds': missing,
                'seed_notes': notes}
        if retriever == 'bfs':
            order = self.bfs_order(seeds, target_types)
            sel = order[:budget]
            pids = self.spanning_edges(np.concatenate([seeds, sel]), seeds)
            prize = {int(x): 1.0 - i / max(len(sel), 1) for i, x in enumerate(sel)}
            meta.update(n_candidates=int(len(order)), alpha=0.0, beta=0.0)
        else:
            w = {'text': 1.0, 'ppr': 0.0, 'gnn': 0.0, 'lift': False} if retriever == 'text_pcst' else (weights or gnn_weights())
            sig = {'text': self.text_signal(question)}
            if w.get('ppr'): sig['ppr'] = self.ppr_signal(seeds)
            if w.get('gnn'): sig['gnn'] = self.gnn_signal(seeds, lift=w.get('lift', True), direct_go=w.get('direct_go', True))
            cand = self.two_hop(seeds)
            pz = self.combine(cand, sig, {k: w.get(k, 0) for k in ('text', 'ppr', 'gnn', 'hub')}, target_types, per_type)
            sel, pids = self.pcst(seeds, cand, pz, budget=budget, cap=cap, K=K, dm_discount=dm_discount)
            prize = dict(zip(cand.tolist(), pz.tolist()))
            meta.update(n_candidates=int(len(cand)), alpha=float(w.get('text', 0)), beta=float(w.get('gnn', 0)),
                        ppr=float(w.get('ppr', 0)), hub=float(w.get('hub', 0)), lift=bool(w.get('lift', False)),
                        direct_go=bool(w.get('direct_go', False)))
        if anchor:  # seed -> retrieved-node edges first, so they survive the agent's truncation; no nodes are added
            anc = self.anchor_edges(seeds, sel, prize, anchor)
            pids = np.concatenate([anc, np.setdiff1d(np.asarray(pids, dtype=np.int64), anc)]).astype(np.int64)
            meta.update(anchor_edges=int(len(anc)))
        allv = np.concatenate([seeds, np.asarray(sel, dtype=np.int64)])
        nodes = [{'curie': self.curies[x], 'name': self.names[x], 'type': self.NT[x], 'prize': round(float(prize.get(int(x), 0.0)), 4),
                  'is_seed': i < len(seeds)} for i, x in enumerate(allv)]
        ev = self.evidence()
        edges, lines = [], []
        for r in (self.pairs.iloc[pids].itertuples() if len(pids) else []):
            eid = edge_id(r.edge_row)
            e = {'edge_id': eid, 'src_curie': self.curies[r.src], 'rel': r.rel, 'dst_curie': self.curies[r.dst], 'origin': r.origin}
            if r.dm_row >= 0 and r.dm_row in ev.index:
                pubs = str(ev.at[r.dm_row, 'publications'] or '')
                e['pmids'] = [x.strip() for x in pubs.replace('|', ',').split(',') if x.strip().upper().startswith('PMID:')]
                e['quote'] = str(ev.at[r.dm_row, 'supporting_text'] or '')[:300]
            edges.append(e)
            lines.append(triple_line(eid, self.names[r.src], self.NT[r.src], r.rel, self.names[r.dst], self.NT[r.dst]))
        return {'nodes': nodes, 'edges': edges, 'edge_ids': [e['edge_id'] for e in edges], 'triples_text': '\n'.join(lines), 'meta': meta}


@lru_cache(maxsize=3)
def get_retriever(variant: str = 'full') -> Retriever:
    return Retriever(variant)


def _require_inputs(variant: str) -> None:
    """Missing processed inputs -> raise the agent's `Missing` (like agent.tools._need does), so agent.tools.dispatch
    falls back to the mocks in BIOHACK_MOCK=auto and returns {"error": ...} otherwise."""
    tag = 'no_dismech' if variant == 'no_dismech' else 'main'
    missing = [p for p in (P.nodes, P.edges, P.evidence, P.feat_text, P.emb(tag)) if not p.exists()]
    if missing:
        try:
            from agent.tools import Missing
        except Exception:  # noqa: BLE001 - agent package unavailable: plain FileNotFoundError
            Missing = FileNotFoundError
        raise Missing('GNN-B inputs not found: ' + ', '.join(p.name for p in missing))


# Hub penalty for agent calls (custom target_types). eval/retriever_budget_sweep.py, 3 demo patients x 2 questions,
# budget 50: with hub=0 the first subgraph never linked a patient protein to a patient phenotype (0/6, the agent's
# retry trigger) and 45% of nodes were top-1%-degree hubs; hub=0.25 -> 6/6 linked, 14% hubs, ~5.7k chars (fits the
# agent's 6000-char view). E1 keeps its own tuned weights (hub 0).
AGENT_HUB_PENALTY = 0.25
# CONTRACTS.md section 6, adaptive size: budget=None -> the retriever sizes the subgraph itself. With no binding cap
# the cost search goes to its minimum, so PCST keeps all K (=150) prized terminals plus the connectors they need:
# ~150-185 nodes for the demo patients, ~165 on E1. The size is therefore set by K, not by a node count.
# ADAPTIVE_MAX_NODES is only a safety net far above that (it was 100, which bound every call, i.e. a fixed cap);
# the agent's ranked retrieval view, not this number, keeps the LLM input bounded. Must stay an int (pcst() compares it).
ADAPTIVE_BUDGET = True
ADAPTIVE_MAX_NODES = 500


def retrieve_subgraph(question: str, seeds: list[str], budget: int | None = DEFAULT_BUDGET,
                      retriever: str = 'gnn_pcst', variant: str = 'full', target_types=None,
                      hub: float | None = None, anchor: int = 0) -> dict:
    """CONTRACTS.md section 6/7. Missing input files raise the agent's `Missing` (handled by agent.tools.dispatch);
    every other failure comes back as {"error": ...}.
    target_types: node types that get prizes; None = MECHANISM_TYPES (genes + GO, the E1 setting). The agent passes
    a wider set (drugs, phenotypes, pathways, mechanisms) for its answers; E1 calls Retriever.subgraph directly.
    hub: degree penalty for gnn_pcst prizes; None = AGENT_HUB_PENALTY for custom target_types, else the tuned weights.
    anchor: add up to this many seed -> retrieved-node edges (no extra nodes) so the subgraph links back to the seeds.
    budget=None: self-sizing (ADAPTIVE_BUDGET), at most ADAPTIVE_MAX_NODES non-seed nodes."""
    _require_inputs(variant)
    try:
        custom = bool(target_types) and tuple(target_types) != MECHANISM_TYPES
        h = hub if hub is not None else (AGENT_HUB_PENALTY if custom else None)
        w = {**gnn_weights(), 'hub': float(h)} if (h is not None and retriever == 'gnn_pcst') else None
        res = get_retriever(variant).subgraph(question, list(seeds),
                                              budget=ADAPTIVE_MAX_NODES if budget is None else int(budget),
                                              retriever=retriever,
                                              target_types=tuple(target_types) if target_types else MECHANISM_TYPES,
                                              weights=w, per_type=custom,  # custom type sets rank each type separately
                                              anchor=int(anchor))
        if budget is None and 'meta' in res:
            res['meta'].update(adaptive_budget=True, budget_ceiling=ADAPTIVE_MAX_NODES,
                               n_nonseed=sum(not n['is_seed'] for n in res['nodes']))
        return res
    except Exception as ex:  # noqa: BLE001 - tools must not raise
        return error(f'retrieve_subgraph failed: {type(ex).__name__}: {ex}')
