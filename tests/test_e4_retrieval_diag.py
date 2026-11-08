"""E4 retrieval diagnostic, offline with a fake retriever.

    python -m pytest tests/test_e4_retrieval_diag.py -q
"""
import pandas as pd

from eval.e4_retrieval_diag import VHL, diagnose, flat, rates, resolve_markers

NODES = pd.DataFrame({"curie": ["NCBIGene:7428", "NCBIGene:2034", "NCBIGene:7422", "NCBIGene:5230", "NCBIGene:1"],
                      "name": ["VHL", "EPAS1", "VEGFA", "PGK1", "OTHER"], "type": "gene"})


def _res(curies, edges=()):
    return {"nodes": [{"curie": c} for c in curies],
            "edges": [{"edge_id": e, "src_curie": s, "dst_curie": d} for e, s, d in edges]}


def test_arms_and_markers():
    m = resolve_markers(NODES)
    assert m["2_hif"] == {"NCBIGene:2034": "EPAS1"} and m["5_glycolysis"] == {"NCBIGene:5230": "PGK1"}
    trace = {"tool_calls": [
        {"name": "get_patient", "args": {}, "result": {"mutations": [{"curie": VHL}, {"curie": "NCBIGene:1"}]}},
        {"name": "retrieve_subgraph", "args": {"question": "q", "seeds": ["PATIENT:X", VHL, "NCBIGene:1"], "budget": 50},
         "result": _res([VHL, "NCBIGene:1", "NCBIGene:5230"])}]}
    seen = []

    def fake(question, seeds, budget):
        seen.append((question, tuple(seeds), budget))
        if seeds == [VHL]:
            return _res([VHL, "NCBIGene:2034", "NCBIGene:7422"], [("E9", "NCBIGene:2034", VHL)])
        return _res([VHL, "NCBIGene:1", "NCBIGene:5230"])

    rows, info = diagnose(trace, fake, m)
    assert rows["agent"]["2_hif"] == [] and rows["agent"]["5_glycolysis"] == ["PGK1"]
    assert rows["agent_rerun"]["2_hif"] == [] and rows["vhl_only"]["2_hif"] == ["EPAS1"]
    assert rows["vhl_only"]["vhl_hif_edges"] == ["E9"] and rows["vhl_only"]["3_vegf"] == ["VEGFA"]
    assert seen[1] == ("q", (VHL,), 50) and seen[2] == ("q", ("NCBIGene:1", VHL), 50)  # mutated, sorted
    assert info == {"agent_seeds": 3, "n_agent_retrieve_calls": 1, "vhl_mutated": True, "n_mutated": 2}
    table = flat("X", "vhl_mutated", rows)
    assert rates(table, "vhl_only")["2_hif"] == 1.0 and rates(table, "agent")["2_hif"] == 0.0


def test_no_retrieval_and_no_mutations():
    m = resolve_markers(NODES)
    rows, info = diagnose({"tool_calls": []}, lambda **k: 1 / 0, m)
    assert "no successful retrieve_subgraph" in info["note"] and rows["agent"]["n_nodes"] == 0
    trace = {"tool_calls": [{"name": "retrieve_subgraph", "args": {"question": "q", "seeds": ["S"]}, "result": _res(["S"])}]}
    rows, _ = diagnose(trace, lambda **k: _res([]), m)
    assert rows["mutated_only"]["note"].startswith("no seeds")
