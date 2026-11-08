"""Range citations ([E12–E15]) and citations inside non-claims (caveats, experiments). Found in the brca_v1 run:
18BR019 cited [E8137916–E8271392] (133,477 rows) for ten drugs; 05BR044's experiment line cited alpelisib's edge
for capivasertib. Offline, no LLM.

    python -m pytest tests/test_verifier_ranges.py -q
"""
import os

os.environ.setdefault("VERIFIER_SPLITTER", "rules")

from agent.verifier import cited_ids, verify  # noqa: E402


class NoLLM:
    model = "none"

    def chat(self, *a, **k):
        raise AssertionError("rules splitter must not call the LLM")


def _run(draft, edges, names=None, strict=True, monkeypatch=None):
    monkeypatch.setenv("VERIFIER_SPLITTER", "rules")
    return verify(draft, {"edge_ids": edges, "pmids": ["PMID:1"]}, NoLLM(), edge_names=names, strict=strict)


def test_range_expands_in_either_order_and_dash():
    # support lists a range as written (its two ends): only IDs that appear in the sentence (e4_case_study checks)
    assert cited_ids("chain [E10–E12]") == ["E10", "E12"]
    assert cited_ids("chain [E12-E10, E7]") == ["E7", "E12", "E10"]
    assert cited_ids("chain (E10 – 12)") == ["E10", "E12"]
    assert cited_ids("too long [E1–E99]") == ["E1", "E99"]  # dropped by verify: longer than RANGE_MAX
    assert cited_ids("mutation p.E545K [pt:P1:somatic_mutation:NCBIGene:5290]") == ["pt:P1:somatic_mutation:NCBIGene:5290"]


def test_range_counts_only_if_every_id_is_in_evidence(monkeypatch):
    draft = ("- VHL loss stabilises HIF2A, which drives VEGFA [E10–E12].\n"
             "- TP53 loss drives EMT and immune evasion [E20–E23].\n"
             "- Ten approved drugs treat it [E1–E99].")
    claims, answer, stats, _ = _run(draft, ["E10", "E11", "E12", "E20", "E23"], strict=False, monkeypatch=monkeypatch)
    c1, c2, c3 = claims
    assert c1["status"] == "supported" and c1["support"]["edge_ids"] == ["E10", "E12"]
    assert c1["range_citations"] == [{"range": "E10–E12", "ids": ["E10", "E11", "E12"], "ok": True}]
    # E21, E22 were never retrieved: the whole range is void, endpoints included
    assert c2["status"] == "removed" and c2["support"]["edge_ids"] == [] and c2["unmatched_ids"] == ["E20–E23"]
    assert c3["status"] == "removed" and c3["unmatched_ids"] == ["E1–E99"]
    assert stats["n_range_citations_rejected"] == 2 and "range_citations" in c3
    assert "TP53" not in answer and "VHL" in answer


def test_id_cited_on_its_own_survives_a_failed_range(monkeypatch):
    claims, _, _, _ = _run("- TP53 loss drives EMT [E20–E23, E20].", ["E20", "E23"], strict=False, monkeypatch=monkeypatch)
    assert claims[0]["status"] == "supported" and claims[0]["support"]["edge_ids"] == ["E20"]


def test_support_ids_appear_in_the_quote(monkeypatch):
    """eval/e4_case_study.supported_claims requires every support ID to be written in the claim's quote."""
    from eval.e4_case_study import cited_in_quote
    claims, _, _, _ = _run("- PDE2A inhibitors [E336698–E336702]; ATIC [E1–E7]; AFDN [E9].",
                           [f"E{i}" for i in range(336698, 336703)] + ["E9", "E1"], strict=False, monkeypatch=monkeypatch)
    c = claims[0]
    assert c["status"] == "supported" and set(c["support"]["edge_ids"]) <= cited_in_quote(c["quote"])
    assert c["support"]["edge_ids"] == ["E9", "E336698", "E336702"]  # failed ATIC range: E1 dropped too


def test_claims_without_ranges_are_unchanged(monkeypatch):
    claims, _, stats, _ = _run("- VHL loss drives HIF2A [E10].", ["E10"], strict=False, monkeypatch=monkeypatch)
    assert "range_citations" not in claims[0] and stats["n_range_citations_rejected"] == 0


def test_experiment_citation_is_checked(monkeypatch):
    """A proposed experiment isn't scored, but a citation in it must still hold (strict: name what it cites)."""
    draft = ("**Testable hypothesis**\nHypothesis: AKT1 E17K makes the tumor AKT-dependent [E5].\n"
             "Experiment: treat organoids with capivasertib, which has a breast cancer indication edge [E8], "
             "then measure pAKT.")
    names = {"E5": ["AKT1", "PI3K-AKT"], "E8": ["Alpelisib", "DRUGBANK:DB12015"]}
    claims, answer, stats, _ = _run(draft, ["E5", "E8"], names, monkeypatch=monkeypatch)
    nc = stats["non_claims"][0]
    assert nc["reason"] == "experiment" and nc["bad_ids"] == ["E8"] and stats["n_non_claims_bad_citations"] == 1
    assert "indication edge [citation removed], then" in answer and "[E8]" not in answer
    assert [c["status"] for c in claims] == ["supported"]  # the hypothesis line is untouched
    # without strict mode the naming check is off: E8 is in evidence, so the line counts as an ordinary claim
    claims2, answer2, _, _ = _run(draft, ["E5", "E8"], names, strict=False, monkeypatch=monkeypatch)
    assert "[E8]" in answer2 and len(claims2) == 2


def test_caveat_citations_not_in_evidence_are_removed_but_model_outputs_are_exempt(monkeypatch):
    draft = ("**Caveats**\n- Other proteins link DARS1 to spasticity [E9].\n"
             "- Baseline medication has 0% share in the model [pred:P1:gnn].")
    names = {"pred:P1:gnn": ["patient", "risk"]}
    claims, answer, stats, _ = _run(draft, ["pred:P1:gnn"], names, monkeypatch=monkeypatch)
    a, b = stats["non_claims"]
    assert a["bad_ids"] == ["E9"] and "spasticity [citation removed]." in answer
    assert "bad_ids" not in b and "[pred:P1:gnn]" in answer
    assert claims == []
