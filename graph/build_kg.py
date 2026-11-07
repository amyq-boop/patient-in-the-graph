#!/usr/bin/env python3
"""WS1: build the merged PrimeKG + dismech knowledge graph.

Inputs  (--raw):   kg.csv (PrimeKG), dismech_nodes.tsv, dismech_edges.tsv
Outputs (--out):
  nodes.parquet        one row per node: gidx, curie, type, lidx (index within its type), name, origin, aliases, dm_ids
  edges.parquet        one row per DIRECTED edge: src, dst (gidx), src_type, rel, dst_type, origin, dm_row, + hold-out flags
  evidence.parquet     every dismech edge row (publications, supporting text, ...) keyed by dm_row -> cite in the agent
  unmapped_nodes.tsv   dismech nodes we could not type/map (dropped), with reason
  graph_full.pt        PyG HeteroData, all edges
  graph_slim.pt        without hub relations (--hub-relations), the default for GNN training
  graph_slim_e1.pt     graph_slim minus the E1 held-out edges of the test diseases
  e1_split.json        E1 train/test diseases with their target genes/GO terms and phenotype seeds
  ws1_report.json      counts, mapping coverage, overlap PrimeKG vs dismech, "done when" checks
Edge flags in edges.parquet: hub, e1_holdout, e4_dismech (ccRCC dismech edges), e4_full (+ PrimeKG ccRCC gene/drug edges).
Each HeteroData edge type carries `edge_row` = row in edges.parquet, so any retrieved edge can be traced to its evidence.
"""
import argparse, json, os, re, time
from collections import Counter, defaultdict
import numpy as np
import pandas as pd
import requests

ap = argparse.ArgumentParser()
ap.add_argument('--raw', default='data/raw')
ap.add_argument('--out', default='data/processed')
ap.add_argument('--cache', default='cache')
ap.add_argument('--seed', type=int, default=0)
ap.add_argument('--test-frac', type=float, default=0.3)
ap.add_argument('--hub-relations', default='anatomy_protein_present,anatomy_protein_absent,drug_drug')
ap.add_argument('--ccrcc', default='MONDO:0005005')
ap.add_argument('--no-torch', action='store_true', help='skip HeteroData export (for quick tests)')
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
os.makedirs(args.cache, exist_ok=True)

REPORT = {}
T0 = time.time()
def _js(v):
    if isinstance(v, dict): return {(' | '.join(map(str, k)) if isinstance(k, tuple) else str(k)): _js(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)): return [_js(x) for x in v]
    if isinstance(v, (np.integer,)): return int(v)
    if isinstance(v, (np.floating,)): return float(v)
    if isinstance(v, np.bool_): return bool(v)
    return v
def rep(k, v):
    v = _js(v)
    REPORT[k] = v
    print(f'[{k}] {v}', flush=True)
    with open(f'{args.out}/ws1_report.json', 'w') as f:
        json.dump(REPORT, f, indent=1, default=str)
def tick(msg):
    print(f'--- {msg} ({time.time() - T0:.0f}s)', flush=True)
def san(s):
    return re.sub(r'[^a-z0-9]+', '_', str(s).lower()).strip('_')

# ----------------------------------------------------------------------------- PrimeKG nodes
tick('load PrimeKG')
PK_TYPE = {'gene/protein': 'gene', 'disease': 'disease', 'effect/phenotype': 'phenotype', 'drug': 'drug',
           'biological_process': 'biological_process', 'molecular_function': 'molecular_function',
           'cellular_component': 'cellular_component', 'pathway': 'pathway', 'anatomy': 'anatomy', 'exposure': 'exposure'}
SRC_PREFIX = {'NCBI': 'NCBIGene', 'MONDO': 'MONDO', 'MONDO_grouped': 'MONDO', 'HPO': 'HP', 'DrugBank': 'DRUGBANK',
              'GO': 'GO', 'REACTOME': 'REACT', 'UBERON': 'UBERON', 'CTD': 'MESH'}
PAD7 = {'MONDO', 'HP', 'GO', 'UBERON', 'CL'}
def curie(prefix, local):
    local = str(local)
    if prefix in PAD7 and local.isdigit():
        local = local.zfill(7)
    return f'{prefix}:{local}'

kg = pd.read_csv(f'{args.raw}/kg.csv', low_memory=False,
                 dtype={'x_index': 'int64', 'y_index': 'int64', 'x_id': str, 'y_id': str})
cols = ['idx', 'id', 'type', 'name', 'source']
pn = pd.concat([kg[['x_index', 'x_id', 'x_type', 'x_name', 'x_source']].set_axis(cols, axis=1),
                kg[['y_index', 'y_id', 'y_type', 'y_name', 'y_source']].set_axis(cols, axis=1)])
pn = pn.drop_duplicates('idx').sort_values('idx').reset_index(drop=True)
rep('primekg_node_index_contiguous', bool((pn.idx.values == np.arange(len(pn))).all()))
rep('primekg_sources_by_type', pn.groupby('type').source.unique().map(list).to_dict())
pref = pn.source.map(lambda s: SRC_PREFIX.get(s, s))
cur_lists = [[curie(p, x) for x in (str(i).split('_') if p == 'MONDO' else [str(i)])] for i, p in zip(pn.id, pref)]
pn['curie'] = [c[0] for c in cur_lists]
pn['aliases'] = ['|'.join(c[1:]) for c in cur_lists]
pn['type'] = pn.type.map(PK_TYPE)
assert pn.type.notna().all(), 'unknown PrimeKG node type'
# PrimeKG stores some renamed genes twice under one NCBI id (e.g. GOLM2/CASC4): merge them into one node
dupmask = pn.curie.duplicated()
rep('primekg_duplicate_curies_merged', dict(pn[dupmask].type.value_counts()))
alt = pn[dupmask].groupby('curie').name.apply(lambda x: '|'.join(x.astype(str)))
keep = pn[~dupmask].reset_index(drop=True)
cur2new = dict(zip(keep.curie, range(len(keep))))
PK_MAP = np.full(int(pn.idx.max()) + 1, -1, dtype=np.int64)   # PrimeKG node_index -> our gidx
PK_MAP[pn.idx.values] = pn.curie.map(cur2new).values
pn = keep
pn['idx'] = np.arange(len(pn))
pn['alt_names'] = pn.curie.map(alt).fillna('')
pn['origin'] = 'primekg'
pn['dm_ids'] = ''

by_curie = {}
for g, c, a in zip(pn.idx, pn.curie, pn.aliases):
    by_curie.setdefault(c, g)
    for x in filter(None, a.split('|')):
        by_curie.setdefault(x, g)
by_name = {}
for g, t, n, a in zip(pn.idx, pn.type, pn.name.astype(str).str.lower(), pn.alt_names.str.lower()):
    by_name.setdefault((t, n), g)
    for x in filter(None, a.split('|')):
        by_name.setdefault((t, x), g)

# ----------------------------------------------------------------------------- dismech nodes
tick('load dismech')
dn = pd.read_csv(f'{args.raw}/dismech_nodes.tsv', sep='\t', dtype=str, low_memory=False).fillna('')
de = pd.read_csv(f'{args.raw}/dismech_edges.tsv', sep='\t', dtype=str, low_memory=False).fillna('')
print('dismech node columns:', dn.columns.tolist())
print('dismech edge columns:', de.columns.tolist())
if 'name' not in dn:
    dn['name'] = dn['id']
if 'category' not in dn:
    dn['category'] = ''
dn = dn.drop_duplicates('id')
dn['prefix'] = dn['id'].str.split(':').str[0].str.lower()
STD = {'hgnc': 'HGNC', 'mondo': 'MONDO', 'hp': 'HP', 'go': 'GO', 'uberon': 'UBERON', 'cl': 'CL', 'chebi': 'CHEBI',
       'ncit': 'NCIT', 'drugbank': 'DRUGBANK', 'dismech': 'dismech', 'maxo': 'MAXO'}
def std_curie(i):
    p, _, l = i.partition(':')
    return curie(STD.get(p.lower(), p), l)
CHEM = ('ChemicalEntity', 'Drug', 'SmallMolecule', 'MolecularMixture', 'ChemicalMixture')
def dm_type(prefix, cat):
    if prefix == 'dismech': return 'mechanism'
    if prefix == 'hgnc': return 'gene'
    if prefix == 'mondo': return 'disease'
    if prefix == 'hp': return 'phenotype'
    if prefix == 'go':
        if 'MolecularActivity' in cat: return 'molecular_function'
        if 'CellularComponent' in cat: return 'cellular_component'
        return 'biological_process'
    if prefix == 'uberon': return 'anatomy'
    if prefix == 'cl': return 'cell'
    if prefix in ('chebi', 'drugbank') or any(c in cat for c in CHEM): return 'drug'
    for key, t in [('Treatment', 'treatment'), ('Disease', 'disease'), ('PhenotypicFeature', 'phenotype'),
                   ('Gene', 'gene'), ('BiologicalProcess', 'biological_process'), ('AnatomicalEntity', 'anatomy'), ('Cell', 'cell')]:
        if key in cat: return t
    return None
dn['ntype'] = [dm_type(p, c) for p, c in zip(dn.prefix, dn.category)]
dn['curie'] = dn['id'].map(std_curie)

# ---- Translator NodeNorm (cached, bulk)
NN = 'https://nodenormalization-sri.renci.org/get_normalized_nodes'
def nodenorm(curies, cache_file):
    cache = json.load(open(cache_file)) if os.path.exists(cache_file) else {}
    todo = sorted({c for c in curies if c not in cache})
    extra = {'conflate': True, 'drug_chemical_conflate': True}
    for i in range(0, len(todo), 500):
        chunk, res = todo[i:i + 500], None
        for attempt in range(4):
            try:
                r = requests.post(NN, json={'curies': chunk, **extra}, timeout=300)
                if r.status_code in (400, 422) and 'drug_chemical_conflate' in extra:
                    extra.pop('drug_chemical_conflate'); continue
                r.raise_for_status(); res = r.json(); break
            except Exception as e:
                print('NodeNorm retry', attempt, repr(e)[:200], flush=True); time.sleep(5 * (attempt + 1))
        if res is None:
            print('NodeNorm chunk FAILED, leaving unmapped', flush=True); continue
        for c in chunk:
            v = res.get(c) or {}
            cache[c] = [e['identifier'] for e in v.get('equivalent_identifiers', [])]
        json.dump(cache, open(cache_file, 'w'))
    return cache
tick('NodeNorm')
nn_genes = nodenorm(dn.loc[dn.ntype == 'gene', 'curie'].tolist(), f'{args.cache}/nodenorm_genes.json')
nn_drugs = nodenorm(dn.loc[(dn.ntype == 'drug') & (dn.prefix != 'drugbank'), 'curie'].tolist(), f'{args.cache}/nodenorm_drugs.json')

# ---- map each dismech node onto an existing PrimeKG node, else create a new node
tick('map dismech nodes')
NEW_ONLY = {'mechanism', 'cell', 'treatment'}
def find_existing(row):
    t, c, nm = row.ntype, row.curie, str(row['name']).lower()
    if t in NEW_ONLY:
        return None, 'new_type'
    if t == 'gene':
        for x in nn_genes.get(c, []):
            if x.startswith('NCBIGene:') and x in by_curie: return by_curie[x], 'nodenorm'
        if ('gene', nm) in by_name: return by_name[('gene', nm)], 'symbol'
        return None, 'unmatched'
    if t == 'drug':
        cands = [c] if row.prefix == 'drugbank' else [x for x in nn_drugs.get(c, []) if x.startswith('DRUGBANK:')]
        for x in cands:
            if x in by_curie: return by_curie[x], 'nodenorm'
        if ('drug', nm) in by_name: return by_name[('drug', nm)], 'name'
        return None, 'unmatched'
    if c in by_curie and nodes_type_ok(by_curie[c], t): return by_curie[c], 'curie'
    if (t, nm) in by_name: return by_name[(t, nm)], 'name'
    return None, 'unmatched'
def nodes_type_ok(g, t):
    pt = PN_TYPE[g]
    return pt == t or (t in GO_TYPES and pt in GO_TYPES)

GO_TYPES = {'biological_process', 'molecular_function', 'cellular_component'}
PN_TYPE = pn.type.values
dm2g, how, new_rows, new_by_name = {}, {}, [], {}
next_g = len(pn)
dropped = dn[dn.ntype.isna()]
for row in dn[dn.ntype.notna()].itertuples(index=False):
    row = pd.Series(row._asdict())
    g, why = find_existing(row)
    key = (row.ntype, str(row['name']).lower())
    if g is None and row.ntype != 'mechanism' and key in new_by_name:
        g, why = new_by_name[key], 'merged_new_by_name'
        new_rows[g - len(pn)]['aliases'] += ('|' if new_rows[g - len(pn)]['aliases'] else '') + row.curie
        new_rows[g - len(pn)]['dm_ids'] += '|' + row.id
    elif g is None:
        g = next_g; next_g += 1
        new_rows.append(dict(idx=g, curie=row.curie, type=row.ntype, name=row['name'], origin='dismech', aliases='', alt_names='', dm_ids=row.id))
        if row.ntype != 'mechanism': new_by_name[key] = g
    dm2g[row.id] = g
    how[row.id] = why if why not in ('new_type', 'unmatched') else 'new_node'
rep('new_dismech_nodes_after_name_merge', len(new_rows))
cov = pd.DataFrame({'ntype': dn.set_index('id').loc[list(dm2g), 'ntype'].values, 'how': [how[i] for i in dm2g]})
rep('dismech_node_mapping(type -> how)', cov.groupby('ntype').how.value_counts().unstack(fill_value=0).to_dict(orient='index'))
rep('dismech_nodes_dropped_by_prefix', dropped.prefix.value_counts().to_dict())
dropped[['id', 'name', 'category', 'prefix']].assign(reason='no node type for this prefix/category').to_csv(f'{args.out}/unmapped_nodes.tsv', sep='\t', index=False)

nodes = pd.concat([pn[['idx', 'curie', 'type', 'name', 'origin', 'aliases', 'alt_names', 'dm_ids']], pd.DataFrame(new_rows)], ignore_index=True)
nodes = nodes.rename(columns={'idx': 'gidx'}).sort_values('gidx').reset_index(drop=True)
assert (nodes.gidx.values == np.arange(len(nodes))).all()
mapped_existing = defaultdict(list)
for d, g in dm2g.items():
    if g < len(pn): mapped_existing[g].append(d)
for g, ds in mapped_existing.items():
    nodes.at[g, 'dm_ids'] = '|'.join(ds)
    nodes.at[g, 'origin'] = 'both'
nodes['lidx'] = nodes.groupby('type').cumcount().astype('int64')
NT = nodes.type.values
LIDX = nodes.lidx.values
rep('nodes_by_type', nodes.type.value_counts().to_dict())
rep('nodes_by_origin', nodes.origin.value_counts().to_dict())

# ----------------------------------------------------------------------------- edges
tick('build edges')
pk_rel = kg.relation.map(san).values
E_pk = pd.DataFrame({'src': PK_MAP[kg.x_index.values], 'dst': PK_MAP[kg.y_index.values], 'rel': pk_rel, 'origin': 'primekg', 'dm_row': -1})
del kg
n0 = len(E_pk)
E_pk = E_pk[E_pk.src != E_pk.dst].drop_duplicates(['src', 'dst', 'rel'])
rep('primekg_edges_removed_by_node_merge(dups+self-loops)', n0 - len(E_pk))
s_g = de['subject'].map(dm2g)
o_g = de['object'].map(dm2g)
pred = de['predicate'].map(lambda p: 'dm_' + san(p.split(':')[-1]))
ok = s_g.notna() & o_g.notna() & (s_g != o_g)
why_drop = np.where(s_g.isna() | o_g.isna(), 'endpoint_unmapped', np.where(s_g == o_g, 'self_loop', 'kept'))
drop_tab = pd.DataFrame({'predicate': de['predicate'], 'why': why_drop})
rep('dismech_edges_kept', f'{int(ok.sum())} / {len(de)}')
rep('dismech_edges_dropped_by_predicate', drop_tab[drop_tab.why != 'kept'].groupby(['why', 'predicate']).size().sort_values(ascending=False).head(25).to_dict())
dmk = pd.DataFrame({'s': s_g[ok].astype('int64').values, 'o': o_g[ok].astype('int64').values, 'rel': pred[ok].values, 'dm_row': np.flatnonzero(ok.values)})
E_dm = pd.concat([
    pd.DataFrame({'src': dmk.s, 'dst': dmk.o, 'rel': dmk.rel, 'origin': 'dismech', 'dm_row': dmk.dm_row}),
    pd.DataFrame({'src': dmk.o, 'dst': dmk.s, 'rel': dmk.rel + '_rev', 'origin': 'dismech', 'dm_row': dmk.dm_row})])
E = pd.concat([E_pk, E_dm], ignore_index=True)
E['src_type'] = NT[E.src.values]
E['dst_type'] = NT[E.dst.values]
for c in ['rel', 'origin', 'src_type', 'dst_type']:
    E[c] = E[c].astype('category')
hubs = {san(h) for h in args.hub_relations.split(',') if h}
E['hub'] = E.rel.isin(hubs).values
rep('edges_directed_total', len(E))
rep('edges_by_origin', E.origin.value_counts().to_dict())
rep('hub_edges_removed_in_slim', int(E.hub.sum()))
rep('edge_types_full', int(E.groupby(['src_type', 'rel', 'dst_type'], observed=True).ngroups))

# ---- what does dismech add? overlap of curated pairs with PrimeKG
def pairs(mask):
    sub = E.loc[mask, ['src', 'dst']]
    return set(zip(sub.src.values.tolist(), sub.dst.values.tolist()))
ov = {}
for name_, pk_rels, ts, td in [('disease-gene', ['disease_protein'], 'disease', 'gene'),
                               ('disease-phenotype', ['disease_phenotype_positive'], 'disease', 'phenotype'),
                               ('drug-disease', ['indication', 'off_label_use'], 'drug', 'disease')]:
    pk = pairs((E.origin == 'primekg') & E.rel.isin(pk_rels) & (E.src_type == ts) & (E.dst_type == td))
    dm = pairs((E.origin == 'dismech') & (E.src_type == ts) & (E.dst_type == td)) | \
         {(b, a) for a, b in pairs((E.origin == 'dismech') & (E.src_type == td) & (E.dst_type == ts))}
    ov[name_] = dict(dismech_pairs=len(dm), also_in_primekg=len(dm & pk), new_from_dismech=len(dm - pk))
rep('dismech_vs_primekg_overlap', ov)

# ----------------------------------------------------------------------------- E1 split + hold-out
tick('E1 split')
GO_T = {'biological_process', 'molecular_function', 'cellular_component'}
dmE = E[E.origin == 'dismech']
nbrs = defaultdict(set)
for a, b in zip(dmE.src.values, dmE.dst.values):
    nbrs[a].add(b)
dis_g = sorted({g for d, g in dm2g.items() if NT[g] == 'disease'})
mech_of = {d: {x for x in nbrs[d] if NT[x] == 'mechanism'} for d in dis_g}
pk_ph = E[(E.origin == 'primekg') & (E.rel == 'disease_phenotype_positive') & (E.src_type == 'disease')]
pk_ph = pk_ph.groupby('src').dst.apply(set).to_dict()
rows = []
for d in dis_g:
    reach = nbrs[d] | {y for m in mech_of[d] for y in nbrs[m]}
    genes = sorted(x for x in reach if NT[x] == 'gene')
    gos = sorted(x for x in reach if NT[x] in GO_T)
    phen = sorted({x for x in nbrs[d] if NT[x] == 'phenotype'} | pk_ph.get(d, set()))
    rows.append(dict(gidx=d, genes=genes, go=gos, phenotypes=phen, mech=sorted(mech_of[d])))
bmk = pd.DataFrame(rows)
elig = bmk[(bmk.genes.str.len() >= 3) & (bmk.go.str.len() >= 3) & (bmk.phenotypes.str.len() >= 1)]
rep('e1_eligible_diseases', len(elig))
rng = np.random.default_rng(args.seed)
perm = rng.permutation(len(elig))
n_test = int(round(args.test_frac * len(elig)))
test = elig.iloc[perm[:n_test]]
train = elig.iloc[perm[n_test:]]
test_set = set(test.gidx)
test_mech = {m for ms in test.mech for m in ms}
src_v, dst_v, st_v, dt_v = E.src.values, E.dst.values, E.src_type.values, E.dst_type.values
touch_test = np.isin(src_v, list(test_set)) | np.isin(dst_v, list(test_set))
other_type = np.where(np.isin(src_v, list(test_set)), dt_v, st_v)
is_mech_edge = np.isin(src_v, list(test_mech)) | np.isin(dst_v, list(test_mech))
E['e1_holdout'] = (touch_test & np.isin(other_type, ['gene'] + sorted(GO_T))) | is_mech_edge
rep('e1_split', dict(train=len(train), test=len(test)))
rep('e1_heldout_directed_edges', E.groupby('origin', observed=True).e1_holdout.sum().to_dict())
cur = nodes.curie.values
def pack(df):
    return [dict(disease=cur[r.gidx], name=nodes.name[r.gidx], targets_gene=[cur[x] for x in r.genes],
                 targets_go=[cur[x] for x in r.go], seeds_phenotype=[cur[x] for x in r.phenotypes]) for r in df.itertuples()]
json.dump(dict(seed=args.seed, test_frac=args.test_frac, train=pack(train), test=pack(test),
               note='Held out for TEST diseases: all disease-gene and disease-GO edges (PrimeKG + dismech) and every edge of their dismech mechanism nodes.'),
          open(f'{args.out}/e1_split.json', 'w'), indent=1)

# ----------------------------------------------------------------------------- E4 (ccRCC) masks
cc = by_curie.get(args.ccrcc)
cc = dm2g.get(args.ccrcc.lower().replace('mondo', 'mondo'), cc) if cc is None else cc
if cc is None:
    rep('e4', f'{args.ccrcc} not found'); E['e4_dismech'] = False; E['e4_full'] = False
else:
    cc_mech = mech_of.get(cc, set())
    touch_cc = (src_v == cc) | (dst_v == cc)
    E['e4_dismech'] = ((E.origin == 'dismech').values & touch_cc) | np.isin(src_v, list(cc_mech)) | np.isin(dst_v, list(cc_mech))
    E['e4_full'] = E['e4_dismech'].values | (touch_cc & E.rel.isin(['disease_protein', 'indication', 'off_label_use', 'contraindication']).values)
    rep('e4_ccrcc_node', f'gidx {cc} | {nodes.curie[cc]} | {nodes.name[cc]} | dismech mechanism nodes: {len(cc_mech)}')
    rep('e4_masked_directed_edges', dict(dismech_only=int(E.e4_dismech.sum()), full=int(E.e4_full.sum())))

# ----------------------------------------------------------------------------- done-when checks
tick('checks')
import scipy.sparse as sp
N = len(nodes)
def adj(mask):
    s, d = E.src.values[mask], E.dst.values[mask]
    A = sp.coo_matrix((np.ones(len(s), np.float32), (s, d)), shape=(N, N)).tocsr()
    return ((A + A.T) > 0).tocsr()
gene_g = {n: g for g, n in zip(nodes.gidx[nodes.type == 'gene'], nodes.name[nodes.type == 'gene'])}
def reach(A, src, targets):
    h1 = set(A[src].indices.tolist())
    out = {}
    for t in targets:
        g = gene_g.get(t)
        if g is None: out[t] = 'not in graph'; continue
        if g in h1: out[t] = 1; continue
        out[t] = 2 if len(h1 & set(A[g].indices.tolist())) else '>2'
    return out
TGT = ['VHL', 'EPAS1', 'VEGFA', 'PBRM1', 'BAP1', 'KDR', 'HIF1A']
if cc is not None:
    slim = ~E.hub.values
    rep('done_when_ccrcc_hops[slim graph]', reach(adj(slim), cc, TGT))
    rep('ccrcc_hops[slim, E4 dismech-only masked]', reach(adj(slim & ~E.e4_dismech.values), cc, TGT))
    rep('ccrcc_hops[slim, E4 full masked]', reach(adj(slim & ~E.e4_full.values), cc, TGT))
    drugs = nodes[nodes.type == 'drug']
    bz = drugs[drugs.name.str.contains('belzutifan', case=False, na=False)]
    rep('belzutifan_nodes', bz[['gidx', 'curie', 'origin']].to_dict(orient='records'))

# ----------------------------------------------------------------------------- write tables
tick('write parquet')
nodes.to_parquet(f'{args.out}/nodes.parquet', index=False)
E.reset_index(drop=True).rename_axis('edge_row').reset_index().to_parquet(f'{args.out}/edges.parquet', index=False)
de.rename_axis('dm_row').reset_index().to_parquet(f'{args.out}/evidence.parquet', index=False)

# ----------------------------------------------------------------------------- HeteroData export
if not args.no_torch:
    tick('export HeteroData')
    import torch
    from torch_geometric.data import HeteroData
    type_counts = nodes.type.value_counts().to_dict()
    def export(mask, path):
        data = HeteroData()
        for t, n in type_counts.items():
            data[t].num_nodes = int(n)
        sub = E[mask]
        for (st, r, dt), g in sub.groupby(['src_type', 'rel', 'dst_type'], observed=True):
            data[st, r, dt].edge_index = torch.from_numpy(np.stack([LIDX[g.src.values], LIDX[g.dst.values]])).long()
            data[st, r, dt].edge_row = torch.from_numpy(g.index.values.astype(np.int64))
        torch.save(data, path)
        return data
    full = export(np.ones(len(E), bool), f'{args.out}/graph_full.pt')
    slim = export(~E.hub.values, f'{args.out}/graph_slim.pt')
    e1 = export(~E.hub.values & ~E.e1_holdout.values, f'{args.out}/graph_slim_e1.pt')
    for nm_, d_ in [('full', full), ('slim', slim), ('slim_e1', e1)]:
        rep(f'graph_{nm_}', dict(node_types=len(d_.node_types), edge_types=len(d_.edge_types), edges=int(sum(d_[et].edge_index.shape[1] for et in d_.edge_types))))
    # reload check
    chk = torch.load(f'{args.out}/graph_slim.pt', weights_only=False)
    rep('graph_slim_reload_ok', dict(nodes={t: chk[t].num_nodes for t in chk.node_types},
                                     edges_top15=dict(sorted({'|'.join(et): chk[et].edge_index.shape[1] for et in chk.edge_types}.items(), key=lambda kv: -kv[1])[:15])))
tick('DONE')
rep('runtime_s', round(time.time() - T0))
