"""Fast offline tests for the real-workflow adapter, using injected fake boundary tools.

The UI never injects fake tools; it calls the repository's real retriever/verifier.
"""
import re

import pytest

from app.external_reasoner import (ExternalReasoningError, available_markers,
                                   edge_summary, run_case)


def example_case():
    return {
        "release": "test", "sha256": {"fixture": "abc"},
        "findings": [{"finding_id": "M-FINDING-007", "sample_id": "M-SAMPLE-003",
                      "assay_id": "M-ASSAY-011", "feature": "CEACAM5 IHC H-score",
                      "value": 300, "unit": None,
                      "evidence_class": "previous_research_report_extraction",
                      "current_node_status": "not_assessed_here", "source_ids": ["M-SOURCE-003"]}],
        "samples": [{"sample_id": "M-SAMPLE-003", "label": "Liver metastasis resection specimen",
                     "collection_date": "2024-10-22", "source_ids": ["M-SOURCE-003"]}],
        "assays": [{"assay_id": "M-ASSAY-011", "label": "Valius IHC",
                    "source_ids": ["M-SOURCE-003"]}],
        "sources": [{"source_id": "M-SOURCE-003", "label": "Valius IHC report"}],
    }


def graph_fixture(**changes):
    g = {
        "meta": {"retriever": "gnn_pcst", "variant": "full", "mock": False},
        "nodes": [{"curie": "NCBIGene:1048", "name": "CEACAM5"},
                  {"curie": "NCBIGene:4233", "name": "MET"}],
        "edges": [{"edge_id": "E42", "src_curie": "NCBIGene:1048",
                   "rel": "protein_protein", "dst_curie": "NCBIGene:4233", "origin": "primekg"}],
        "edge_ids": ["E42"],
    }
    g.update(changes)
    return g


def fake_verifier(draft, evidence, llm, edge_names, strict):
    assert strict is True
    assert "pt:youngcrc:reported_ihc:NCBIGene:1048" in evidence["edge_ids"]
    assert "E42" in evidence["edge_ids"]
    assert edge_names["E42"][0] == "CEACAM5"
    removed = "This archival marker proves a drug will cure the patient's current metastases."
    assert removed in draft
    return ([{"text": removed, "status": "removed"}], draft.replace(removed, ""),
            {"n_supported": 2, "n_removed": 1}, "deterministic")


def test_marker_has_exact_specimen_and_id():
    f = available_markers(example_case())
    assert len(f) == 1
    assert f[0]["curie"] == "NCBIGene:1048"
    assert f[0]["sample_date"] == "2024-10-22"
    assert f[0]["evidence_id"].startswith("pt:youngcrc:")


def test_live_retrieval_wires_seed_and_verifier():
    def fake_gnn(**kwargs):
        assert kwargs["retriever"] == "gnn_pcst"
        assert kwargs["seeds"] == ["NCBIGene:1048"]
        return graph_fixture()
    out = run_case(example_case(), "M-FINDING-007", retriever=fake_gnn,
                   verifier=fake_verifier, use_llm=False)
    assert out["n_graph_edges"] == 1
    assert out["verifier"]["n_removed"] == 1
    assert "drug will cure" not in out["answer"]
    assert out["source_ids"] == ["M-SOURCE-003"]


def test_mock_graph_is_rejected():
    with pytest.raises(ExternalReasoningError, match="mock retrieval"):
        run_case(example_case(), "M-FINDING-007", retriever=lambda **kw: graph_fixture(
            meta={"retriever": "gnn_pcst", "variant": "full", "mock": True}),
            verifier=fake_verifier)


def test_unlinked_findings_are_not_usable():
    case = example_case()
    case["sources"] = []
    assert available_markers(case) == []


def test_inconsistent_graph_edge_ids_are_rejected():
    with pytest.raises(ExternalReasoningError, match="inconsistent"):
        run_case(example_case(), "M-FINDING-007", retriever=lambda **kw: graph_fixture(
            edge_ids=["E44"]), verifier=fake_verifier)


def test_edge_order_prioritizes_measured_seed():
    g = graph_fixture()
    g["edges"].insert(0, {"edge_id":"E1", "src_curie":"NCBIGene:4233",
                           "dst_curie":"NCBIGene:999", "rel":"protein_protein"})
    assert edge_summary(g,"NCBIGene:1048")[0]["edge_id"] == "E42"


def test_source_only_path_requires_no_graph():
    from app.external_reasoner import run_source_check
    def verify_stub(draft, evidence, llm, edge_names, strict):
        assert evidence["edge_ids"] == ["pt:youngcrc:reported_ihc:NCBIGene:1048"]
        assert llm is None
        return ([], draft.split("### Deliberately injected")[0].strip(),
                {"n_supported": 1, "n_removed": 1}, "deterministic")
    out = run_source_check(example_case(), "M-FINDING-007", verifier=verify_stub)
    assert out["n_graph_edges"] == 0
    assert "no GNN" in out["mode"]
    assert out["verifier"]["n_removed"] == 1


def test_real_repo_verifier_when_available():
    import importlib.util
    if importlib.util.find_spec("agent") is None:
        pytest.skip("Full agent package is available in the user's repository")
    from app.external_reasoner import run_source_check
    out = run_source_check(example_case(), "M-FINDING-007")
    assert out["verifier"]["n_supported"] >= 1
    assert out["verifier"]["n_removed"] == 1
    assert "drug will cure" not in out["answer"]
