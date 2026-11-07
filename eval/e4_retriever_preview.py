"""E4 retriever preview (GNN-B only, no LLM):   python -m eval.e4_retriever_preview

ccRCC question on graph variant "e4" (hub + e4_full dropped: ccRCC's dismech, mechanism, PrimeKG gene and drug edges
are hidden; GNN-A `main` was trained without them too). Seeds = ccRCC + its dismech presentation phenotypes (a stand-in
for a chart). Reports which known ccRCC biology the <=50-node subgraph recovers. The agent-level E4 result is the
Agent workstream's eval/results/e4.json; this file is eval/results/e4_retriever_preview.json.
"""
import json

import pandas as pd

from common.contracts import CCRCC_GENES, CCRCC_ID, P
from models.retriever import MECHANISM_TYPES, get_retriever

R = get_retriever('e4')
E = pd.read_parquet(P.edges, columns=['src', 'dst', 'dst_type'])
cc = R.cur2g[CCRCC_ID]
phen = sorted(set(E.dst[(E.src == cc) & (E.dst_type == 'phenotype')]))      # from the unmasked table: patient input, not answer key
seeds = [CCRCC_ID] + [R.curies[x] for x in phen]
q = ('What mechanisms explain clear cell renal cell carcinoma, which presents with '
     + ', '.join(R.names[x] for x in phen) + '? What would you target?')
res = R.subgraph(q, seeds, budget=50, retriever='gnn_pcst', target_types=MECHANISM_TYPES + ('drug',))
names = {n['name'].upper() for n in res['nodes']}
KEY_GENES = list(CCRCC_GENES) + ['HIF1A', 'KDR', 'SETD2', 'MTOR', 'CA9']
KEY_GO = ['angiogenesis', 'response to hypoxia', 'cellular response to hypoxia', 'glycolytic process', 'tricarboxylic acid cycle']
KEY_DRUGS = ['sunitinib', 'pazopanib', 'axitinib', 'cabozantinib', 'belzutifan', 'everolimus', 'temsirolimus']
non_seed = [n for n in res['nodes'] if not n['is_seed']]
out = dict(variant='e4', question=q, n_nodes=len(res['nodes']), n_seeds=len(seeds), n_edges=len(res['edges']),
           node_types=pd.Series([n['type'] for n in non_seed]).value_counts().to_dict(),
           key_genes_found=[g for g in KEY_GENES if g in names], key_go_found=[g for g in KEY_GO if g.upper() in names],
           key_drugs_found=[d for d in KEY_DRUGS if d.upper() in names],
           edges_with_pmid=sum(1 for e in res['edges'] if e.get('pmids')),
           top_nonseed=[(n['name'], n['type']) for n in sorted(non_seed, key=lambda n: -n['prize'])[:25]], meta=res['meta'])
(P.results / 'e4_retriever_preview.json').write_text(json.dumps(out, indent=1))
(P.results / 'e4_retriever_preview_triples.txt').write_text(q + '\n\n' + res['triples_text'])
print(json.dumps(out, indent=1))
