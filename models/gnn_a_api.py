"""GNN-A query API (WS3 step 4) for the agent and for GNN-B.

    from models.gnn_a_api import GnnA
    g = GnnA('data/processed', tag='main')
    g.rank_for_patient(['HP:0000790', 'NCBIGene:768'], 'drug', k=10)       # neighbours of a (pseudo-)patient
    g.rank_relation('MONDO:0005005', ('disease', 'disease_protein', 'gene'), k=20)
    g.similarity_to_seeds(seed_curies, 'gene')                               # GNN-B beta term (one score per node of a type)

Patients are attached inductively WITHOUT retraining: a patient's embedding is the (signed, weighted) mean of its
neighbours' GNN embeddings. When WS2 delivers real patient edges, pass each patient's neighbour CURIEs (+ weights;
negative weight for down-regulated proteins). The trained outcome head (step 3) will replace this for predict_patient().
"""
import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F


class GnnA:
    def __init__(self, processed='data/processed', tag='main'):
        self.nodes = pd.read_parquet(f'{processed}/nodes.parquet', columns=['gidx', 'curie', 'type', 'name', 'lidx'])
        emb = torch.load(f'{processed}/emb_{tag}.pt', weights_only=False)
        self.h = emb['h']
        self.hn = {t: F.normalize(v, dim=-1) for t, v in self.h.items()}
        self.rel = emb['rel']
        self.gidx_by_type = emb['gidx_by_type']
        self.cur2g = dict(zip(self.nodes.curie, self.nodes.gidx))
        for g, al in zip(self.nodes.gidx, pd.read_parquet(f'{processed}/nodes.parquet', columns=['aliases']).aliases.fillna('')):
            for a in filter(None, al.split('|')):
                self.cur2g.setdefault(a, g)
        self.NT = self.nodes.type.values
        self.LIDX = self.nodes.lidx.values

    def _vecs(self, curies, normalized=True):
        H = self.hn if normalized else self.h
        gl = [self.cur2g[c] for c in curies if c in self.cur2g]
        missing = [c for c in curies if c not in self.cur2g]
        return (torch.stack([H[self.NT[g]][self.LIDX[g]] for g in gl]) if gl else None), gl, missing

    def _table(self, node_type, scores, k, exclude):
        cand = self.gidx_by_type[node_type]
        order = np.argsort(-scores, kind='stable')
        rows = []
        for i in order:
            g = cand[i]
            if g in exclude:
                continue
            rows.append((self.nodes.curie.values[g], self.nodes.name.values[g], float(scores[i])))
            if len(rows) == k:
                break
        return pd.DataFrame(rows, columns=['curie', 'name', 'score'])

    def similarity_to_seeds(self, seed_curies, node_type, weights=None):
        """Cosine similarity of every node of `node_type` to the (weighted) mean seed embedding."""
        V, gl, _ = self._vecs(seed_curies)
        if V is None:
            return np.zeros(len(self.gidx_by_type[node_type]))
        w = torch.ones(len(gl)) if weights is None else torch.tensor([weights[i] for i, c in enumerate(seed_curies) if c in self.cur2g], dtype=torch.float)
        q = F.normalize((w[:, None] * V).sum(0), dim=-1)
        return (self.hn[node_type] @ q).numpy()

    def rank_for_patient(self, neighbour_curies, node_type, k=20, weights=None, exclude_neighbours=True):
        s = self.similarity_to_seeds(neighbour_curies, node_type, weights)
        excl = {self.cur2g[c] for c in neighbour_curies if c in self.cur2g} if exclude_neighbours else set()
        return self._table(node_type, s, k, excl)

    # preferred trained relation for (neighbour type -> target type); drug_effect (side effects) deliberately not used
    PREF = {('gene', 'drug'): 'drug_protein', ('gene', 'disease'): 'disease_protein', ('gene', 'gene'): 'protein_protein',
            ('gene', 'biological_process'): 'bioprocess_protein', ('gene', 'pathway'): 'pathway_protein',
            ('phenotype', 'disease'): 'disease_phenotype_positive', ('phenotype', 'gene'): 'phenotype_protein',
            ('phenotype', 'phenotype'): 'phenotype_phenotype', ('disease', 'gene'): 'disease_protein',
            ('disease', 'drug'): 'indication', ('disease', 'phenotype'): 'disease_phenotype_positive'}

    def relation_scores(self, neighbour_curies, node_type, weights=None):
        """Relation-aware score: for each neighbour type S with a trained relation S->node_type, mean sigmoid(DistMult);
        averaged over neighbour types. Falls back to cosine if no relation applies."""
        w_all = [1.0] * len(neighbour_curies) if weights is None else list(weights)
        by_type = {}
        for c, w in zip(neighbour_curies, w_all):
            g = self.cur2g.get(c)
            if g is not None:
                by_type.setdefault(self.NT[g], []).append((self.LIDX[g], w))
        parts = []
        for S, items in by_type.items():
            rel = self.PREF.get((S, node_type))
            key = '__'.join((S, rel, node_type)) if rel else None
            if key not in self.rel:
                continue
            li = torch.tensor([i for i, _ in items]); w = torch.tensor([x for _, x in items], dtype=torch.float)
            sc = torch.sigmoid((self.h[S][li] * self.rel[key]) @ self.h[node_type].T)
            parts.append((w[:, None] * sc).sum(0) / w.abs().sum())
        if not parts:
            return self.similarity_to_seeds(neighbour_curies, node_type, weights)
        return torch.stack(parts).mean(0).numpy()

    def rank_for_patient_rel(self, neighbour_curies, node_type, k=20, weights=None, exclude_neighbours=True):
        s = self.relation_scores(neighbour_curies, node_type, weights)
        excl = {self.cur2g[c] for c in neighbour_curies if c in self.cur2g} if exclude_neighbours else set()
        return self._table(node_type, s, k, excl)

    def rank_relation(self, src_curie, edge_type, k=20):
        """DistMult score of (src, relation, every node of the dst type), e.g. disease -> genes."""
        key = '__'.join(edge_type)
        if key not in self.rel:
            raise KeyError(f'relation {edge_type} not in trained model')
        g = self.cur2g[src_curie]
        hs = self.h[self.NT[g]][self.LIDX[g]]
        s = ((hs * self.rel[key]) @ self.h[edge_type[2]].T).numpy()
        return self._table(edge_type[2], s, k, {g})
