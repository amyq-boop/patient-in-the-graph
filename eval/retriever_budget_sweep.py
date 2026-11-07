"""Retriever budget sweep: what happens if we lift the 50-node cap?   python -m eval.retriever_budget_sweep [--part e1|patient]

Mentor to-do: "remove the evidence retriever's 50-node cap and check whether PyTorch Geometric returns coherent
sub-networks without breaking the agent call". Compares, on the same seeds and prizes:
  pcst     the GNN-B retriever (PCST tree) at budgets 25..400 and with no cap (budget = inf; size set by K terminals);
           anchor=30 adds up to 30 seed -> retrieved-node edges (Retriever.anchor_edges; no extra nodes)
  topk     the same prizes, top-`budget` nodes with no tree (citable spanning-forest edges only): is the tree what
           makes the subgraph coherent?
  khop1/2  torch_geometric.utils.k_hop_subgraph around the seeds: the plain PyG neighbourhood, no pruning
Cases:
  e1       40 held-out E1 diseases (variant e1, genes + GO prizes, API weights): recall of the hidden targets
  patient  3 demo patients x 2 questions (variant full), seeded the way the agent's controller does
           (PATIENT:<id> + phenotypes + top-10 up + top-5 down proteins) with the agent's wider node types,
           per-type ranking, and hub penalty 0 / 0.5 / 1
Coherence / usability per subgraph:
  n_nonseed, n_edges, comps_nonseed (connected pieces the non-seed nodes fall into), largest_share, seeds_in_tree,
  hub_share (non-seed nodes in the top 1% by degree), dm_share / pmid_share (edges from dismech / with a PMID),
  relevance (mean text cosine question <-> node), view_chars + visible_share (the agent shows the LLM
  json({triples_text, edge_ids, n_nodes}) cut at AGENT_TOOL_RESULT_CHARS = 6000: what fraction of edges survives),
  recall (e1), pp_connected (patient: a KG path links a patient protein to a patient phenotype, the agent's own
  retry criterion), type mix.
Outputs: eval/results/retriever_budget_sweep.csv (one row per case x config), retriever_budget_sweep_summary.json
"""
import argparse
import json
import time
from collections import defaultdict

import numpy as np
import pandas as pd
import scipy.sparse as sp
import torch
from scipy.sparse.csgraph import connected_components

from common.contracts import P, edge_id, triple_line
from models.retriever import MECHANISM_TYPES, Retriever, gnn_weights

ap = argparse.ArgumentParser()
ap.add_argument('--part', choices=['e1', 'patient', 'all'], default='all')
ap.add_argument('--n-e1', type=int, default=40)
ap.add_argument('--budgets', default='25,50,100,200,400')
ap.add_argument('--hubs', default='0,0.5,1')
ap.add_argument('--chars', type=int, default=6000, help='agent TOOL_RESULT_CHARS')
ap.add_argument('--out', default=str(P.results))
args = ap.parse_args()
BUDGETS = [int(b) for b in args.budgets.split(',')]
HUBS = [float(h) for h in args.hubs.split(',')]
NOCAP = 10 ** 9
AGENT_TT = ('gene', 'biological_process', 'molecular_function', 'cellular_component', 'drug', 'phenotype', 'pathway', 'mechanism')
DEMO = ['C3L-00416', 'C3N-00244', 'C3L-00790']
T0 = time.time()


def build(R, seeds, sel, pids):
    """Same result shape as Retriever.subgraph (nodes / edges / edge_ids / triples_text) for a node set + edges."""
    ev = R.evidence()
    allv = np.concatenate([seeds, np.asarray(sel, dtype=np.int64)])
    nodes = [{'curie': R.curies[x], 'name': R.names[x], 'type': R.NT[x], 'is_seed': i < len(seeds)} for i, x in enumerate(allv)]
    edges, lines = [], []
    for r in (R.pairs.iloc[pids].itertuples() if len(pids) else []):
        e = {'edge_id': edge_id(r.edge_row), 'src_curie': R.curies[r.src], 'dst_curie': R.curies[r.dst], 'origin': r.origin}
        if r.dm_row >= 0 and r.dm_row in ev.index:
            pubs = str(ev.at[r.dm_row, 'publications'] or '')
            e['pmids'] = [x for x in pubs.replace('|', ',').split(',') if x.strip().upper().startswith('PMID:')]
        edges.append(e)
        lines.append(triple_line(e['edge_id'], R.names[r.src], R.NT[r.src], r.rel, R.names[r.dst], R.NT[r.dst]))
    return {'nodes': nodes, 'edges': edges, 'edge_ids': [e['edge_id'] for e in edges], 'triples_text': '\n'.join(lines)}


def prizes(R, q, seeds, tt, per_type, w):
    sig = {'text': R.text_signal(q)}
    if w.get('ppr'): sig['ppr'] = R.ppr_signal(seeds)
    if w.get('gnn'): sig['gnn'] = R.gnn_signal(seeds, lift=w.get('lift', True), direct_go=w.get('direct_go', True))
    cand = R.two_hop(seeds)
    return cand, R.combine(cand, sig, {k: w.get(k, 0) for k in ('text', 'ppr', 'gnn', 'hub')}, tt, per_type)


def metrics(R, res, seeds, q_emb, hub_t, targets=None, patient=None):
    g = R.cur2g
    allv = np.array([g[n['curie']] for n in res['nodes']], dtype=np.int64)
    sel = np.array([g[n['curie']] for n in res['nodes'] if not n['is_seed']], dtype=np.int64)
    idx = {int(x): i for i, x in enumerate(allv)}
    ri = [idx[g[e['src_curie']]] for e in res['edges']]; ci = [idx[g[e['dst_curie']]] for e in res['edges']]
    adj = sp.coo_matrix((np.ones(len(ri)), (ri, ci)), shape=(len(allv), len(allv)))
    _, lab = connected_components(adj, directed=False)
    nl = lab[[idx[int(x)] for x in sel]] if len(sel) else np.array([], int)
    touched = set(ri) | set(ci)
    view = json.dumps({'triples_text': res['triples_text'], 'edge_ids': res['edge_ids'], 'n_nodes': len(res['nodes'])})
    cut = view[:args.chars]
    m = dict(n_nonseed=len(sel), n_edges=len(res['edges']),
             comps_nonseed=int(len(np.unique(nl))) if len(nl) else 0,
             largest_share=float(np.bincount(nl).max() / len(nl)) if len(nl) else 0.0,
             seeds_in_tree=float(np.mean([idx[int(s)] in touched for s in seeds])),
             hub_share=float(np.mean(R.deg[sel] >= hub_t)) if len(sel) else 0.0,
             dm_share=float(np.mean([e['origin'] == 'dismech' for e in res['edges']])) if res['edges'] else 0.0,
             pmid_share=float(np.mean([bool(e.get('pmids')) for e in res['edges']])) if res['edges'] else 0.0,
             relevance=float((R.X[torch.as_tensor(sel, device=R.dev)] @ q_emb).mean()) if len(sel) else 0.0,
             view_chars=len(view),
             visible_share=float(np.mean([f'[{eid}]' in cut for eid in res['edge_ids']])) if res['edge_ids'] else 1.0)
    if targets is not None:
        m['recall'] = len(set(sel.tolist()) & targets) / max(len(targets), 1)
    if patient is not None:
        phen = {p['curie'] for p in patient['phenotypes']}
        prot = {p['curie'] for p in patient['proteins_up'] + patient['proteins_down']}
        nb = defaultdict(set)
        for e in res['edges']:
            nb[e['src_curie']].add(e['dst_curie']); nb[e['dst_curie']].add(e['src_curie'])
        seen, stack, ok = set(prot), list(prot), not phen
        while stack and not ok:
            n = stack.pop()
            ok = n in phen
            for x in nb[n] - seen:
                seen.add(x); stack.append(x)
        m['pp_connected'] = bool(ok)
        types = pd.Series([R.NT[x] for x in sel]).value_counts()
        for t in ('gene', 'drug', 'phenotype', 'pathway', 'biological_process', 'disease'):
            m[f'n_{t}'] = int(types.get(t, 0))
    return m


def khop_metrics(R, ei, seeds, hops, hub_t, q_emb, targets=None):
    from torch_geometric.utils import k_hop_subgraph
    subset = k_hop_subgraph(torch.as_tensor(seeds), hops, ei, num_nodes=R.N, relabel_nodes=False)[0].numpy()
    sel = np.setdiff1d(subset, seeds)
    n_edges = int(sp.triu(R.A[subset][:, subset], k=1).nnz)
    m = dict(n_nonseed=len(sel), n_edges=n_edges, hub_share=float(np.mean(R.deg[sel] >= hub_t)) if len(sel) else 0.0,
             relevance=float((R.X[torch.as_tensor(sel, device=R.dev)] @ q_emb).mean()) if len(sel) else 0.0,
             view_chars=int(n_edges * 95), visible_share=min(1.0, args.chars / max(n_edges * 95, 1)))
    if targets is not None:
        m['recall'] = len(set(sel.tolist()) & targets) / max(len(targets), 1)
    return m


def edge_index(R):
    a, b = R.pairs.a.values, R.pairs.b.values
    return torch.as_tensor(np.stack([np.concatenate([a, b]), np.concatenate([b, a])]), dtype=torch.long)


rows = []


def run_cases(R, cases, configs, hub_t, ei):
    for ci, c in enumerate(cases):
        q_emb = R.encode([c['q']])[0]
        seeds, _, _ = R.resolve_seeds(c['seeds'])
        for cfg in configs:
            t = time.time()
            w = {**gnn_weights(), 'hub': cfg.get('hub', 0.0)}
            if cfg['method'] == 'pcst':
                res = R.subgraph(c['q'], c['seeds'], budget=cfg['budget'], target_types=c['tt'], weights=w,
                                 per_type=c['per_type'], K=cfg.get('K', 150), anchor=cfg.get('anchor', 0))
                m = metrics(R, res, seeds, q_emb, hub_t, c.get('targets'), c.get('patient'))
            elif cfg['method'] == 'topk':
                cand, pz = prizes(R, c['q'], seeds, c['tt'], c['per_type'], w)
                sel = cand[np.argsort(-pz, kind='stable')[:cfg['budget']]]
                res = build(R, seeds, sel, R.spanning_edges(np.concatenate([seeds, sel]), seeds))
                m = metrics(R, res, seeds, q_emb, hub_t, c.get('targets'), c.get('patient'))
            else:
                m = khop_metrics(R, ei, seeds, cfg['hops'], hub_t, q_emb, c.get('targets'))
            rows.append({'set': c['set'], 'case': c['id'], 'method': cfg['method'],
                         'budget': 'none' if cfg.get('budget') == NOCAP else cfg.get('budget', f"{cfg.get('hops')}-hop"),
                         'K': cfg.get('K', 150) if cfg['method'] == 'pcst' else None, 'hub': cfg.get('hub', 0.0),
                         'anchor': cfg.get('anchor', 0),
                         'seconds': round(time.time() - t, 2), **m})
        print(f"{c['set']} {ci + 1}/{len(cases)} ({time.time() - T0:.0f}s)", flush=True)


if args.part in ('e1', 'all'):
    R = Retriever('e1')
    hub_t = float(np.percentile(R.deg[R.deg > 0], 99))
    spl = json.loads(P.e1_split.read_text())
    pick = sorted(np.random.default_rng(0).choice(len(spl['test']), min(args.n_e1, len(spl['test'])), replace=False))
    cases = []
    for i in pick:
        it = spl['test'][i]
        s = [it['disease']] + [c for c in it['seeds_phenotype'] if c in R.cur2g]
        ph = [R.names[R.cur2g[c]] for c in s[1:16]]
        cases.append(dict(set='e1', id=it['disease'], q=f"What mechanisms explain {it['name']}, which presents with {', '.join(ph)}?",
                          seeds=s, tt=MECHANISM_TYPES, per_type=False,
                          targets={R.cur2g[c] for c in it['targets_gene'] + it['targets_go'] if c in R.cur2g}))
    cfgs = ([dict(method='pcst', budget=b) for b in BUDGETS] + [dict(method='pcst', budget=NOCAP), dict(method='pcst', budget=NOCAP, K=500)]
            + [dict(method='pcst', budget=b, anchor=30) for b in (50, 100)]
            + [dict(method='topk', budget=b) for b in BUDGETS if b <= 200] + [dict(method='khop', hops=1), dict(method='khop', hops=2)])
    run_cases(R, cases, cfgs, hub_t, edge_index(R))
    del R; torch.cuda.empty_cache()

if args.part in ('patient', 'all'):
    import os
    os.environ.setdefault('BIOHACK_MOCK', '0')
    from agent.tools import get_patient
    R = Retriever('full')
    hub_t = float(np.percentile(R.deg[R.deg > 0], 99))
    qs = {json.loads(l)['qid']: json.loads(l)['question'] for l in open('eval/questions.jsonl') if l.strip()}
    cases = []
    for pid in DEMO:
        pt = get_patient(pid)
        seeds = ([pt['curie']] + [p['curie'] for p in pt['phenotypes']] + [p['curie'] for p in pt['proteins_up'][:10]]
                 + [p['curie'] for p in pt['proteins_down'][:5]])
        for qid in ('P01', 'P03'):
            cases.append(dict(set='patient', id=f'{pid}:{qid}', q=qs[qid], seeds=seeds, tt=AGENT_TT, per_type=True, patient=pt))
    cfgs = ([dict(method='pcst', budget=b, hub=h, anchor=a) for b in BUDGETS + [NOCAP] for h in HUBS for a in (0, 30)]
            + [dict(method='topk', budget=b, hub=0.0) for b in (50, 100)] + [dict(method='khop', hops=1), dict(method='khop', hops=2)])
    run_cases(R, cases, cfgs, hub_t, edge_index(R))

df = pd.DataFrame(rows)
tag = '' if args.part == 'all' else f'_{args.part}'
df.to_csv(f'{args.out}/retriever_budget_sweep{tag}.csv', index=False)
num = [c for c in df.columns if c not in ('set', 'case', 'method', 'budget', 'K', 'hub', 'anchor')]
summ = (df.assign(budget=df.budget.astype(str), K=df.K.fillna(-1)).groupby(['set', 'method', 'budget', 'K', 'hub', 'anchor'], sort=False)[num]
        .mean(numeric_only=True).round(3).reset_index())
summ.to_json(f'{args.out}/retriever_budget_sweep{tag}_summary.json', orient='records', indent=1)
with pd.option_context('display.width', 250, 'display.max_columns', 40):
    print(summ.to_string(index=False))
print(f'done in {time.time() - T0:.0f}s')
