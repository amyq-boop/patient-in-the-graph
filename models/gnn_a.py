#!/usr/bin/env python3
"""GNN-A, patient-free part (WS3 steps 2, 4, 5 + a pseudo-patient test of the step-3 code path).

* Graph: edges.parquet minus --mask flags (default: hub relations + E1 hold-out + E4 full ccRCC mask), so ONE model
  serves E1, E4 and the demo without leakage. --drop-origin dismech gives the "no dismech" ablation (step 5).
* Node input: text embeddings of node names (feat_text.pt) -> per-type Linear. Future patient nodes get features too.
* Encoder: 2-layer heterogeneous GraphSAGE (HeteroConv, mean over edge types) + per-type skip + LayerNorm,
  trained full-batch (the slim graph fits on one L40S).
* Decoder: DistMult (one diagonal relation vector per edge type), BCE with 1:1 negatives (random node of the dst type).
* Link-prediction split: 90/5/5 over undirected pairs (a pair and its reverse always land in the same split);
  val/test pairs are removed from message passing.
* Evaluations: link-pred MRR/Hits@10/AUC vs 50 sampled negatives; E1 preview (rank genes / GO for held-out
  diseases vs degree, personalized PageRank, text similarity); pseudo-patient ranking for ccRCC (E4 preview).
Outputs: models/ckpt/gnn_a_<tag>.pt (weights), data/processed/emb_<tag>.pt (embeddings), results/gnn_a_<tag>.json
"""
import argparse, json, os, time
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.nn import HeteroConv, SAGEConv


def k(et):
    return '__'.join(et)


class Encoder(nn.Module):
    def __init__(self, node_types, edge_types, in_dim, h=128, layers=2, dropout=0.1):
        super().__init__()
        self.inp = nn.ModuleDict({t: nn.Linear(in_dim, h) for t in node_types})
        self.convs = nn.ModuleList([HeteroConv({et: SAGEConv((h, h), h) for et in edge_types}, aggr='mean') for _ in range(layers)])
        self.skips = nn.ModuleList([nn.ModuleDict({t: nn.Linear(h, h) for t in node_types}) for _ in range(layers)])
        self.norms = nn.ModuleList([nn.ModuleDict({t: nn.LayerNorm(h) for t in node_types}) for _ in range(layers)])
        self.dropout = dropout

    def forward(self, x_dict, ei_dict):
        h = {t: self.inp[t](x) for t, x in x_dict.items()}
        for i, conv in enumerate(self.convs):
            out = conv(h, ei_dict)
            h = {t: self.norms[i][t](self.skips[i][t](h[t]) + (out[t] if t in out else 0)) for t in h}
            if i < len(self.convs) - 1:
                h = {t: F.dropout(F.relu(v), self.dropout, self.training) for t, v in h.items()}
        return h


class DistMult(nn.Module):
    def __init__(self, edge_types, h=128):
        super().__init__()
        self.rel = nn.ParameterDict({k(et): nn.Parameter(torch.ones(h)) for et in edge_types})

    def __contains__(self, et):
        return k(et) in self.rel

    def forward(self, hs, et, hd):
        return (hs * self.rel[k(et)] * hd).sum(-1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--processed', default='data/processed')
    ap.add_argument('--ckpt-dir', default='models/ckpt')
    ap.add_argument('--results', default='results')
    ap.add_argument('--tag', default='main')
    ap.add_argument('--mask', default='hub,e1_holdout,e4_full')
    ap.add_argument('--drop-origin', default='')
    ap.add_argument('--hidden', type=int, default=128)
    ap.add_argument('--layers', type=int, default=2)
    ap.add_argument('--dropout', type=float, default=0.1)
    ap.add_argument('--steps', type=int, default=800)
    ap.add_argument('--eval-every', type=int, default=100)
    ap.add_argument('--lr', type=float, default=2e-3)
    ap.add_argument('--per-type', type=int, default=2048)
    ap.add_argument('--val-frac', type=float, default=0.05)
    ap.add_argument('--test-frac', type=float, default=0.05)
    ap.add_argument('--seed', type=int, default=0)
    args = ap.parse_args()
    for d in [args.ckpt_dir, args.results]:
        os.makedirs(d, exist_ok=True)
    torch.manual_seed(args.seed)
    dev = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    T0 = time.time()
    R = {'args': vars(args)}
    def log(k_, v):
        R[k_] = v
        print(f'[{k_}] {v}' if not isinstance(v, (dict, list)) or len(str(v)) < 1500 else f'[{k_}] (long)', flush=True)
        json.dump(R, open(f'{args.results}/gnn_a_{args.tag}.json', 'w'), indent=1, default=str)

    # ------------------------------------------------------------------ data
    P = args.processed
    nodes = pd.read_parquet(f'{P}/nodes.parquet', columns=['gidx', 'curie', 'type', 'name', 'lidx'])
    E = pd.read_parquet(f'{P}/edges.parquet', columns=['src', 'dst', 'rel', 'origin', 'src_type', 'dst_type', 'hub', 'e1_holdout', 'e4_full', 'e4_dismech'])
    keep = np.ones(len(E), bool)
    for m in filter(None, args.mask.split(',')):
        keep &= ~E[m].values
    if args.drop_origin:
        keep &= (E.origin.astype(str) != args.drop_origin).values
    E = E[keep].reset_index(drop=True)
    for c in ['rel', 'src_type', 'dst_type']:
        E[c] = E[c].astype(str)
    N = len(nodes)
    NT, LIDX = nodes.type.values, nodes.lidx.values.astype(np.int64)
    types = sorted(nodes.type.unique())
    gidx_by_type = {t: nodes.gidx.values[NT == t] for t in types}
    n_by_type = {t: len(v) for t, v in gidx_by_type.items()}
    log('edges_after_mask', len(E))

    # undirected-pair split
    a, b = np.minimum(E.src.values, E.dst.values), np.maximum(E.src.values, E.dst.values)
    rb_codes, _ = pd.factorize(E.rel.str.replace(r'_rev$', '', regex=True))
    key = (rb_codes.astype(np.int64) * N + a) * N + b
    ukeys, inv = np.unique(key, return_inverse=True)
    r = np.random.default_rng(args.seed).random(len(ukeys))
    split = np.where(r < args.val_frac, 1, np.where(r < args.val_frac + args.test_frac, 2, 0))[inv]
    E['ls'], E['ld'], E['split'] = LIDX[E.src.values], LIDX[E.dst.values], split
    msg, val_pos, test_pos = {}, {}, {}
    for et, g in E.groupby(['src_type', 'rel', 'dst_type']):
        tr, va, te = g[g.split == 0], g[g.split == 1], g[g.split == 2]
        if len(tr):
            msg[et] = torch.from_numpy(np.stack([tr.ls.values, tr.ld.values])).to(dev)
        val_pos[et] = (va.ls.values, va.ld.values)
        test_pos[et] = (te.ls.values, te.ld.values)
    log('edge_types_message_passing', len(msg))
    log('message_edges', int(sum(v.shape[1] for v in msg.values())))

    feat = torch.load(f'{P}/feat_text.pt', weights_only=False)
    X = feat['x']
    x_dict = {t: X[torch.from_numpy(gidx_by_type[t])].to(dev) for t in types}

    # ------------------------------------------------------------------ model
    enc = Encoder(types, list(msg), X.shape[1], args.hidden, args.layers, args.dropout).to(dev)
    dec = DistMult(list(msg), args.hidden).to(dev)
    opt = torch.optim.Adam(list(enc.parameters()) + list(dec.parameters()), lr=args.lr)
    log('parameters', sum(p.numel() for p in enc.parameters()) + sum(p.numel() for p in dec.parameters()))

    eval_gen = torch.Generator(device='cpu').manual_seed(123)
    @torch.no_grad()
    def eval_lp(h, pos, nneg=50, maxn=2000):
        res = {}
        g = torch.Generator(device='cpu').manual_seed(123)
        for et, (s, d) in pos.items():
            if len(s) < 20 or et not in dec:
                continue
            sel = torch.randperm(len(s), generator=g)[:maxn].numpy()
            s_t, d_t = torch.from_numpy(s[sel]).to(dev), torch.from_numpy(d[sel]).to(dev)
            hs, rr = h[et[0]][s_t], dec.rel[k(et)]
            ps = (hs * rr * h[et[2]][d_t]).sum(-1)
            negd = torch.randint(0, n_by_type[et[2]], (len(sel), nneg), generator=g).to(dev)
            ns = (hs[:, None, :] * rr * h[et[2]][negd]).sum(-1)
            rank = 1 + (ns >= ps[:, None]).sum(1)
            res['|'.join(et)] = dict(n=len(sel), mrr=float((1 / rank.float()).mean()), hits10=float((rank <= 10).float().mean()),
                                     auc=float((ps[:, None] > ns).float().mean()))
        macro = {m: float(np.mean([v[m] for v in res.values()])) for m in ['mrr', 'hits10', 'auc']}
        return res, macro

    # ------------------------------------------------------------------ train
    best, best_state, curve = -1, None, []
    sup = list(msg)
    for step in range(1, args.steps + 1):
        enc.train(); dec.train(); opt.zero_grad()
        h = enc(x_dict, msg)
        losses = []
        for et in sup:
            ei = msg[et]
            idx = torch.randint(0, ei.shape[1], (min(args.per_type, ei.shape[1]),), device=dev)
            s, d = ei[0, idx], ei[1, idx]
            dn = torch.randint(0, n_by_type[et[2]], (len(idx),), device=dev)
            hs = h[et[0]][s]
            ps, ns = dec(hs, et, h[et[2]][d]), dec(hs, et, h[et[2]][dn])
            losses.append(F.binary_cross_entropy_with_logits(torch.cat([ps, ns]), torch.cat([torch.ones_like(ps), torch.zeros_like(ns)])))
        loss = torch.stack(losses).mean()
        loss.backward(); opt.step()
        if step % args.eval_every == 0 or step == args.steps:
            enc.eval(); dec.eval()
            with torch.no_grad():
                h = enc(x_dict, msg)
            _, macro = eval_lp(h, val_pos)
            curve.append(dict(step=step, loss=float(loss.detach()), **{f'val_{m}': v for m, v in macro.items()}))
            print(f'step {step} loss {float(loss.detach()):.4f} val {macro} ({time.time() - T0:.0f}s)', flush=True)
            if macro['mrr'] > best:
                best = macro['mrr']
                best_state = ({n: p.detach().clone() for n, p in enc.state_dict().items()}, {n: p.detach().clone() for n, p in dec.state_dict().items()})
    enc.load_state_dict(best_state[0]); dec.load_state_dict(best_state[1])
    enc.eval(); dec.eval()
    with torch.no_grad():
        h = enc(x_dict, msg)
    log('train_curve', curve)
    per_type, macro = eval_lp(h, test_pos)
    log('test_linkpred_macro', macro)
    KEY = ['gene|protein_protein|gene', 'disease|disease_protein|gene', 'gene|disease_protein|disease', 'drug|indication|disease',
           'disease|disease_phenotype_positive|phenotype', 'gene|bioprocess_protein|biological_process', 'drug|drug_protein|gene',
           'gene|dm_contributes_to|disease', 'disease|dm_has_phenotype|phenotype', 'mechanism|dm_causes|mechanism', 'drug|dm_treats_or_applied_or_studied_to_treat|disease']
    log('test_linkpred_key_relations', {kk: per_type[kk] for kk in KEY if kk in per_type})
    log('test_linkpred_all', per_type)

    # ------------------------------------------------------------------ helpers on final embeddings
    Hn = {t: F.normalize(v, dim=-1) for t, v in h.items()}
    Xn = {t: F.normalize(v, dim=-1) for t, v in x_dict.items()}
    cur2g = dict(zip(nodes.curie, nodes.gidx))
    def vec(Hd, gl):
        return torch.stack([Hd[NT[g]][LIDX[g]] for g in gl])
    import scipy.sparse as sp
    trE = E[E.split == 0]
    A = sp.coo_matrix((np.ones(len(trE), np.float32), (trE.src.values, trE.dst.values)), shape=(N, N)).tocsr()
    A = ((A + A.T) > 0).astype(np.float32).tocsr()
    deg = np.asarray(A.sum(1)).ravel()
    PT = (sp.diags(1 / np.maximum(deg, 1)) @ A).T.tocsr()
    def ppr(seeds, it=25, alpha=0.15):
        s = np.zeros(N, np.float32); s[seeds] = 1 / len(seeds); p = s.copy()
        for _ in range(it):
            p = (1 - alpha) * (PT @ p) + alpha * s
        return p
    GO = ['biological_process', 'molecular_function', 'cellular_component']
    cand = {'gene': gidx_by_type['gene'], 'go': np.concatenate([gidx_by_type[t] for t in GO])}
    def scores_sim(Hd, q, kind):
        if kind == 'gene':
            return (Hd['gene'] @ q).cpu().numpy()
        return torch.cat([Hd[t] @ q for t in GO]).cpu().numpy()

    # ------------------------------------------------------------------ E1 preview (held-out diseases)
    spl = json.load(open(f'{P}/e1_split.json'))
    rel_dg = ('disease', 'disease_protein', 'gene')
    rel_pg = ('phenotype', 'phenotype_protein', 'gene')
    rows = []
    for item in spl['test']:
        d = cur2g[item['disease']]
        seeds = [d] + [cur2g[c] for c in item['seeds_phenotype'] if c in cur2g]
        tg = {'gene': set(cur2g[c] for c in item['targets_gene']), 'go': set(cur2g[c] for c in item['targets_go'])}
        p = ppr(np.array(seeds))
        qg = F.normalize(vec(Hn, seeds).mean(0), dim=-1)
        qt = F.normalize(vec(Xn, seeds).mean(0), dim=-1)
        for kind in ['gene', 'go']:
            cg = cand[kind]
            meth = {'GNN seed-similarity': scores_sim(Hn, qg, kind), 'Text seed-similarity': scores_sim(Xn, qt, kind),
                    'Personalized PageRank': p[cg], 'Degree (popularity)': deg[cg]}
            if kind == 'gene' and rel_dg in dec:
                meth['GNN DistMult disease->gene'] = dec(h['disease'][LIDX[d]][None, :], rel_dg, h['gene']).detach().cpu().numpy()
                comps = [torch.sigmoid(dec(h['disease'][LIDX[d]][None, :], rel_dg, h['gene']))]
                ph = [x for x in seeds if NT[x] == 'phenotype']
                if ph and rel_pg in dec:
                    comps.append(torch.sigmoid((h['phenotype'][torch.from_numpy(LIDX[ph]).to(dev)] * dec.rel[k(rel_pg)]) @ h['gene'].T).mean(0))
                gnn_rel = torch.stack(comps).mean(0).detach().cpu().numpy()
                meth['GNN DistMult seeds->gene'] = gnn_rel
                rk = lambda v: np.argsort(np.argsort(-v, kind='stable'), kind='stable')
                meth['PPR + GNN (rank average)'] = -(rk(p[cg]) + rk(gnn_rel)).astype(np.float64)
            for mname, sc in meth.items():
                order = cg[np.argsort(-sc, kind='stable')]
                hit = np.isin(order, list(tg[kind]))
                first = int(np.argmax(hit)) + 1 if hit.any() else None
                rows.append(dict(disease=item['name'], kind=kind, method=mname, n_targets=len(tg[kind]),
                                 recall50=hit[:50].sum() / len(tg[kind]), recall500=hit[:500].sum() / len(tg[kind]),
                                 mrr=1 / first if first else 0.0))
    e1 = pd.DataFrame(rows)
    e1.to_csv(f'{args.results}/gnn_a_{args.tag}_e1_preview.csv', index=False)
    agg = e1.groupby(['kind', 'method'])[['recall50', 'recall500', 'mrr']].mean().round(4)
    print(agg.to_string())
    log('e1_preview', {f'{a_}|{b_}': row.to_dict() for (a_, b_), row in agg.iterrows()})
    log('e1_random_expectation_recall50', dict(gene=round(50 / len(cand['gene']), 4), go=round(50 / len(cand['go']), 4)))

    # ------------------------------------------------------------------ pseudo-patient (step 3/4 code path; E4 preview)
    gene_by_name = {n: g for g, n in zip(gidx_by_type['gene'], nodes.name.values[gidx_by_type['gene']])}
    cc = cur2g.get('MONDO:0005005')
    if cc is not None:
        phen = sorted(set(trE.dst.values[(trE.src.values == cc) & (trE.dst_type.values == 'phenotype')].tolist()))
        hif_up = [g for g in ['CA9', 'NDUFA4L2', 'PDK1', 'ENO2', 'HK2', 'SLC2A1', 'VEGFA', 'ANGPTL4', 'PLIN2', 'LOX', 'EGLN3'] if g in gene_by_name]
        prot = [gene_by_name[g] for g in hif_up]
        dis_names = nodes.name.values[gidx_by_type['disease']]
        drug_names = nodes.name.astype(str).str.lower().values[gidx_by_type['drug']]
        def rank_of(scores, cand_g, target_g):
            order = cand_g[np.argsort(-scores, kind='stable')]
            pos = np.flatnonzero(order == target_g)
            return int(pos[0]) + 1 if len(pos) else None
        out = {}
        for vname, nb in [('phenotypes_only', phen), ('HIF_proteins_only', prot), ('phenotypes+proteins', phen + prot)]:
            if not nb:
                continue
            q = F.normalize(vec(Hn, nb).mean(0), dim=-1)          # parameter-free inductive attach: mean of neighbour embeddings
            sd = (Hn['disease'] @ q).cpu().numpy(); sg = (Hn['gene'] @ q).cpu().numpy(); sr = (Hn['drug'] @ q).cpu().numpy()
            o = {'n_neighbours': len(nb),
                 'ccRCC_rank_among_diseases': rank_of(sd, gidx_by_type['disease'], cc),
                 'top10_diseases': [dis_names[i] for i in np.argsort(-sd)[:10]],
                 'gene_ranks': {g: rank_of(sg, gidx_by_type['gene'], gene_by_name[g]) for g in ['VHL', 'EPAS1', 'PBRM1', 'BAP1', 'SETD2', 'HIF1A', 'KDR'] if g in gene_by_name},
                 'drug_ranks': {}, 'top10_drugs': [drug_names[i] for i in np.argsort(-sr)[:10]]}
            for dname in ['sunitinib', 'pazopanib', 'axitinib', 'cabozantinib', 'belzutifan', 'everolimus', 'temsirolimus', 'lenvatinib']:
                hits = gidx_by_type['drug'][drug_names == dname]
                if len(hits):
                    o['drug_ranks'][dname] = rank_of(sr, gidx_by_type['drug'], hits[0])
            out[vname] = o
        log('pseudo_patient_ccRCC', dict(n_phenotypes=len(phen), proteins=hif_up, n_diseases=n_by_type['disease'],
                                         n_genes=n_by_type['gene'], n_drugs=n_by_type['drug'], results=out))

    # ------------------------------------------------------------------ save
    torch.save({'enc': enc.state_dict(), 'dec': dec.state_dict(), 'args': vars(args), 'node_types': types, 'edge_types': list(msg)},
               f'{args.ckpt_dir}/gnn_a_{args.tag}.pt')
    torch.save({'h': {t: v.cpu() for t, v in h.items()}, 'gidx_by_type': gidx_by_type, 'rel': {kk: v.detach().cpu() for kk, v in dec.rel.items()}},
               f'{P}/emb_{args.tag}.pt')
    log('runtime_s', round(time.time() - T0))


if __name__ == '__main__':
    main()
