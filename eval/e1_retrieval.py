"""E1: does the GNN make retrieval better?   python -m eval.e1_retrieval [--limit N]

196 held-out dismech diseases (e1_split.json test). Graph variant "e1" (hub + e1_holdout dropped), so their
disease-gene / disease-GO / mechanism edges are invisible to the retriever; GNN-A `main` was trained without them too.
Query = "What mechanisms explain <disease>, which presents with <phenotypes>?", seeds = [disease] + seeds_phenotype.
Each retriever returns <= 50 non-seed nodes; prizes go to gene + GO nodes for every method (fair type prior).

Outputs (eval/results/):
  e1.csv           CONTRACT: disease, kind (gene/go), method (bfs/text_pcst/gnn_pcst), n_targets, recall50, recall500, mrr
                   recall50  = fraction of that kind's hidden targets inside the <=50-node retrieved subgraph
                   recall500 = fraction in the top 500 of the method's candidate ranking (prize order; bfs: BFS order)
                   mrr       = 1 / rank of the first hidden target of that kind in that ranking
  e1_extended.csv  same columns, more methods:
                   ppr_pcst, text_ppr_pcst (best tuned non-GNN; may use the hub penalty too), gnn_only_pcst, text_gnn_pcst,
                   gnn_pcst_v1 (PR #3 version: no direct disease->GO, no hub penalty), gnn_pcst_direct (+ direct GO),
                   untyped_* (no gene/GO prize restriction)
  e1_summary.json  per-method means, Wilcoxon of gnn_pcst vs each method, tuned configs
  e1.png
Configs are cross-fitted: tuned on one half of the test diseases (ranking recall@50), scored on the other half.
models/results/gnn_b_best_weights_typed.json = gnn_pcst config tuned on all 196 (API/demo only, not used for E1 numbers).
"""
import argparse, itertools, json, os, time
import numpy as np
import pandas as pd
from scipy.stats import wilcoxon

from common.contracts import DEFAULT_BUDGET, GRAPH_VARIANTS, P, ROOT
from models.retriever import MECHANISM_TYPES, Retriever

ap = argparse.ArgumentParser()
ap.add_argument('--budget', type=int, default=DEFAULT_BUDGET)
ap.add_argument('--limit', type=int, default=0)
ap.add_argument('--out', default=str(P.results))
ap.add_argument('--variant', default='e1', help='graph variant; must hide e1_holdout (e.g. e1, e1_bifo, e1_bifo_strict)')
args = ap.parse_args()
if 'e1_holdout' not in GRAPH_VARIANTS[args.variant]:
    raise SystemExit(f'variant {args.variant!r} does not hide e1_holdout edges: E1 would leak')
if args.variant != 'e1' and args.out == str(P.results):
    args.out = str(P.results / f'e1_{args.variant}')  # never overwrite the contract e1.csv
T0 = time.time()
OUT = args.out
os.makedirs(OUT, exist_ok=True)
R = Retriever(args.variant)
spl = json.loads(P.e1_split.read_text())
test = spl['test'][:args.limit] if args.limit else spl['test']

C = []
for i, it in enumerate(test):
    d = R.cur2g[it['disease']]
    seeds = np.array(sorted({d} | {R.cur2g[c] for c in it['seeds_phenotype'] if c in R.cur2g}), dtype=np.int64)
    ph = [R.names[x] for x in seeds if R.NT[x] == 'phenotype'][:15]
    q = f"What mechanisms explain {it['name']}, which presents with {', '.join(ph)}?"
    tg = {'gene': {R.cur2g[c] for c in it['targets_gene'] if c in R.cur2g}, 'go': {R.cur2g[c] for c in it['targets_go'] if c in R.cur2g}}
    sig = {'text': R.text_signal(q), 'ppr': R.ppr_signal(seeds), 'deg': R.deg.astype(np.float32)}
    for lift, direct in itertools.product([False, True], [False, True]):
        sig[f"gnn_{'lift' if lift else 'raw'}_{'d' if direct else 'v1'}"] = R.gnn_signal(seeds, lift=lift, direct_go=direct)
    cand = R.two_hop(seeds)
    typed = np.isin(R.NT[cand], list(MECHANISM_TYPES))
    rn = {'untyped': {k: R.rank_norm(v[cand]).astype(np.float32) for k, v in sig.items()}, 'typed': {}}
    for k, v in sig.items():
        a = np.zeros(len(cand), np.float32); a[typed] = R.rank_norm(v[cand[typed]]); rn['typed'][k] = a
    C.append(dict(name=it['name'], disease=it['disease'], seeds=seeds, cand=cand, rn=rn, tg=tg, n_t=len(tg['gene']) + len(tg['go']),
                  all_t=np.isin(cand, list(tg['gene'] | tg['go']))))
    if i % 25 == 0:
        print(f'signals {i}/{len(test)} ({time.time() - T0:.0f}s)', flush=True)


def prize(c, cfg, mode):
    rn = c['rn'][mode]
    p = np.zeros(len(c['cand']), np.float32)
    if cfg.get('text'): p += cfg['text'] * rn['text']
    if cfg.get('ppr'): p += cfg['ppr'] * rn['ppr']
    if cfg.get('gnn'): p += cfg['gnn'] * rn[f"gnn_{'lift' if cfg.get('lift') else 'raw'}_{'d' if cfg.get('direct') else 'v1'}"]
    if cfg.get('hub'): p -= cfg['hub'] * rn['deg']
    return p


def rank_recall(c, p, k):
    top = np.argpartition(-p, min(k, len(p) - 1))[:k]
    return c['all_t'][top].sum() / max(c['n_t'], 1)


def grid(**axes):
    keys = list(axes)
    return [dict(zip(keys, vals)) for vals in itertools.product(*axes.values())]


GNN_AXES = dict(text=[0, 0.1, 0.25], ppr=[1], gnn=[0.25, 0.5, 1, 2], lift=[False, True])
GRIDS = {
    'text_pcst': ('typed', [dict(text=1)]),
    'gnn_pcst': ('typed', grid(**GNN_AXES, direct=[False, True], hub=[0, 0.25, 0.5])),
    'ppr_pcst': ('typed', [dict(ppr=1)]),
    'text_ppr_pcst': ('typed', grid(text=[0, 0.1, 0.25, 0.5, 1], ppr=[1], hub=[0, 0.25, 0.5])),
    'gnn_only_pcst': ('typed', grid(gnn=[1], lift=[False, True], direct=[True])),
    'text_gnn_pcst': ('typed', grid(text=[0, 0.1, 0.25, 0.5, 1], gnn=[1], lift=[False, True], direct=[True])),
    'gnn_pcst_v1': ('typed', grid(**GNN_AXES, direct=[False], hub=[0])),
    'gnn_pcst_direct': ('typed', grid(**GNN_AXES, direct=[True], hub=[0])),
    'untyped_text_pcst': ('untyped', [dict(text=1)]),
    'untyped_ppr_pcst': ('untyped', [dict(ppr=1)]),
    'untyped_gnn_pcst': ('untyped', grid(**GNN_AXES, direct=[True], hub=[0])),
}
half = np.random.default_rng(0).permutation(len(C)) % 2
chosen = {}
for meth, (mode, g) in GRIDS.items():
    for f in (0, 1):
        dev = [c for c, h in zip(C, half) if h != f]
        chosen[(meth, f)] = max(g, key=lambda cfg: np.mean([rank_recall(c, prize(c, cfg, mode), args.budget) for c in dev]))
print('chosen:', {f'{m}|fold{f}': v for (m, f), v in chosen.items()}, flush=True)


def kind_rows(c, method, sel, order, extra):
    rows = []
    for kind in ('gene', 'go'):
        n = len(c['tg'][kind])
        if not n:
            continue
        hit = np.isin(order, list(c['tg'][kind]))
        rows.append(dict(disease=c['disease'], kind=kind, method=method, n_targets=n,
                         recall50=float(np.isin(sel, list(c['tg'][kind])).sum() / n),
                         recall500=float(hit[:500].sum() / n), mrr=float(1 / (np.argmax(hit) + 1)) if hit.any() else 0.0, **extra))
    return rows


rows = []
for i, (c, h) in enumerate(zip(C, half)):
    for mode, name in [('typed', 'bfs'), ('untyped', 'untyped_bfs')]:
        order = R.bfs_order(c['seeds'], MECHANISM_TYPES if mode == 'typed' else None)
        rows += kind_rows(c, name, order[:args.budget], order, dict(sub_nodes=min(len(order), args.budget), cfg=''))
    for meth, (mode, _) in GRIDS.items():
        cfg = chosen[(meth, h)]
        p = prize(c, cfg, mode)
        order = c['cand'][np.argsort(-p, kind='stable')]
        sel, pids = R.pcst(c['seeds'], c['cand'], p.astype(np.float64), budget=args.budget)
        rows += kind_rows(c, meth, sel, order, dict(sub_nodes=len(sel), cfg=json.dumps(cfg)))
    if i % 25 == 0:
        print(f'pcst {i}/{len(C)} ({time.time() - T0:.0f}s)', flush=True)
df = pd.DataFrame(rows)
df[df.method.isin(['bfs', 'text_pcst', 'gnn_pcst'])][['disease', 'kind', 'method', 'n_targets', 'recall50', 'recall500', 'mrr']].to_csv(f'{OUT}/e1.csv', index=False)
df.to_csv(f'{OUT}/e1_extended.csv', index=False)

df['hits'] = df.recall50 * df.n_targets
pooled = df.groupby(['method', 'disease']).apply(lambda g: g.hits.sum() / g.n_targets.sum(), include_groups=False).rename('recall').reset_index()
order_m = ['bfs', 'text_pcst', 'ppr_pcst', 'text_ppr_pcst', 'gnn_only_pcst', 'text_gnn_pcst', 'gnn_pcst_v1', 'gnn_pcst_direct', 'gnn_pcst',
           'untyped_bfs', 'untyped_text_pcst', 'untyped_ppr_pcst', 'untyped_gnn_pcst']
summ = {}
for m in order_m:
    s = pooled[pooled.method == m].recall
    g = df[df.method == m]
    summ[m] = dict(recall_pooled=round(float(s.mean()), 4), sem=round(float(s.sem()), 4),
                   recall50_gene=round(float(g[g.kind == 'gene'].recall50.mean()), 4), recall50_go=round(float(g[g.kind == 'go'].recall50.mean()), 4),
                   recall500=round(float(g.recall500.mean()), 4), mrr=round(float(g.mrr.mean()), 4), sub_nodes=round(float(g.sub_nodes.mean()), 1))
ours = pooled[pooled.method == 'gnn_pcst'].set_index('disease').recall
tests = {}
for m in order_m:
    if m == 'gnn_pcst':
        continue
    other = pooled[pooled.method == m].set_index('disease').recall.reindex(ours.index)
    diff = ours - other
    try:
        pv = float(wilcoxon(ours, other, zero_method='zsplit').pvalue)
    except ValueError:
        pv = float('nan')
    tests[m] = dict(mean_diff=round(float(diff.mean()), 4), better=int((diff > 0).sum()), worse=int((diff < 0).sum()), tie=int((diff == 0).sum()), wilcoxon_p=pv)
best = max(GRIDS['gnn_pcst'][1], key=lambda cfg: np.mean([rank_recall(c, prize(c, cfg, 'typed'), args.budget) for c in C]))
(ROOT / 'models' / 'results').mkdir(parents=True, exist_ok=True)
(ROOT / 'models' / 'results' / 'gnn_b_best_weights_typed.json').write_text(json.dumps(
    {'weights': {'text': best.get('text', 0), 'ppr': best.get('ppr', 0), 'gnn': best.get('gnn', 0), 'hub': best.get('hub', 0)},
     'lift': bool(best.get('lift')), 'direct_go': bool(best.get('direct')), 'target_types': list(MECHANISM_TYPES),
     'note': 'tuned on all E1 test diseases (ranking recall@50); for the API/demo only. E1 numbers use cross-fitted configs.'}, indent=1))
res = dict(variant=args.variant, gnn_a_tag=R.tag, n_test_diseases=len(C), budget=args.budget,
           ceiling_targets_within_2hop=round(float(np.mean([c['all_t'].sum() / c['n_t'] for c in C])), 3),
           median_candidates_2hop=int(np.median([len(c['cand']) for c in C])), median_seeds=int(np.median([len(c['seeds']) for c in C])),
           summary=summ, gnn_pcst_vs=tests, chosen={f'{m}|fold{f}': v for (m, f), v in chosen.items()}, api_config=best,
           runtime_s=round(time.time() - T0), definitions=__doc__)
json.dump(res, open(f'{OUT}/e1_summary.json', 'w'), indent=1, default=str)
try:
    import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt
    show = ['bfs', 'text_pcst', 'ppr_pcst', 'text_ppr_pcst', 'gnn_only_pcst', 'text_gnn_pcst', 'gnn_pcst_v1', 'gnn_pcst']
    g = df[df.method.isin(show)]
    mm = g.groupby(['method', 'kind']).recall50.mean().unstack().loc[show]
    ee = g.groupby(['method', 'kind']).recall50.sem().unstack().loc[show]
    ax = mm.plot.barh(xerr=ee, figsize=(9, 5.5), capsize=2, color=['#4C72B0', '#DD8452'])
    ax.invert_yaxis(); ax.set_xlabel(f'Recall of hidden mechanism nodes inside the {args.budget}-node subgraph')
    ax.set_title(f'E1: retrieval on {len(C)} held-out dismech diseases (variant {args.variant})'); ax.legend(['genes', 'GO terms'])
    plt.tight_layout(); plt.savefig(f'{OUT}/e1.png', dpi=150)
except Exception as ex:
    print('plot skipped:', ex)
print(json.dumps(summ, indent=1)); print(json.dumps(tests, indent=1)); print(f'done in {time.time() - T0:.0f}s')
