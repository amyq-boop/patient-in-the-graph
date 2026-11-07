#!/usr/bin/env python3
"""Pseudo-patient check of the patient code path (WS3 step 3/4) and an E4 preview, from saved GNN-A embeddings.

Patient = ccRCC presentation phenotypes (dismech has_phenotype for MONDO:0005005; a real patient's chart would supply
these) + HIF-target proteins that are strongly up in CPTAC ccRCC tumours (pilot: median z 4-6.6 vs normal).
The model was trained with the E4-full mask, so ccRCC's gene/drug/mechanism edges were hidden from it.
"""
import argparse, json, sys
import numpy as np, pandas as pd
sys.path.insert(0, '.')
from models.gnn_a_api import GnnA

ap = argparse.ArgumentParser(); ap.add_argument('--tag', default='main'); ap.add_argument('--processed', default='data/processed')
args = ap.parse_args()
g = GnnA(args.processed, args.tag)
E = pd.read_parquet(f'{args.processed}/edges.parquet', columns=['src', 'dst', 'rel', 'dst_type'])
cc = g.cur2g['MONDO:0005005']
ph_g = sorted(set(E.dst[(E.src == cc) & (E.dst_type == 'phenotype')]))
phen = [g.nodes.curie.values[x] for x in ph_g]
genes = g.nodes[g.nodes.type == 'gene'].set_index('name').curie.to_dict()
HIF = ['CA9', 'NDUFA4L2', 'PDK1', 'ENO2', 'HK2', 'SLC2A1', 'VEGFA', 'ANGPTL4', 'PLIN2', 'LOX', 'EGLN3']
prot = [genes[x] for x in HIF if x in genes]
names = dict(zip(g.nodes.curie, g.nodes.name))
def ranks(df_all, wanted):
    pos = {n.lower(): i + 1 for i, n in enumerate(df_all.name.astype(str))}
    return {w: pos.get(w.lower()) for w in wanted}
out = {'patient_phenotypes': [names[c] for c in phen], 'patient_proteins': HIF}
for variant, nb in [('phenotypes_only', phen), ('proteins_only', prot), ('phenotypes+proteins', phen + prot)]:
    if not nb:
        continue
    o = {}
    for method, fn in [('relation', g.rank_for_patient_rel), ('cosine', g.rank_for_patient)]:
        dis = fn(nb, 'disease', k=len(g.gidx_by_type['disease']))
        gen = fn(nb, 'gene', k=len(g.gidx_by_type['gene']))
        drg = fn(nb, 'drug', k=len(g.gidx_by_type['drug']))
        rcc_like = dis.reset_index(drop=True)
        rcc_hits = rcc_like.index[rcc_like.name.str.contains('renal cell|kidney cancer|renal carcinoma|kidney carcinoma', case=False, na=False)]
        o[method] = dict(
            best_rank_any_RCC_disease=int(rcc_hits[0]) + 1 if len(rcc_hits) else None,
            ccRCC_rank=ranks(dis, ['clear cell renal carcinoma'])['clear cell renal carcinoma'],
            top10_diseases=dis.name.head(10).tolist(),
            gene_ranks=ranks(gen, ['VHL', 'EPAS1', 'PBRM1', 'BAP1', 'SETD2', 'HIF1A', 'KDR']),
            drug_ranks=ranks(drg, ['sunitinib', 'pazopanib', 'axitinib', 'cabozantinib', 'belzutifan', 'everolimus', 'lenvatinib', 'temsirolimus']),
            top10_drugs=drg.name.head(10).tolist())
    out[variant] = o
out['candidates'] = {t: len(g.gidx_by_type[t]) for t in ['disease', 'gene', 'drug']}
json.dump(out, open(f'results/pseudo_patient_{args.tag}.json', 'w'), indent=1)
print(json.dumps(out, indent=1)[:6000])
