"""E4 cohort chart renders from results files and refuses incomplete ones.

    python -m pytest tests/test_make_e4_cohort_chart.py -q
"""
import json

import pytest

from eval import make_charts as M
from eval import make_e4_cohort_chart as C


def _results(path, steps, drugs):
    path.write_text(json.dumps({"n_valid": 20, "n_traces": 20, "n_leaky": 0, "overall": {
        "step_recovery_kg": dict(zip(C.LABELS, steps)), "mean_drugs_surfaced": drugs}}))
    return path


def test_chart_renders(tmp_path):
    hidden = C.load(_results(tmp_path / "h.json", [0.05, 0, 0.05, 0, 0.15], 0.0))
    full = C.load(_results(tmp_path / "f.json", [1, 0.8, 1, 0.8, 1], 4.0))
    png = C.chart(hidden, full, tmp_path)
    assert png.name == "e4_cohort.png" and png.stat().st_size > 10_000


def test_incomplete_results_are_refused(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"n_valid": 20, "overall": {"step_recovery_kg": {"VHL Tumor Suppressor Inactivation": 1}}}))
    with pytest.raises(M.ValidationError):
        C.load(p)
