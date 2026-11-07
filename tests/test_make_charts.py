"""Tests for eval/make_charts.py (WS4 / Demo): validation, metrics and placeholder handling.

    python -m pytest tests/test_make_charts.py -q
"""
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

from common.contracts import ROOT
from eval import make_charts as M

KEY = ROOT / "eval" / "e4_answer_key.json"
STEPS = M.answer_key_steps(KEY)
PNG = {"e1": "e1_retrieval", "e2": "e2_patients", "e3": "e3_grounding", "e4": "e4_case_study"}


def real_e1():
    return pd.DataFrame([{"disease": f"d{i}", "kind": k, "method": m, "n_targets": 3, "recall50": 0.1 * (j + 1),
                          "recall500": 0.5, "mrr": 0.1}
                         for i in range(10) for k in M.E1_KINDS for j, m in enumerate(M.E1_METHODS)])


def real_e2():
    return pd.DataFrame([{"model": m, "fold": f, "auroc": 0.6 + 0.01 * f}
                         for m in M.E2_MODELS for f in range(M.E2_FOLDS)])


def real_e3():
    return pd.DataFrame([{"mode": mode, "qid": f"P{i:02d}", "n_claims": 4, "n_supported": s, "n_hypothesis": 1,
                          "n_removed": 3 - s} for mode, s in (("agent", 3), ("llm_only", 1)) for i in range(1, 21)])


def real_e4(**over):
    d = {"patient_id": "C3L-00004", "qid": "P01", "expected_steps": STEPS, "recovered_steps": STEPS[:3],
         "step_evidence": {s: ["E1834221"] for s in STEPS[:3]}, "drugs_surfaced": ["Sunitinib"]}
    d.update(over)
    return d


@pytest.fixture
def dirs(tmp_path):
    res, out = tmp_path / "results", tmp_path / "fig"
    res.mkdir()
    return res, out


def write(res, e1=None, e2=None, e3=None, e4=None):
    for name, df in (("e1", e1), ("e2", e2), ("e3", e3)):
        if df is not None:
            df.to_csv(res / f"{name}.csv", index=False)
    if e4 is not None:
        (res / "e4.json").write_text(e4 if isinstance(e4, str) else json.dumps(e4))


def run(res, out):
    return {k: v["status"] for k, v in M.run(res, out, KEY).items()}


# ------------------------------------------------------------------ whole-pipeline cases
def test_all_missing_gives_four_placeholders(dirs):
    res, out = dirs
    assert run(res, out) == {k: "PLACEHOLDER" for k in PNG}
    assert all((out / f"{p}.png").exists() for p in PNG.values())


def test_all_valid_gives_four_real(dirs):
    res, out = dirs
    write(res, real_e1(), real_e2(), real_e3(), real_e4())
    assert run(res, out) == {k: "REAL" for k in PNG}


def test_one_missing_only_that_one_placeholder(dirs):
    res, out = dirs
    write(res, real_e1(), real_e2(), None, real_e4())
    s = run(res, out)
    assert s["e3"] == "PLACEHOLDER" and all(s[k] == "REAL" for k in ("e1", "e2", "e4"))


def test_invalid_file_removes_stale_png_and_reports(dirs):
    res, out = dirs
    run(res, out)  # placeholders exist
    bad = real_e3()
    bad.loc[0, "n_claims"] = 99
    write(res, e3=bad)
    st = M.run(res, out, KEY)
    assert st["e3"]["status"] == "ERROR" and "n_claims" in st["e3"]["notes"][0]
    assert not (out / "e3_grounding.png").exists()


def test_fake_markers_in_real_file_stay_placeholder(dirs):
    res, out = dirs
    write(res, e4=real_e4(patient_id="C3L-MOCK1"))
    assert run(res, out)["e4"] == "PLACEHOLDER"


# ------------------------------------------------------------------ E1
def test_e1_unknown_method(dirs):
    res, _ = dirs
    df = real_e1()
    df.loc[df.method == "bfs", "method"] = "bfs_v2"
    write(res, e1=df)
    with pytest.raises(M.ValidationError, match="unknown method"):
        M.load("e1", res, KEY)


def test_e1_missing_kind(dirs):
    res, _ = dirs
    write(res, e1=real_e1().query("kind == 'gene'"))
    with pytest.raises(M.ValidationError, match="kinds"):
        M.load("e1", res, KEY)


def test_e1_unequal_coverage(dirs):
    res, _ = dirs
    df = real_e1()
    write(res, e1=df.drop(df[(df.method == "bfs") & (df.disease == "d0")].index))
    with pytest.raises(M.ValidationError, match="unequal disease coverage"):
        M.load("e1", res, KEY)


def test_e1_llm_only_optional(dirs):
    res, _ = dirs
    write(res, e1=real_e1().query("method != 'llm_only'"))
    assert any("llm_only" in n for n in M.load("e1", res, KEY).notes)


def test_e1_shared_axis_and_macro_mean():
    m = M.e1_means(real_e1())
    assert m.loc["gene", "gnn_pcst"] == pytest.approx(0.4)


# ------------------------------------------------------------------ E2
def test_e2_missing_fold(dirs):
    res, _ = dirs
    df = real_e2()
    write(res, e2=df.drop(df[(df.model == "gnn") & (df.fold == 4)].index))
    with pytest.raises(M.ValidationError, match="same 5 folds"):
        M.load("e2", res, KEY)


def test_e2_missing_required_model(dirs):
    res, _ = dirs
    write(res, e2=real_e2().query("model != 'prot_pca_lr'"))
    with pytest.raises(M.ValidationError, match="missing required"):
        M.load("e2", res, KEY)


@pytest.mark.parametrize("val", [float("nan"), 1.3])
def test_e2_bad_auroc(dirs, val):
    res, _ = dirs
    df = real_e2()
    df.loc[0, "auroc"] = val
    write(res, e2=df)
    with pytest.raises(M.ValidationError, match="AUROC"):
        M.load("e2", res, KEY)


def test_e2_identical_values_zero_sd(dirs):
    res, out = dirs
    df = real_e2()
    df["auroc"] = 0.7
    write(res, e2=df)
    assert run(res, out)["e2"] == "REAL"
    assert (M.e2_stats(df)["std"] == 0).all()


# ------------------------------------------------------------------ E3
def test_e3_one_mode(dirs):
    res, _ = dirs
    write(res, e3=real_e3().query("mode == 'agent'"))
    with pytest.raises(M.ValidationError, match="modes"):
        M.load("e3", res, KEY)


def test_e3_inconsistent_counts(dirs):
    res, _ = dirs
    df = real_e3()
    df.loc[3, "n_removed"] = 7
    write(res, e3=df)
    with pytest.raises(M.ValidationError, match="!= n_claims"):
        M.load("e3", res, KEY)


def test_e3_zero_claims(dirs):
    res, _ = dirs
    df = real_e3()
    df.loc[df["mode"] == "llm_only", ["n_claims", "n_supported", "n_hypothesis", "n_removed"]] = 0
    write(res, e3=df)
    with pytest.raises(M.ValidationError, match="zero claims"):
        M.load("e3", res, KEY)


def test_e3_mismatched_questions(dirs):
    res, _ = dirs
    df = real_e3()
    df.loc[0, "qid"] = "X99"
    write(res, e3=df)
    with pytest.raises(M.ValidationError, match="different questions"):
        M.load("e3", res, KEY)


def test_e3_denominator_and_rounding():
    tot, share = M.e3_shares(real_e3())
    assert share.sum(axis=1).round(9).eq(1).all()
    assert sum(M.whole_percents([1 / 3, 1 / 3, 1 / 3])) == 100


# ------------------------------------------------------------------ E4
@pytest.mark.parametrize("expected", [STEPS[:4], STEPS + ["extra"]])
def test_e4_wrong_expected_steps(dirs, expected):
    res, _ = dirs
    write(res, e4=real_e4(expected_steps=expected))
    with pytest.raises(M.ValidationError, match="expected_steps"):
        M.load("e4", res, KEY)


@pytest.mark.parametrize("rec,msg", [(["Made-up step"], "not in the answer key"), ([STEPS[0], STEPS[0]], "duplicate")])
def test_e4_bad_recovered(dirs, rec, msg):
    res, _ = dirs
    write(res, e4=real_e4(recovered_steps=rec))
    with pytest.raises(M.ValidationError, match=msg):
        M.load("e4", res, KEY)


def test_e4_unverified_steps_not_counted(dirs):
    res, out = dirs
    d = real_e4(recovered_steps=STEPS[:3])
    del d["step_evidence"]
    write(res, e4=d)
    L = M.load("e4", res, KEY)
    assert any("UNVERIFIED" in n for n in L.notes)
    assert run(res, out)["e4"] == "REAL"


def test_e4_malformed_json(dirs):
    res, _ = dirs
    write(res, e4="{not json")
    with pytest.raises(M.ValidationError, match="not valid JSON"):
        M.load("e4", res, KEY)


def test_e4_long_drug_list_renders(dirs):
    res, out = dirs
    write(res, e4=real_e4(drugs_surfaced=[f"Very-long-drug-name-{i}" for i in range(30)]))
    assert run(res, out)["e4"] == "REAL"


# ------------------------------------------------------------------ empty / malformed files
def test_empty_csv(dirs):
    res, _ = dirs
    (res / "e2.csv").write_text("")
    with pytest.raises(M.ValidationError, match="empty"):
        M.load("e2", res, KEY)


def test_missing_columns(dirs):
    res, _ = dirs
    pd.DataFrame({"model": ["gnn"]}).to_csv(res / "e2.csv", index=False)
    with pytest.raises(M.ValidationError, match="missing columns"):
        M.load("e2", res, KEY)
