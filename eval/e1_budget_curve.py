"""E1 recall vs subgraph size (budget):   python -m eval.e1_budget_curve

E1 stays reported at the contract budget (50, eval/results/e1.csv). This adds the curve the team asked for: the same
E1 (196 held-out diseases, variant e1, cross-fitted configs tuned for that budget) re-run at other budgets with
    python -m eval.e1_retrieval --budget K --out eval/results/e1_budget/bK
and collected here with the budget-50 run (eval/results/e1_summary.json).
Outputs: eval/results/e1_budget_curve.csv (budget x method: pooled / gene / GO recall, MRR, mean subgraph size, and
gnn_pcst vs that method: mean paired difference, better/worse/tie, Wilcoxon p) and eval/results/e1_budget_curve.png
"""
import argparse
import json
from pathlib import Path

import pandas as pd

from common.contracts import P

ap = argparse.ArgumentParser()
ap.add_argument('--budgets', default='25,50,100,200')
args = ap.parse_args()
BUDGETS = [int(b) for b in args.budgets.split(',')]
SHOW = {  # method -> label (fixed order = fixed colour slot; ours first)
    'gnn_pcst': 'Graph search + GNN (ours)',
    'text_ppr_pcst': 'Best without GNN: text + PageRank PCST',
    'text_pcst': 'Graph search, text only',
    'bfs': 'Simple graph search (BFS)',
}
COLORS = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100']  # dataviz reference categorical slots 1-4 (validated, light)
INK, INK2, GRID, SURFACE = '#0b0b0b', '#52514e', '#e4e3df', '#fcfcfb'

rows = []
for b in BUDGETS:
    f = P.results / 'e1_summary.json' if b == 50 else P.results / 'e1_budget' / f'b{b}' / 'e1_summary.json'
    if not f.exists():
        print(f'missing {f}: run python -m eval.e1_retrieval --budget {b} --out eval/results/e1_budget/b{b}')
        continue
    s = json.loads(f.read_text())
    assert s.get('budget', 50) == b and s.get('variant', 'e1') == 'e1', (f, s.get('budget'), s.get('variant'))
    for m, v in s['summary'].items():
        t = s.get('gnn_pcst_vs', {}).get(m, {})
        rows.append(dict(budget=b, method=m, n_diseases=s['n_test_diseases'], recall_pooled=v.get('recall_pooled'),
                         recall_gene=v.get('recall50_gene'), recall_go=v.get('recall50_go'), mrr=v.get('mrr'),
                         mean_subgraph_nodes=v.get('sub_nodes'), gnn_minus_method=t.get('mean_diff'),
                         gnn_better=t.get('better'), gnn_worse=t.get('worse'), gnn_tie=t.get('tie'), wilcoxon_p=t.get('wilcoxon_p')))
df = pd.DataFrame(rows).sort_values(['method', 'budget'])
df.to_csv(P.results / 'e1_budget_curve.csv', index=False)
piv = df.pivot(index='method', columns='budget', values='recall_pooled')
print(piv.loc[[m for m in SHOW if m in piv.index]].round(3).to_string())

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

fig, ax = plt.subplots(figsize=(9, 6.0), facecolor=SURFACE)
ax.set_facecolor(SURFACE)
ends = []
for (m, label), c in zip(SHOW.items(), COLORS):
    d = df[df.method == m].sort_values('budget')
    if d.empty:
        continue
    ax.plot(d.budget, d.recall_pooled, color=c, lw=2, marker='o', ms=8, mec=SURFACE, mew=2, label=label, zorder=3)
    ends.append([float(d.recall_pooled.iloc[-1]), label, float(d.budget.iloc[-1])])
ends.sort()
for i in range(1, len(ends)):  # keep the end labels at least ~0.022 apart
    ends[i][0] = max(ends[i][0], ends[i - 1][0] + 0.022)
for y, label, x in ends:
    ax.annotate(label.split(':')[0], (x, y), xytext=(10, 0), textcoords='offset points',
                va='center', fontsize=9.5, color=INK)
ax.axvline(50, color=INK2, lw=1, ls=(0, (3, 3)), zorder=1)
ax.text(50, 0.995, '  E1 headline budget (50)', transform=ax.get_xaxis_transform(), va='top', fontsize=9, color=INK2)
ax.set_xscale('log', base=2)
ax.set_xticks(BUDGETS); ax.set_xticklabels([str(b) for b in BUDGETS])
ax.set_xlim(min(BUDGETS) / 1.25, max(BUDGETS) * 2.6)
ax.set_ylim(0, max(0.05, float(df[df.method.isin(SHOW)].recall_pooled.max()) * 1.15))
ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
ax.grid(axis='y', color=GRID, lw=0.8); ax.set_axisbelow(True)
for s in ('top', 'right', 'left'):
    ax.spines[s].set_visible(False)
ax.spines['bottom'].set_color(GRID)
ax.tick_params(colors=INK2, length=0)
ax.set_xlabel('Subgraph size (max non-seed nodes)', color=INK2)
ax.set_ylabel('Hidden targets recovered (pooled recall)', color=INK2)
n = int(df.n_diseases.max()) if len(df) else 0
fig.suptitle('E1: recall vs subgraph size', x=0.07, ha='left', fontsize=14, color=INK, y=0.98)
ax.set_title(f'{n} held-out diseases; each method re-tuned (cross-fitted) at every size', loc='left', fontsize=10, color=INK2, pad=12)
ax.legend(loc='upper center', bbox_to_anchor=(0.45, -0.13), ncol=2, frameon=False, fontsize=9, labelcolor=INK)
fig.tight_layout()
fig.savefig(P.results / 'e1_budget_curve.png', dpi=150, facecolor=SURFACE)
print('wrote', P.results / 'e1_budget_curve.csv', P.results / 'e1_budget_curve.png')
