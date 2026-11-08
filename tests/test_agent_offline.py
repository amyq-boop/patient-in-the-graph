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


def test_verifier_skips_headers_and_keeps_non_claims():
    """Headers are not claims; caveats, abstentions and experiments are kept but not scored."""
    draft = ("**Summary**\nVHL drives ccRCC [E1].\n\n**Mechanism** (dismech chains)\n- HIF-2a is stabilized [PMID:7].\n\n"
             "**Testable hypothesis**\nHypothesis: CA9 predicts response.\nExperiment: Knock down CA9 in 786-O cells and compare growth.\n\n"
             "**Caveats**\n- Germline VHL status cannot be determined from tumor proteomics.\n- Risk is a model estimate.\n"
             "- Made-up fact outside the caveats here.")
    claims, answer, stats, _ = verify(draft, {"edge_ids": ["E1"], "pmids": ["PMID:7"]}, FakeLLM())
    texts = [c["text"] for c in claims]
    assert not any("Summary" in x or "Mechanism" in x or "Caveats" in x for x in texts)
    assert {n["reason"] for n in stats["non_claims"]} == {"experiment", "caveat"}
    assert stats["n_non_claims"] == 4 and stats["n_supported"] == 2 and stats["n_hypothesis"] == 1
    assert "cannot be determined" in answer and "Experiment:" in answer and "**Caveats**" in answer
    assert check({"contract_version": "1", "run_id": "r", "qid": "q", "mode": "agent", "patient_id": None,
                  "question": "q", "model": "m", "tool_calls": [], "evidence": {"edge_ids": ["E1"], "pmids": ["PMID:7"]},
                  "draft": draft, "claims": claims, "answer": answer, "verifier": stats}) == []


def test_verifier_abstention_outside_caveats_is_kept():
    draft = "- Whether this patient will respond to checkpoint inhibitors cannot be determined from these data."
    claims, answer, stats, _ = verify(draft, {"edge_ids": [], "pmids": []}, FakeLLM())
    assert not claims and stats["non_claims"][0]["reason"] == "abstention" and "cannot be determined" in answer


def test_model_outputs_are_citable():
    tr = run(Q, patient_id=PID, llm=FakeLLM(), run_id="test_model_ids")
    ev = set(tr["evidence"]["edge_ids"])
    assert f"pred:{PID}:gnn" in ev and any(x.startswith(f"contrib:{PID}:") for x in ev)
    draft = f"- The GNN risk for this patient is 0.78 [pred:{PID}:gnn].\n- VEGFA contributes most [contrib:{PID}:NCBIGene:7422]."
    claims, _, stats, _ = verify(draft, tr["evidence"], FakeLLM())
    assert [c["status"] for c in claims] == ["supported", "supported"] and stats["n_supported_model"] == 2


def test_entity_match_and_strict_mode():
    draft = "- VHL contributes to ccRCC [E1].\n- PBRM1 doubles the risk [E1]."
    names = {"E1": ["VHL", "clear cell renal carcinoma"]}
    ev = {"edge_ids": ["E1"], "pmids": []}
    claims, _, stats, _ = verify(draft, ev, FakeLLM(), edge_names=names)
    assert [c["entity_match"] for c in claims] == [True, False] and stats["n_supported"] == 2
    claims, answer, _, _ = verify(draft, ev, FakeLLM(), edge_names=names, strict=True)
    assert [c["status"] for c in claims] == ["supported", "removed"] and "PBRM1" not in answer


def test_mislabelled_citation_is_caught():
    """'Lenvatinib [<axitinib edge>]' must not pass in strict mode; long mechanism labels still match a paraphrase."""
    names = {"E58": ["Sunitinib", "DRUGBANK:DB01268"], "E60": ["Axitinib", "DRUGBANK:DB06626"],
             "E83": ["Immune Evasion via PD-L1 and Immunosuppressive Microenvironment", "dismech:x"]}
    ev = {"edge_ids": list(names), "pmids": []}
    draft = "- Sunitinib and lenvatinib treat ccRCC [E58, E60].\n- Immune evasion in ccRCC occurs via PD-L1 [E83]."
    claims, _, stats, _ = verify(draft, ev, FakeLLM(), edge_names=names)
    assert claims[0]["mismatched_ids"] == ["E60"] and claims[0]["status"] == "supported"  # recorded, not removed
    assert claims[1]["entity_match"] is True and claims[1]["mismatched_ids"] == []
    assert stats["n_claims_mismatched"] == 1
    claims, _, _, _ = verify(draft, ev, FakeLLM(), edge_names=names, strict=True)
    assert [c["status"] for c in claims] == ["removed", "supported"]


def test_subgraph_view_ranks_and_reports_cut():
    from agent import graph_agent as G
    res = {"nodes": [{"curie": "A", "prize": 0.1}, {"curie": "B", "prize": 0.9}, {"curie": "S", "prize": 0, "is_seed": True}],
           "edges": [{"edge_id": "E1", "src_curie": "A", "dst_curie": "A"}, {"edge_id": "E2", "src_curie": "B", "dst_curie": "A"},
                     {"edge_id": "E3", "src_curie": "S", "dst_curie": "A"}],
           "triples_text": "[E1] a --r--> a\n[E2] b --r--> a\n[E3] s --r--> a"}
    assert [l[:4] for l in G._ranked_lines(res)] == ["[E3]", "[E2]", "[E1]"]  # seed edge first, then by prize
    text, n = G._fit(G._ranked_lines(res), 35)
    assert n == 2 and text.endswith("1 lower-ranked edges not shown")
    assert G._ranked_lines(res, skip={"E3"})[0].startswith("[E2]")


def test_trace_logs_llm_calls_and_subgraph_sizes():
    t = run(Q, patient_id=PID, qid="P01")
    assert t["subgraph_sizes"] and all("n_nodes" in s for s in t["subgraph_sizes"])
    assert isinstance(t["llm_calls"], list)
    ctrl = [tc for tc in t["tool_calls"] if tc["by"] == "controller"]
    assert all(tc["args"]["budget"] <= max(C.RETRY_BUDGET_MAX, C.DEFAULT_BUDGET) for tc in ctrl)  # no x1.5 blow-up


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
                  "origin": ["dismech"] * 4, "dm_row": [0, 1, 1, 2],
                  # every edge here is ccRCC's own dismech edge, so the e4 variants hide all of them
                  "hub": [False] * 4, "e1_holdout": [False] * 4,
                  "e4_dismech": [True] * 4, "e4_full": [True] * 4}).to_parquet(proc / "edges.parquet")
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
    pd.DataFrame({"patient_id": ["C3L-1", "C3L-1"], "curie": ["NCBIGene:7428", "NCBIGene:7428"], "name": ["VHL", "VHL"],
                  "source_field": ["somatic_mutation"] * 2, "source_text": ["p.R167Q", "p.L85P"], "weight": [1.0, 1.0]}).to_parquet(
        proc / "patients" / "patient_mutations.parquet")
    monkeypatch.setattr(K.P, "patient_mutations", proc / "patients" / "patient_mutations.parquet")
    monkeypatch.setattr(K.P, "patient_drugs", proc / "patients" / "patient_drugs.parquet")  # missing on purpose
    caches = (T._nodes, T._patient_tables, T._predictions, T._dm_tables, T._mech_file, T._gnn_a, T._patient_extras,
              T._cohort_scores, T._approved_drugs)
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
    assert {"tumor_drivers", "clinical_drivers", "driver_split"} <= set(pr)
    assert all(x["driver"] == "tumor" for x in pr["tumor_drivers"])
    assert all(x["driver"] == "clinical" and x["curie"].startswith("HP:") for x in pr["clinical_drivers"])
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


def test_get_mechanism_respects_graph_variant(tiny_processed, monkeypatch):
    """E4 hides ccRCC's dismech edges from retrieval; get_mechanism must not hand them back (answer-key leak)."""
    monkeypatch.setattr(C, "MOCK_MODE", "0")
    for variant in ("e4", "e4_dismech", "no_dismech"):
        monkeypatch.setattr(C, "VARIANT", variant)
        m = T.dispatch("get_mechanism", {"disease_curie": "MONDO:0005005"})
        assert "error" in m and not m.get("chains"), variant
    monkeypatch.setattr(C, "MECHANISMS", tiny_processed / "mechanisms.jsonl")
    (tiny_processed / "mechanisms.jsonl").write_text(json.dumps(
        {"disease": "MONDO:0005005", "name": "x", "chains": [{"chain_id": "c", "steps": []}], "genes": [], "drugs": []}))
    T._mech_file.cache_clear()
    monkeypatch.setattr(C, "VARIANT", "e4")
    assert "error" in T.dispatch("get_mechanism", {"disease_curie": "MONDO:0005005"})  # precomputed file ignored off full
    monkeypatch.setattr(C, "VARIANT", "full")
    assert T.dispatch("get_mechanism", {"disease_curie": "MONDO:0005005"})["chains"]


def test_rank_for_patient_uses_tumor_proteins_by_default(tiny_processed, monkeypatch):
    seen = {}

    class FakeGnnA:
        def rank_for_patient_rel(self, neigh, node_type, k=10, weights=None):
            seen["neigh"] = list(neigh)
            return pd.DataFrame({"curie": ["NCBIGene:7428"], "name": ["VHL"], "score": [0.9]})

    monkeypatch.setattr(C, "MOCK_MODE", "0")
    monkeypatch.setattr(T, "_gnn_a", lambda tag: FakeGnnA())
    monkeypatch.setattr(C, "RANK_BASIS", "tumor")
    r = T.dispatch("rank_for_patient", {"patient_id": "C3L-1", "node_type": "gene"})
    assert r["basis"] == "tumor_proteins" and seen["neigh"] == ["NCBIGene:7428"]  # no HP:0000822 (hypertension)
    monkeypatch.setattr(C, "RANK_BASIS", "all")
    T.dispatch("rank_for_patient", {"patient_id": "C3L-1", "node_type": "gene"})
    assert seen["neigh"] == ["HP:0000822", "NCBIGene:7428"]


def test_get_patient_mutations_and_medications(tiny_processed, monkeypatch):
    monkeypatch.setattr(C, "MOCK_MODE", "0")
    p = T.dispatch("get_patient", {"patient_id": "C3L-1"})
    assert p["mutations"] == [{"curie": "NCBIGene:7428", "symbol": "VHL", "type": "somatic", "protein_change": "p.R167Q, p.L85P",
                               "edge_id": "pt:C3L-1:somatic_mutation:NCBIGene:7428"}]
    assert p["medications"] == []  # the medications file is optional
    monkeypatch.setattr(C, "MOCK_MODE", "1")  # the mock patient carries a VHL mutation too
    tr = run("Which of this patient's genes are mutated?", patient_id=PID, llm=FakeLLM(), run_id="t_mut")
    assert f"pt:{PID}:somatic_mutation:NCBIGene:7428" in tr["evidence"]["edge_ids"]  # citable (mock patient)


def test_auto_mode_falls_back_to_mocks(tiny_processed, monkeypatch):
    monkeypatch.setattr(C, "MOCK_MODE", "auto")
    fb = T.dispatch("retrieve_subgraph", {"question": "q", "seeds": ["PATIENT:C3L-1"]})
    assert fb.get("_fallback") and fb["edge_ids"]
    assert "error" not in T.dispatch("get_patient", {"patient_id": "C3L-1"})  # real file exists: no fallback
    assert json.dumps(T.dispatch("get_patient", {"patient_id": "C3L-1"}))


def test_driver_split_and_view():
    from common.contracts import driver_split
    from agent.graph_agent import _llm_view
    top = [{"curie": "HP:0000822", "name": "Hypertension", "type": "phenotype", "score": 2.0,
            "evidence_id": "contrib:X:HP:0000822", "driver": "clinical"},
           {"curie": "NCBIGene:7428", "name": "VHL", "type": "gene", "score": 6.0,
            "evidence_id": "contrib:X:NCBIGene:7428", "driver": "tumor"}]
    s = driver_split(top)
    assert s["driver_split"]["tumor_share"] == 0.75 and s["driver_split"]["clinical_share"] == 0.25
    assert [x["name"] for x in s["tumor_drivers"]] == ["VHL"]
    assert driver_split([]) == {"tumor_drivers": [], "clinical_drivers": [], "driver_split": {}}
    v = _llm_view("predict_patient", {"patient_id": "X", "risk": 0.5, "evidence_id": "pred:X:gnn",
                                      "top_contributing": top, **s})
    assert "[contrib:X:NCBIGene:7428] VHL" in v and "top_contributing" not in v and "clinical_drivers" in v


def test_rank_drugs_cohort_relative(tiny_processed, monkeypatch):
    """A hub drug that scores high for everyone drops below a drug specific to this patient; unapproved drugs are out."""
    import numpy as np
    monkeypatch.setattr(C, "MOCK_MODE", "0")
    monkeypatch.setattr(C, "RANK_DRUGS", "cohort")

    class Fake:
        gidx_by_type = {"drug": np.array([5, 6, 7, 8])}
        nodes = pd.DataFrame({"gidx": range(9), "curie": [f"DRUGBANK:DB0{i}" for i in range(9)],
                              "name": ["x"] * 5 + ["Iron", "Specific", "Unapproved", "Other"]})
        def relation_scores(self, neigh, node_type, w):  # C3L-1's own scores
            return np.array([0.30, 0.25, 0.40, 0.10])

    fake = lambda tag: Fake()  # noqa: E731
    fake.cache_clear = lambda: None
    std = lambda s: (s - s.mean()) / s.std()  # noqa: E731
    R = np.vstack([std(np.array([0.30, 0.25, 0.40, 0.10])),   # C3L-1 (left out of its own baseline)
                   std(np.array([0.30, 0.05, 0.10, 0.12])),   # others: Iron high for everyone
                   std(np.array([0.31, 0.06, 0.12, 0.10]))])
    cohort = lambda *a: (["C3L-1", "C3L-2", "C3L-3"], R)  # noqa: E731
    cohort.cache_clear = lambda: None
    approved = lambda v: frozenset({5, 6, 8})  # noqa: E731
    approved.cache_clear = lambda: None
    monkeypatch.setattr(T, "_gnn_a", fake)
    monkeypatch.setattr(T, "_cohort_scores", cohort)
    monkeypatch.setattr(T, "_approved_drugs", approved)
    r = T.dispatch("rank_for_patient", {"patient_id": "C3L-1", "node_type": "drug", "k": 3})
    assert r["scoring"] == "cohort_relative" and r["filter"] == "drugs with an indication edge"
    names = [x["name"] for x in r["items"]]
    assert names[0] == "Specific" and "Unapproved" not in names and names.index("Iron") > 0
    assert r["items"][0]["evidence_id"] == "rank:C3L-1:drug:DRUGBANK:DB06" and "model_score" in r["items"][0]
    none = lambda v: frozenset()  # noqa: E731  (a variant with no indication edges)
    none.cache_clear = lambda: None
    monkeypatch.setattr(T, "_approved_drugs", none)
    assert "none" in T.dispatch("rank_for_patient", {"patient_id": "C3L-1", "node_type": "drug"})["filter"]


def test_approved_drugs_respects_variant(tiny_processed):
    e = pd.read_parquet(tiny_processed / "edges.parquet")
    extra = pd.DataFrame({"edge_row": [4, 5], "src": [2, 2], "dst": [3, 4], "src_type": ["disease"] * 2,
                          "rel": ["indication"] * 2, "dst_type": ["drug"] * 2, "origin": ["primekg"] * 2,
                          "dm_row": [-1, -1], "hub": [False] * 2, "e1_holdout": [False] * 2,
                          "e4_dismech": [False] * 2, "e4_full": [False, True]})
    pd.concat([e, extra]).to_parquet(tiny_processed / "edges.parquet")
    assert T._approved_drugs("full") == {3, 4}
    assert T._approved_drugs("e4") == {3}
