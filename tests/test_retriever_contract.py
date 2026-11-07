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


@needs_data
def test_anchor_edges_add_no_nodes_and_touch_seeds():
    """anchor=k ties the tree back to the seeds with up to k extra edges; the node set (and so E1 recall) is unchanged."""
    from models.retriever import get_retriever
    R = get_retriever('e4')
    q, seeds = 'What mechanisms explain clear cell renal cell carcinoma, which presents with hematuria?', [CCRCC_ID, 'HP:0000790']
    base = R.subgraph(q, seeds, budget=30)
    anc = R.subgraph(q, seeds, budget=30, anchor=10)
    assert [n['curie'] for n in anc['nodes']] == [n['curie'] for n in base['nodes']]
    assert set(base['edge_ids']) <= set(anc['edge_ids']) and len(anc['edge_ids']) - len(base['edge_ids']) <= 10
    seed_set = {n['curie'] for n in anc['nodes'] if n['is_seed']}
    extra = [e for e in anc['edges'] if e['edge_id'] not in set(base['edge_ids'])]
    assert all(e['src_curie'] in seed_set or e['dst_curie'] in seed_set for e in extra)
    assert anc['meta']['anchor_edges'] == len(extra)


@needs_data
def test_agent_calls_get_hub_penalty_e1_default_does_not():
    from models.retriever import AGENT_HUB_PENALTY, gnn_weights, retrieve_subgraph
    q, seeds = 'ccRCC mechanisms', [CCRCC_ID, 'HP:0000790']
    default = retrieve_subgraph(q, seeds, budget=20, variant='e4')
    agent = retrieve_subgraph(q, seeds, budget=20, variant='e4', target_types=('gene', 'drug', 'phenotype', 'pathway'))
    assert default['meta']['hub'] == gnn_weights().get('hub', 0.0)
    assert agent['meta']['hub'] == AGENT_HUB_PENALTY and agent['meta']['per_type']
    assert retrieve_subgraph(q, seeds, budget=20, variant='e4', target_types=('gene', 'drug'), hub=0.0)['meta']['hub'] == 0.0


@needs_data
def test_adaptive_budget_none_self_sizes_under_ceiling():
    """CONTRACTS.md section 6 handshake: ADAPTIVE_BUDGET = True, budget=None -> the retriever sizes the subgraph."""
    import models.retriever as rmod
    assert rmod.ADAPTIVE_BUDGET is True
    res = rmod.retrieve_subgraph('ccRCC mechanisms', [CCRCC_ID, 'HP:0000790'], budget=None, variant='e4',
                                 target_types=('gene', 'drug', 'phenotype', 'pathway'))
    assert 'error' not in res, res
    n = sum(not x['is_seed'] for x in res['nodes'])
    assert 0 < n <= rmod.ADAPTIVE_MAX_NODES and res['meta']['adaptive_budget'] and res['meta']['n_nonseed'] == n
