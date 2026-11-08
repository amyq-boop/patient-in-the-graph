"""Offline tests for the guided tour (app/tour). No network, no graph assets, no API key."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from app.external_case import BASE_URL, PublicCaseError
from app.tour import case_data, investigate, results
from app.tour.graph_index import GraphIndex


def _refuse(url):
    raise OSError("network disabled in tests")


@pytest.fixture(scope="module")
def case():
    return case_data.load(prefer_live=True, live_fetch=_refuse)


def test_falls_back_to_hash_verified_snapshot(case):
    assert case["data_source"] == "snapshot"
    assert case["load_errors"] and case["load_errors"][0].startswith("live:")
    assert case["release"] == "1.53"
    assert len(case["sha256"]) == 7 and len(case["sha256_extra"]) == 3


def test_tampered_snapshot_is_rejected(monkeypatch):
    real = case_data.snapshot_fetch

    def tampered(url):
        data = real(url)
        return data + b" " if url.endswith("findings.json") else data

    monkeypatch.setattr(case_data, "snapshot_fetch", tampered)
    with pytest.raises(PublicCaseError, match="mismatch"):
        case_data.load(prefer_live=False)


def test_snapshot_refuses_paths_outside_it():
    with pytest.raises(PublicCaseError):
        case_data.snapshot_fetch(BASE_URL + "../../common/contracts.py")


def test_patient_payload_keeps_specimens_and_sources(case):
    p = case_data.patient_payload(case)
    ihc = [f for f in p["findings"] if f["feature"] == "CEACAM5 IHC H-score"][0]
    assert ihc["sample_id"] == "M-SAMPLE-003" and ihc["sample_date"] == "2024-10-22"
    assert ihc["current_node_status"] == "not_assessed_here"
    assert all(s["resolved"] for s in ihc["sources"])
    assert all(v["sample_id"] is None for v in p["variants"])  # the release attaches no specimen to these loci
    assert p["proteomics"]["sample_id"] == "M-SAMPLE-010"
    assert "not matched-normal fold changes" in p["proteomics"]["caveat"]


def test_gene_symbol_parsing():
    assert case_data.gene_symbol("APC_S1411fs") == "APC"
    assert case_data.gene_symbol("SOX9;SOX9-AS1") == "SOX9"
    assert case_data.gene_symbol("LINC00574;LINC00574-LOC102724511") is None


def test_graph_index_uses_only_real_edges():
    g = GraphIndex()
    assert g.describe()["source"] in ("executed_retrievals", "full_graph")
    assert not any(str(k).startswith("E9000") for k in g.edges)  # mock IDs never enter the index
    for e in g.edges.values():
        assert (e["edge_id"] is not None) == e["citable"]  # kg.csv extract rows are never offered as citations
    st = g.stage_graph([g.resolve("MET")])
    ids = {n["curie"] for n in st["nodes"]}
    assert all(e["src"] in ids and e["dst"] in ids for e in st["edges"])


def test_results_are_scoped_to_ccrcc():
    b = results.bundle()
    assert "not colorectal" in b["scope"]
    assert b["e2"] is None or "No GNN variant beats" in b["e2"]["honest_readout"]


def test_live_run_refuses_without_graph_assets(case, monkeypatch):
    monkeypatch.setattr(investigate, "capabilities",
                        lambda: {"gnn_ready": False, "missing_files": ["x"], "missing_packages": [], "llm_ready": False})
    r = investigate.run(case, "M-FINDING-007", "", False, True)
    assert r["ok"] is False and r["reason"] == "gnn_unavailable" and "trace" not in r


def test_source_check_uses_real_verifier_and_removes_false_claim(case):
    r = investigate.source_check(case, "M-FINDING-007", inject=True)
    assert r["ok"]
    v = investigate.view(r["trace"])
    kinds = [c["kind"] for c in v["claims"]]
    assert "supported" in kinds and "removed" in kinds
    assert v["claims"][0]["cited_ids"] == ["pt:youngcrc:reported_ihc:NCBIGene:1048"]
    md = investigate.review_markdown(r["trace"], case_data.patient_payload(case))
    for section in ("Observed (source-linked) finding", "Verifier-supported statements", "What this review does not conclude"):
        assert section in md
    assert "kidney-cancer stage model was not run" in md
    assert "—" not in md  # no em dashes in generated prose


def test_saved_trace_loader_rejects_fake_gnn_and_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(investigate, "TRACE_DIR", tmp_path)
    fake = {"case_id": "youngcrc", "graph_backend": "gnn_pcst", "graph_meta": {"retriever": "gnn_pcst", "variant": "full", "mock": True}}
    (tmp_path / "x__gnn__1.json").write_text(json.dumps(fake))
    assert investigate.load_saved("x__gnn__1.json") == {"ok": False, "reason": "not_a_real_run"}
    assert investigate.saved()[0]["real_gnn"] is False
    assert investigate.load_saved("../../CLAUDE.md")["ok"] is False


def test_server_endpoints(case, monkeypatch):
    from app.tour import server
    monkeypatch.setattr(case_data, "_cached", case)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    try:
        boot = json.load(urllib.request.urlopen(base + "/api/bootstrap", timeout=60))
        assert boot["patient"]["release"] == "1.53" and boot["stage"]["nodes"] and boot["markers"]
        assert {m["kind"] for m in boot["mapping"]} >= {"ihc", "variant", "proteomics"}
        html = urllib.request.urlopen(base + "/", timeout=10).read().decode()
        assert "Patient-in-the-Graph" in html
        req = urllib.request.Request(base + "/api/source_check", data=b'{"finding_id": "M-FINDING-008"}', method="POST")
        r = json.load(urllib.request.urlopen(req, timeout=30))
        assert r["ok"] and r["claims"]
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(base + "/%2e%2e/%2e%2e/CLAUDE.md", timeout=10)
    finally:
        httpd.shutdown()
