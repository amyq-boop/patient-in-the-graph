"""Cancer cohorts (ccRCC unchanged, BRCA added) and the get_drug_targets tool. Offline, tiny synthetic graph.

    python -m pytest tests/test_cancer_and_drug_targets.py -q
"""
import os

os.environ.setdefault("BIOHACK_FAKE_LLM", "1")

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from agent import config as C  # noqa: E402
from agent import prompts  # noqa: E402
from agent import tools as T  # noqa: E402
from agent.graph_agent import _edge_names, _evidence, _llm_view  # noqa: E402
from agent.schemas import TOOLS, tools_for  # noqa: E402


def test_ccrcc_prompts_and_tools_are_unchanged():
    assert prompts.agent_system("ccrcc", False) == prompts.AGENT_SYSTEM.replace("__CCRCC_ID__", C.CCRCC_ID)
    assert prompts.llm_only_system("ccrcc") is prompts.LLM_ONLY_SYSTEM
    assert tools_for(False) is TOOLS and "get_drug_targets" not in [t["function"]["name"] for t in TOOLS]
    assert not C.drug_targets_enabled("ccrcc") or os.environ.get("AGENT_DRUG_TARGETS") == "1"


def test_brca_prompts():
    s = prompts.agent_system("brca", True)
    assert "ccRCC" not in s and "breast cancer (BRCA)" in s and "MONDO:0004989 (BRCA)" in s
    assert "then get_drug_targets, then retrieve_subgraph" in s and "not BRCA treatment" in s
    assert "ccRCC" not in prompts.llm_only_system("brca")
    assert [t["function"]["name"] for t in tools_for(True)][-1] == "get_drug_targets"


@pytest.fixture
def tiny(tmp_path, monkeypatch):
    """Nodes: genes ERBB2, PIK3CA, CYP3A4, KDR; drugs trastuzumab, alpelisib, sunitinib, tamoxifen, a lab ligand;
    diseases breast carcinoma, renal cell carcinoma. One ccRCC and one BRCA patient."""
    from common import contracts as K
    proc = tmp_path / "processed"
    (proc / "patients").mkdir(parents=True)
    (proc / "brca" / "patients").mkdir(parents=True)
    nodes = [("NCBIGene:2064", "gene", "ERBB2"), ("NCBIGene:5290", "gene", "PIK3CA"), ("NCBIGene:1576", "gene", "CYP3A4"),
             ("NCBIGene:3791", "gene", "KDR"), ("DRUGBANK:DB00072", "drug", "Trastuzumab"),
             ("DRUGBANK:DB12015", "drug", "Alpelisib"), ("DRUGBANK:DB01268", "drug", "Sunitinib"),
             ("DRUGBANK:DB00675", "drug", "Tamoxifen"), ("DRUGBANK:DB99999", "drug", "2'-Deoxy-ligand"),
             ("MONDO:0004989", "disease", "breast carcinoma"), ("MONDO:0005086", "disease", "renal cell carcinoma")]
    pd.DataFrame({"gidx": range(len(nodes)), "curie": [n[0] for n in nodes], "type": [n[1] for n in nodes],
                  "name": [n[2] for n in nodes], "alt_names": "", "aliases": ""}).to_parquet(proc / "nodes.parquet")
    E = [(4, 0, "drug_protein"), (5, 1, "drug_protein"), (5, 2, "drug_protein"), (6, 3, "drug_protein"),
         (7, 2, "drug_protein"), (8, 1, "drug_protein"), (4, 9, "indication"), (6, 10, "indication"), (7, 9, "indication")]
    pd.DataFrame({"edge_row": range(len(E)), "src": [e[0] for e in E], "dst": [e[1] for e in E],
                  "rel": [e[2] for e in E], "origin": "primekg", "dm_row": -1, "src_type": "drug",
                  "dst_type": ["gene" if e[2] == "drug_protein" else "disease" for e in E],
                  "hub": False, "e1_holdout": False, "e4_dismech": False, "e4_full": False}).to_parquet(proc / "edges.parquet")
    for d, pid, prot, sym in ((proc / "patients", "C3L-1", "NCBIGene:3791", "KDR"),
                              (proc / "brca" / "patients", "11BR1", "NCBIGene:2064", "ERBB2")):
        pd.DataFrame({"patient_id": [pid], "curie": [f"PATIENT:{pid}"], "clinical_summary": ["x"]}).to_parquet(d / "patients.parquet")
        pd.DataFrame(columns=["patient_id", "curie", "source_field", "source_text"]).to_parquet(d / "patient_phenotypes.parquet")
        pd.DataFrame({"patient_id": [pid, pid], "curie": [prot, "NCBIGene:1576"], "symbol": [sym, "CYP3A4"], "z": [4.0, 6.0],
                      "direction": ["up", "up"], "rank": [1, 2],
                      "z_ref": ["normal" if pid.startswith("C3") else "cohort"] * 2}).to_parquet(d / "patient_proteins.parquet")
    pd.DataFrame({"patient_id": ["11BR1"], "curie": ["NCBIGene:5290"], "name": ["PIK3CA"], "source_field": ["somatic_mutation"],
                  "source_text": ["p.H1047R"], "weight": [1.0]}).to_parquet(proc / "brca" / "patients" / "patient_mutations.parquet")
    for attr, val in [("nodes", proc / "nodes.parquet"), ("edges", proc / "edges.parquet"), ("processed", proc),
                      ("patients_dir", proc / "patients"), ("patients", proc / "patients" / "patients.parquet"),
                      ("patient_phenotypes", proc / "patients" / "patient_phenotypes.parquet"),
                      ("patient_proteins", proc / "patients" / "patient_proteins.parquet"),
                      ("patient_mutations", proc / "patients" / "patient_mutations.parquet"),
                      ("patient_drugs", proc / "patients" / "patient_drugs.parquet")]:
        monkeypatch.setattr(K.P, attr, val)
    monkeypatch.setitem(C.CANCERS, "brca", {**C.CANCERS["brca"], "patients_dir": proc / "brca" / "patients"})
    monkeypatch.setattr(C, "MOCK_MODE", "0")
    monkeypatch.setattr(C, "VARIANT", "full")
    caches = (T._nodes, T._cohort_index, T._patient_tables, T._patient_extras, T._drug_edges, T._cancer_indications,
              T._approved_drugs, T._adme_gidx)
    for f in caches:
        f.cache_clear()
    yield proc
    for f in caches:
        f.cache_clear()


def test_cohort_routing(tiny):
    assert T.cancer_of("C3L-1") == "ccrcc" and T.cancer_of("PATIENT:11BR1") == "brca" and T.cancer_of("nobody") is None
    b = T.dispatch("get_patient", {"patient_id": "11BR1"})
    assert b["cancer"] == "brca" and "cohort" in b["protein_reference"] and "not BRCA treatment" in b["note"]
    assert b["mutations"][0]["edge_id"] == "pt:11BR1:somatic_mutation:NCBIGene:5290"
    c = T.dispatch("get_patient", {"patient_id": "C3L-1"})
    assert "cancer" not in c and "protein_reference" not in c and "not ccRCC treatment" in c["note"]  # ccRCC unchanged
    assert "trained on the ccRCC cohort only" in T.dispatch("predict_patient", {"patient_id": "11BR1"})["error"]


def test_drug_targets_brca(tiny):
    r = T.dispatch("get_drug_targets", {"patient_id": "11BR1"})
    names = [d["name"] for d in r["items"]]
    assert names == ["Trastuzumab"]                      # Tamoxifen only hits CYP3A4 (ADME, not counted)
    t = r["items"][0]
    assert t["indicated_for_cancer"] and t["indication"][0]["edge_id"] == "E6"
    assert t["targets"][0]["drug_target_edge_id"] == "E0" and t["targets"][0]["patient_edge_id"] == "pt:11BR1:protein_up:NCBIGene:2064"
    other = [d["name"] for d in r["other_drugs_on_mutated_genes"]]
    assert other == ["Alpelisib"]                       # no indication edge, but hits mutated PIK3CA; lab ligand dropped
    a = r["other_drugs_on_mutated_genes"][0]
    assert a["targets"][0]["basis"] == "somatic_mutation" and a["n_targets_total"] == 1  # CYP3A4 not counted as a target

    calls = [{"name": "get_drug_targets", "result": r, "by": "llm"}]
    ev = _evidence(calls)["edge_ids"]
    assert {"E0", "E6", "E1", "pt:11BR1:protein_up:NCBIGene:2064", "pt:11BR1:somatic_mutation:NCBIGene:5290"} <= set(ev)
    names = _edge_names(calls)
    assert names["E0"][:2] == ["Trastuzumab", "ERBB2"] and names["E6"][:2] == ["Trastuzumab", "breast carcinoma"]
    view = _llm_view("get_drug_targets", r)
    assert "[E0] Trastuzumab (drug) --drug_protein--> ERBB2" in view and "[E1] Alpelisib (drug (no indication edge" in view


def test_drug_targets_ccrcc_patient_uses_ccrcc_indications(tiny):
    r = T.dispatch("get_drug_targets", {"patient_id": "C3L-1"})
    assert [d["name"] for d in r["items"]] == ["Sunitinib"] and r["items"][0]["indicated_for_cancer"]
    assert r["cancer"] == "ccrcc"


def test_unknown_patient_errors(tiny):
    assert "error" in T.dispatch("get_drug_targets", {"patient_id": "nobody"})
