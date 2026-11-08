"""E4 cohort chart renders from results files and refuses incomplete ones.

    python -m pytest tests/test_make_e4_cohort_chart.py -q
"""
import json

import pytest

from eval import make_charts as M
from eval import make_e4_cohort_chart as C


def _results(path, kg, plain, drugs):
    path.write_text(json.dumps({"n_valid": 20, "n_traces": 20, "n_leaky": 0, "overall": {
        "step_recovery_kg": dict(zip(C.LABELS, kg)), "step_recovery": dict(zip(C.LABELS, plain)),
        "mean_drugs_surfaced": drugs}}))
    return path


def test_chart_renders_and_counts_uncited_hif(tmp_path):
    hidden = C.load(_results(tmp_path / "h.json", [0.05, 0, 0.05, 0, 0.15], [0.15, 0.05, 0.05, 0, 0.15], 0.0))
    full = C.load(_results(tmp_path / "f.json", [1, 0.8, 1, 0.8, 1], [1, 0.8, 1, 0.8, 1], 4.0))
    assert hidden["hif2_uncited"] == 1 and full["hif2_uncited"] == 0
    png = C.chart(hidden, full, tmp_path, {"llm_only": (15, 16, 20), "hub0": (14, 20, ["HIF1A"])})
    assert png.name == "e4_cohort.png" and png.stat().st_size > 10_000


def test_llm_only_and_hub0_notes(tmp_path):
    d = tmp_path / "llm"
    d.mkdir()
    for i, text in enumerate(["VHL loss stabilises HIF-2a; VEGF", "VHL only", "nothing"]):
        (d / f"Q{i}.json").write_text(json.dumps({"draft": text}))
    assert C.llm_only_mentions(d) == (1, 2, 3)
    diag = tmp_path / "diag.json"
    diag.write_text(json.dumps({"per_patient": {"A": {"arms": {"agent_rerun": {"2_hif": ["HIF1A"]}}},
                                                "B": {"arms": {"agent_rerun": {"2_hif": []}}}}}))
    assert C.hif_genes_without_penalty(diag) == (1, 2, ["HIF1A"])


def test_incomplete_results_are_refused(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"n_valid": 20, "overall": {"step_recovery_kg": {"VHL Tumor Suppressor Inactivation": 1}}}))
    with pytest.raises(M.ValidationError):
        C.load(p)
