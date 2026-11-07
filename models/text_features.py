#!/usr/bin/env python3
"""Text-embedding node features for every node (also used by GNN-B for question<->node similarity).

Output: data/processed/feat_text.pt = {'model': str, 'x': FloatTensor [num_nodes, d] in gidx order, 'text': list[str]}
Swap --model for an NVIDIA NIM embedding endpoint later (sponsor points); the file format stays the same.
"""
import argparse, time
import pandas as pd
import torch

ap = argparse.ArgumentParser()
ap.add_argument('--processed', default='data/processed')
ap.add_argument('--model', default='sentence-transformers/all-MiniLM-L6-v2')
ap.add_argument('--batch', type=int, default=1024)
args = ap.parse_args()

from sentence_transformers import SentenceTransformer
t0 = time.time()
nodes = pd.read_parquet(f'{args.processed}/nodes.parquet', columns=['gidx', 'type', 'name', 'alt_names'])
assert (nodes.gidx.values == range(len(nodes))).all()
TYPE_WORD = {'gene': 'gene', 'disease': 'disease', 'phenotype': 'phenotype', 'drug': 'drug', 'biological_process': 'biological process',
             'molecular_function': 'molecular function', 'cellular_component': 'cellular component', 'pathway': 'pathway',
             'anatomy': 'anatomy', 'exposure': 'exposure', 'mechanism': 'disease mechanism', 'cell': 'cell type', 'treatment': 'treatment'}
text = [f"{TYPE_WORD.get(t, t)}: {n}" + (f" (also {a.replace('|', ', ')})" if a else '')
        for t, n, a in zip(nodes.type, nodes.name.astype(str), nodes.alt_names.fillna(''))]
model = SentenceTransformer(args.model, device='cuda' if torch.cuda.is_available() else 'cpu')
x = model.encode(text, batch_size=args.batch, convert_to_tensor=True, normalize_embeddings=True, show_progress_bar=False).float().cpu()
torch.save({'model': args.model, 'x': x, 'text': text}, f'{args.processed}/feat_text.pt')
print(f'feat_text: {tuple(x.shape)} with {args.model} in {time.time() - t0:.0f}s')
print('examples:', text[:2], text[-2:])
