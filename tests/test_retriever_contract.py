"""GNN-B contract checks (CONTRACTS.md sections 6-7). Skipped when the processed data isn't present (laptops)."""
import re

import pytest

from common.contracts import CCRCC_ID, P

needs_data = pytest.mark.skipif(not (P.edges.exists() and P.emb('main').exists() and P.feat_text.exists()),
                                reason='needs data/processed (Brev)')
LINE = re.compile(r'^\[E\d+\] .+ \([a-z_]+\) --[a-z0-9_]+--> .+ \([a-z_]+\)$')


@needs_data
@pytest.mark.parametrize('retriever', ['bfs', 'text_pcst', 'gnn_pcst'])
def test_subgraph_result_shape(retriever):
    from models.retriever import retrieve_subgraph
    res = retrieve_subgraph('What mechanisms explain clear cell renal cell carcinoma, which presents with hematuria?',
                            [CCRCC_ID, 'HP:0000790'], budget=20, retriever=retriever, variant='e4')
    assert 'error' not in res, res
    assert set(res) >= {'nodes', 'edges', 'edge_ids', 'triples_text', 'meta'}
    assert set(res['meta']) >= {'retriever', 'variant', 'budget', 'n_candidates', 'alpha', 'beta'}
    assert res['meta']['retriever'] == retriever and res['meta']['variant'] == 'e4'
    for n in res['nodes']:
        assert set(n) >= {'curie', 'name', 'type', 'prize'}
    assert sum(not n['is_seed'] for n in res['nodes']) <= 20
    for e in res['edges']:
        assert set(e) >= {'edge_id', 'src_curie', 'rel', 'dst_curie', 'origin'} and re.fullmatch(r'E\d+', e['edge_id'])
    assert res['edge_ids'] == [e['edge_id'] for e in res['edges']]
    lines = [l for l in res['triples_text'].split('\n') if l]
    assert len(lines) == len(res['edges']) and all(LINE.match(l) for l in lines)


@needs_data
def test_edges_exist_in_variant_and_hide_answer_key():
    import pandas as pd
    from models.retriever import retrieve_subgraph
    res = retrieve_subgraph('ccRCC mechanisms', [CCRCC_ID, 'HP:0000790'], budget=30, variant='e4')
    E = pd.read_parquet(P.edges, columns=['edge_row', 'hub', 'e4_full'])
    rows = [int(x[1:]) for x in res['edge_ids']]
    sub = E.set_index('edge_row').loc[rows]
    assert not sub.hub.any() and not sub.e4_full.any()        # nothing from the E4 answer key / hubs


@needs_data
def test_never_raises():
    from models.retriever import retrieve_subgraph
    assert 'error' in retrieve_subgraph('q', ['NOT:A_CURIE'], variant='e4')
    assert 'error' in retrieve_subgraph('q', [CCRCC_ID], retriever='nope', variant='e4')
    assert 'error' in retrieve_subgraph('q', [CCRCC_ID], variant='no_such_variant')
