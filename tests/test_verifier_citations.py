"""Verifier citation parsing: parenthesised IDs, PMID lists, empty sections (found in the e3_v1 run, D07).

    python -m pytest tests/test_verifier_citations.py -q
"""
import os

os.environ.setdefault("VERIFIER_SPLITTER", "rules")

from agent.verifier import cited_ids, strip_citations, verify  # noqa: E402


class NoLLM:
    model = "none"

    def chat(self, *a, **k):
        raise AssertionError("rules splitter must not call the LLM")


def test_parenthesised_ids_are_citations():
    assert cited_ids("GAP loss (E3635962, E3505947) drives mTORC1 (E8320611).") == ["E3635962", "E3505947", "E8320611"]
    assert cited_ids("VEGFA is up (pt:C3L-1:protein_up:NCBIGene:7422).") == ["pt:C3L-1:protein_up:NCBIGene:7422"]


def test_plain_parentheses_are_not():
    assert cited_ids("Stage (III) tumors, BMI (35.0), n (=3), year (2019).") == []
    assert strip_citations("Loss (E36, E35) causes signaling (E83) in patients (n=3).") == \
        "Loss causes signaling in patients (n=3)."



def test_protein_notation_is_not_an_edge_id():
    # p.E70* is a VHL truncation, not edge E70 (found in the audit).
    assert cited_ids("VHL truncation (p.E70*) drives HIF-2\u03b1 stabilization [E12].") == ["E12"]
    assert cited_ids("Variants (p.E70K, p.E70fs, p.E70_L72del) are listed.") == []
    assert cited_ids("Truncation (E70*) only.") == []
    assert cited_ids("VHL loss [E70, E71] and [E12\u2013E14].") == ["E70", "E71", "E12", "E14"]
    assert strip_citations("VHL truncation (p.E70*) drives HIF.") == "VHL truncation (p.E70*) drives HIF."

def test_bare_numbers_after_pmid_are_pmids():
    assert cited_ids("First hit [PMID:39334956, 38540392] (E8320604).") == ["PMID:39334956", "PMID:38540392", "E8320604"]
    assert cited_ids("Edge [E1, 38540392]") == ["E1"]  # no PMID: in the group -> a bare number is not a PMID


def test_parenthesised_claims_are_supported_and_strict_still_checks_names(monkeypatch):
    monkeypatch.setenv("VERIFIER_SPLITTER", "rules")
    draft = ("**Mechanism**\n- TSC2 loss causes constitutive mTORC1 signaling (E2).\n"
             "- Everolimus inhibits mTORC1 (E2).\n- Germline TSC2 hit [PMID:39334956, 38540392].")
    names = {"E2": ["TSC2", "mTORC1"]}
    claims, answer, stats, _ = verify(draft, {"edge_ids": ["E2"], "pmids": ["PMID:38540392"]}, NoLLM(),
                                      edge_names=names, strict=True)
    st = {c["text"].split()[0]: c["status"] for c in claims}
    assert st["TSC2"] == "supported"
    assert st["Germline"] == "supported"   # PMID:38540392 is the second, bare number in the group
    assert st["Everolimus"] == "supported"  # mTORC1 is named; strict mode only needs one endpoint named
    assert stats["n_removed"] == 0


def test_mismatched_parenthesised_id_is_still_removed_in_strict(monkeypatch):
    monkeypatch.setenv("VERIFIER_SPLITTER", "rules")
    draft = "- Lenvatinib inhibits FGFR4 (E9)."
    claims, _, _, _ = verify(draft, {"edge_ids": ["E9"], "pmids": []}, NoLLM(),
                             edge_names={"E9": ["Axitinib", "KDR"]}, strict=True)
    assert claims[0]["status"] == "removed" and claims[0]["mismatched_ids"] == ["E9"]


def test_empty_section_header_is_dropped(monkeypatch):
    monkeypatch.setenv("VERIFIER_SPLITTER", "rules")
    draft = "**Summary**\nThe tumor is aggressive for many reasons.\n\n**Evidence paths**\n- VHL links ccRCC [E1]."
    claims, answer, stats, _ = verify(draft, {"edge_ids": ["E1"], "pmids": []}, NoLLM(),
                                      edge_names={"E1": ["VHL", "ccRCC"]}, strict=True)
    assert stats["n_removed"] == 1 and "**Summary**" not in answer and answer.startswith("**Evidence paths**")
