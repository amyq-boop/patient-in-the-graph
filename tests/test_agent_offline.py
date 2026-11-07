"""Offline tests for the agent workstream: mock tools + FakeLLM, no network, no GPU.

    python -m pytest tests/test_agent_offline.py -q
"""
import json
import os

os.environ["BIOHACK_MOCK"] = "1"
os.environ["BIOHACK_FAKE_LLM"] = "1"
os.environ.setdefault("BIOHACK_VARIANT", "full")

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from agent import config as C  # noqa: E402
from agent import tools as T  # noqa: E402
from agent.graph_agent import protein_phenotype_connected, run  # noqa: E402
from agent.llm import FakeLLM, _parse_text_tool_call  # noqa: E402
from agent.trace_check import check  # noqa: E402
from agent.verifier import cited_ids, verify  # noqa: E402
from common.validate import FAIL, check_trace  # noqa: E402

Q = "Why is this patient high-risk, and what would you target?"
PID = "C3L-MOCK1"


def test_tools_never_raise():
    assert "error" in T.dispatch("nope", {})
    assert "error" in T.dispatch("get_patient", {"patient_id": "C3L-99999"})
    assert "error" in T.dispatch("get_patient", {"wrong": 1})


def test_cited_ids():
    s = "x [E12, E7] y [PMID: 123] z [pt:C3L-1:protein_up:NCBIGene:7422] w [NCBIGene:5]"
    assert cited_ids(s) == ["E12", "E7", "PMID:123", "pt:C3L-1:protein_up:NCBIGene:7422"]


def test_verifier_support_rule():
    draft = ("- VHL drives ccRCC [E1].\n- Made-up fact here [E999].\n- BAP1 is bad for patients.\n"
             "- Hypothesis: CA9 predicts response.\n- HIF-2a is stabilized [PMID:7].\n"
             "- The patient overexpresses VEGFA [pt:X:protein_up:NCBIGene:7422].")
    ev = {"edge_ids": ["E1", "pt:X:protein_up:NCBIGene:7422"], "pmids": ["PMID:7"]}
    claims, answer, stats, _ = verify(draft, ev, FakeLLM())
    st = {c["text"].split()[0]: c["status"] for c in claims}
    assert st == {"VHL": "supported", "Made-up": "removed", "BAP1": "removed", "Hypothesis:": "hypothesis",
                  "HIF-2a": "supported", "The": "supported"}
    assert "Made-up" not in answer and "BAP1" not in answer and "VHL drives" in answer
    assert (stats["n_claims"], stats["n_supported"], stats["n_hypothesis"], stats["n_removed"]) == (6, 3, 1, 2)
    assert next(c for c in claims if c["text"].startswith("Made-up"))["unmatched_ids"] == ["E999"]


def test_entity_match_and_strict_mode():
    draft = "- VHL contributes to ccRCC [E1].\n- PBRM1 doubles the risk [E1]."
    names = {"E1": ["VHL", "clear cell renal carcinoma"]}
    ev = {"edge_ids": ["E1"], "pmids": []}
    claims, _, stats, _ = verify(draft, ev, FakeLLM(), edge_names=names)
    assert [c["entity_match"] for c in claims] == [True, False] and stats["n_supported"] == 2
    claims, answer, _, _ = verify(draft, ev, FakeLLM(), edge_names=names, strict=True)
    assert [c["status"] for c in claims] == ["supported", "removed"] and "PBRM1" not in answer


def test_agent_end_to_end_patient():
    t = run(Q, patient_id=PID, qid="P01")
    assert check(t) == [], check(t)
    n = len(FAIL)
    check_trace(t, "P01")  # the team validator agrees
    assert len(FAIL) == n
    names = [tc["name"] for tc in t["tool_calls"]]
    assert {"get_patient", "predict_patient", "retrieve_subgraph", "get_mechanism"} <= set(names)
    assert t["verifier"]["n_supported"] == 3  # graph edge, patient edge, PMID
    assert any(c["status"] == "removed" and "E999999999" in c["support"]["edge_ids"] for c in t["claims"])
    assert "E999999999" not in t["answer"]
    assert any("MOCK" in x for x in t["notes"])
    # mocks never link proteins to phenotypes, so the controller retried retrieval, at most 3 times
    assert 1 <= sum(tc["by"] == "controller" for tc in t["tool_calls"]) <= 3


def test_agent_disease_question_and_llm_only():
    t = run("What mechanisms explain clear cell renal carcinoma?", qid="D01")
    assert check(t) == [] and t["evidence"]["edge_ids"] and t["evidence"]["pmids"]
    b = run(Q, patient_id=PID, qid="P01", mode="llm_only")
    assert check(b) == [] and b["tool_calls"] == [] and b["verifier"]["n_supported"] == 0


def test_path_check():
    pat = {"phenotypes": [{"curie": "HP:1"}], "proteins_up": [{"curie": "G:1"}], "proteins_down": []}
    linked = {"name": "retrieve_subgraph", "result": {"edges": [
        {"src_curie": "G:1", "dst_curie": "X:1"}, {"src_curie": "X:1", "dst_curie": "HP:1"}]}}
    via_patient = {"name": "retrieve_subgraph", "result": {"edges": [
        {"src_curie": "PATIENT:p", "dst_curie": "G:1"}, {"src_curie": "PATIENT:p", "dst_curie": "HP:1"}]}}
    assert protein_phenotype_connected([linked], pat)
    assert not protein_phenotype_connected([via_patient], pat)


def test_text_tool_call_parsing():
    c = _parse_text_tool_call('<|python_tag|>{"name": "get_patient", "parameters": {"patient_id": "C3L-00004"}}')
    assert c[0]["name"] == "get_patient" and c[0]["arguments"]["patient_id"] == "C3L-00004"
    assert _parse_text_tool_call("VHL loss drives ccRCC.") is None


@pytest.fixture
def tiny_processed(tmp_path, monkeypatch):
    """WS1-shaped nodes/edges/evidence + WS2/GNN-shaped patient tables, in a temp dir."""
    from common import contracts as K

    proc = tmp_path / "processed"
    (proc / "patients").mkdir(parents=True)
    (proc / "gnn_a").mkdir()
    pd.DataFrame({"gidx": [0, 1, 2, 3, 4], "curie": ["NCBIGene:7428", "HP:0000822", "MONDO:0005005",
                                                     "dismech:M1", "dismech:M2"],
                  "type": ["gene", "phenotype", "disease", "mechanism", "mechanism"],
                  "name": ["VHL", "Hypertension", "clear cell renal carcinoma", "VHL loss", "HIF-2a stabilization"],
                  "alt_names": ["", "high blood pressure", "ccRCC", "", ""],
                  "aliases": ["HGNC:12687", "", "", "", ""]}).to_parquet(proc / "nodes.parquet")
    pd.DataFrame({"edge_row": [0, 1, 2, 3], "src": [3, 3, 4, 0], "dst": [2, 4, 3, 2],
                  "src_type": ["mechanism", "mechanism", "mechanism", "gene"],
                  "rel": ["dm_affects", "dm_causes", "dm_causes_rev", "dm_contributes_to"],
                  "dst_type": ["disease", "mechanism", "mechanism", "disease"],
                  "origin": ["dismech"] * 4, "dm_row": [0, 1, 1, 2]}).to_parquet(proc / "edges.parquet")
    pd.DataFrame({"dm_row": [0, 1, 2], "publications": ["PMID:111|PMID:222", "PMID:333", ""],
                  "supporting_text": ["VHL is lost early.", "VHL loss stabilizes HIF-2a.", ""]}).to_parquet(
        proc / "evidence.parquet")
    pd.DataFrame({"patient_id": ["C3L-1"], "curie": ["PATIENT:C3L-1"], "age": [60], "sex": ["F"],
                  "clinical_summary": ["60F, hypertension."]}).to_parquet(proc / "patients" / "patients.parquet")
    pd.DataFrame({"patient_id": ["C3L-1"], "curie": ["HP:0000822"], "source_field": ["medical_condition"],
                  "source_text": ["Hypertension"]}).to_parquet(proc / "patients" / "patient_phenotypes.parquet")
    pd.DataFrame({"patient_id": ["C3L-1"], "curie": ["NCBIGene:7428"], "symbol": ["VHL"], "z": [-3.1],
                  "direction": ["down"], "rank": [1], "z_ref": ["normal"]}).to_parquet(
        proc / "patients" / "patient_proteins.parquet")
    pd.DataFrame({"patient_id": ["C3L-1"], "model": ["gnn"], "fold": [3], "risk": [0.7]}).to_parquet(
        proc / "gnn_a" / "predictions.parquet")

    for attr, val in [("nodes", proc / "nodes.parquet"), ("edges", proc / "edges.parquet"),
                      ("evidence", proc / "evidence.parquet"), ("processed", proc),
                      ("patients", proc / "patients" / "patients.parquet"),
                      ("patient_phenotypes", proc / "patients" / "patient_phenotypes.parquet"),
                      ("patient_proteins", proc / "patients" / "patient_proteins.parquet"),
                      ("predictions", proc / "gnn_a" / "predictions.parquet")]:
        monkeypatch.setattr(K.P, attr, val)
    monkeypatch.setattr(C, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(C, "MECHANISMS", proc / "mechanisms.jsonl")
    monkeypatch.setattr(C, "CONTRIBUTIONS", proc / "gnn_a" / "contributions.parquet")
    monkeypatch.setattr(T, "_nameres", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    caches = (T._nodes, T._patient_tables, T._predictions, T._dm_tables, T._mech_file, T._gnn_a)
    for f in caches:
        f.cache_clear()
    yield proc
    for f in caches:
        f.cache_clear()


def test_real_tools_on_contract_files(tiny_processed, monkeypatch):
    monkeypatch.setattr(C, "MOCK_MODE", "0")
    p = T.dispatch("get_patient", {"patient_id": "PATIENT:C3L-1"})
    assert p["curie"] == "PATIENT:C3L-1" and p["phenotypes"][0]["name"] == "Hypertension"
    assert p["proteins_down"][0]["edge_id"] == "pt:C3L-1:protein_down:NCBIGene:7428"
    assert not {"y_stage", "stage", "grade"} & set(p)
    pr = T.dispatch("predict_patient", {"patient_id": "C3L-1"})
    assert pr["risk"] == 0.7 and pr["fold"] == 3 and pr["label"] == "y_stage"
    r = T.dispatch("resolve_terms", {"texts": ["high blood pressure", "HGNC:12687", "ccrcc"]})["results"]
    assert [x["matches"][0]["curie"] for x in r] == ["HP:0000822", "NCBIGene:7428", "MONDO:0005005"]
    assert r[0]["matches"][0]["type"] == "phenotype" and "warning" in r[0]
    m = T.dispatch("get_mechanism", {"disease_curie": "MONDO:0005005"})
    steps = m["chains"][0]["steps"]
    assert [s["label"] for s in steps] == ["VHL loss", "HIF-2a stabilization"]
    assert steps[0]["pmids"] == ["PMID:111", "PMID:222"] and steps[0]["edge_id"] == "E0"
    assert steps[1]["pmids"] == ["PMID:333"] and steps[1]["edge_id"] == "E1"
    assert m["genes"] == ["NCBIGene:7428"]
    assert "error" in T.dispatch("retrieve_subgraph", {"question": "q", "seeds": ["HP:0000822"]})


def test_auto_mode_falls_back_to_mocks(tiny_processed, monkeypatch):
    monkeypatch.setattr(C, "MOCK_MODE", "auto")
    fb = T.dispatch("retrieve_subgraph", {"question": "q", "seeds": ["PATIENT:C3L-1"]})
    assert fb.get("_fallback") and fb["edge_ids"]
    assert "error" not in T.dispatch("get_patient", {"patient_id": "C3L-1"})  # real file exists: no fallback
    assert json.dumps(T.dispatch("get_patient", {"patient_id": "C3L-1"}))
