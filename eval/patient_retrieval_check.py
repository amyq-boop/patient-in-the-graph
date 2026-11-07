"""Patient-seeded retrieval check (GNN-B):   python -m eval.patient_retrieval_check

Picks 3 real CPTAC patients WITHOUT looking at labels (most phenotypes, ties by id) and asks the demo question through
the contract API with PATIENT:<id> seeds (expanded via contracts.patient_neighbours: phenotypes + top-50 up/down
proteins), with and without the ccRCC disease node as an extra seed, on variants `full` (demo) and `e4` (ccRCC answer
key hidden). Reports which known ccRCC biology the <=50-node subgraph contains. Output: eval/results/patient_retrieval_check.json
"""
import json

import pandas as pd

from common.contracts import CCRCC_GENES, CCRCC_ID, P
from models.retriever import retrieve_subgraph

Q = "What drives this patient's predicted risk, and what would you target?"
KEY_GENES = list(CCRCC_GENES) + ['HIF1A', 'KDR', 'SETD2', 'MTOR', 'CA9', 'NDUFA4L2', 'EGLN3']
KEY_GO = ['angiogenesis', 'response to hypoxia', 'cellular response to hypoxia', 'glycolytic process', 'tricarboxylic acid cycle']

ph = pd.read_parquet(P.patient_phenotypes)
pids = ph.groupby('patient_id').size().reset_index(name='n').sort_values(['n', 'patient_id'], ascending=[False, True]).patient_id.head(3).tolist()
out = {'question': Q, 'patients': pids, 'runs': []}
for pid in pids:
    for variant in ['full', 'e4']:
        for with_cc in [False, True]:
            seeds = [f'PATIENT:{pid}'] + ([CCRCC_ID] if with_cc else [])
            res = retrieve_subgraph(Q, seeds, budget=50, retriever='gnn_pcst', variant=variant)
            if 'error' in res:
                out['runs'].append(dict(patient=pid, variant=variant, with_ccrcc_seed=with_cc, error=res['error'])); continue
            ns = [n for n in res['nodes'] if not n['is_seed']]
            names = {n['name'].upper() for n in res['nodes']}
            nonseed_names = {n['name'].upper() for n in ns}
            out['runs'].append(dict(
                patient=pid, variant=variant, with_ccrcc_seed=with_cc, n_seeds=sum(n['is_seed'] for n in res['nodes']),
                n_nonseed=len(ns), n_edges=len(res['edges']), edges_with_pmid=sum(1 for e in res['edges'] if e.get('pmids')),
                nonseed_types=pd.Series([n['type'] for n in ns]).value_counts().to_dict(),
                key_genes_anywhere=[g for g in KEY_GENES if g in names], key_genes_retrieved=[g for g in KEY_GENES if g in nonseed_names],
                key_go_retrieved=[g for g in KEY_GO if g.upper() in nonseed_names],
                top_nonseed=[(n['name'], n['type']) for n in sorted(ns, key=lambda n: -n['prize'])[:12]],
                meta={k: res['meta'][k] for k in ('n_candidates', 'alpha', 'beta', 'ppr', 'hub', 'direct_go', 'seed_notes') if k in res['meta']}))
            r = out['runs'][-1]
            print(f"{pid} {variant:4s} cc={with_cc!s:5s} genes={r['key_genes_retrieved']} GO={r['key_go_retrieved']} pmid_edges={r['edges_with_pmid']}", flush=True)
(P.results / 'patient_retrieval_check.json').write_text(json.dumps(out, indent=1))
