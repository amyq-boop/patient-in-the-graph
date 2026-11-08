"""Offline tests for eval/e4_cohort.py: sampling, scoring rules, leak handling, resume, hand-check agreement.

    python -m pytest tests/test_e4_cohort.py -q
"""
import csv
import json
import os

import pandas as pd
import pytest

from eval import e4_cohort as K
from eval import e4_case_study as E4

KEY = E4.load_key()


# ---------------------------------------------------------------- plan

def _pools(tmp_path):
    ids = [f"C3L-{i:05d}" for i in range(40)]
    pd.DataFrame({"patient_id": ids + ids[:3], "model": ["gnn"] * 40 + ["clinical_lr"] * 3,
                  "fold": 0, "risk": 0.5}).to_parquet(tmp_path / "pred.parquet")
    rows = [(p, K.VHL if i < 25 else "NCBIGene:5290") for i, p in enumerate(ids[:35])]  # 25 VHL, 10 other, 5 no data
    pd.DataFrame({"patient_id": [r[0] for r in rows], "curie": [r[1] for r in rows], "name": "x",
                  "source_field": "somatic_mutation", "source_text": "p.X", "weight": 1.0}).to_parquet(tmp_path / "mut.parquet")
    return tmp_path / "pred.parquet", tmp_path / "mut.parquet", ids


def test_pools_and_draw(tmp_path):
    pred, mut, ids = _pools(tmp_path)
    vhl, non, none = K.eligible_pools(pred, mut)
    assert (len(vhl), len(non), len(none)) == (25, 10, 5) and set(none) == set(ids[35:])
    a, b = K.draw(vhl, non)
    assert len(a) == 15 and len(b) == 5 and not set(a) & set(b)
    assert set(a) <= set(vhl) and set(b) <= set(non) and not set(b) & set(none)  # no-data patients never drawn
    assert (a, b) == K.draw(list(reversed(vhl)), list(reversed(non)))           # deterministic, order-independent


def test_make_plan_records_and_never_overwrites(tmp_path, monkeypatch):
    pred, mut, _ = _pools(tmp_path)
    monkeypatch.setattr(K, "git", lambda *a: "abc1234" if a[0] == "rev-parse" else "")
    path = tmp_path / "plan.json"
    plan = K.make_plan(predictions=pred, mutations=mut, path=path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["seed"] == 2026 and saved["git_commit"] == "abc1234" and saved["rule"] == K.RULE
    assert [p["group"] for p in saved["patients"]].count("vhl_mutated") == 15 and len(saved["patients"]) == 20
    assert saved["question"] == E4.QUESTION and saved["settings"]["AGENT_DRUG_TARGETS"] == "0"
    assert saved["created_utc"] and plan["patients"] == saved["patients"]
    with pytest.raises(SystemExit):
        K.make_plan(predictions=pred, mutations=mut, path=path)


def _pin_env(monkeypatch, variant="e4"):
    """Set the fixed settings through monkeypatch, so they are restored after the test."""
    for k, v in {**K.SETTINGS, "BIOHACK_VARIANT": variant}.items():
        monkeypatch.setenv(k, v)


def test_settings_clash_stops(monkeypatch):
    _pin_env(monkeypatch)
    monkeypatch.setenv("VERIFIER_STRICT", "0")
    with pytest.raises(SystemExit) as e:
        K.apply_settings("e4")
    assert "VERIFIER_STRICT" in str(e.value)


# ---------------------------------------------------------------- scoring

def _trace(pid="C3L-00001", group="vhl_mutated", leak=False, claims=None):
    pt = f"pt:{pid}:protein_up:NCBIGene:1"
    calls = [{"name": "get_patient", "args": {"patient_id": pid}, "by": "llm", "ms": 1,
              "result": {"patient_id": pid, "proteins_up": [{"curie": "NCBIGene:1", "edge_id": pt}]}},
             {"name": "retrieve_subgraph", "args": {"seeds": []}, "by": "llm", "ms": 1,
              "result": {"edge_ids": ["E5", "E6"], "edges": [{"edge_id": "E5"}, {"edge_id": "E6"}],
                         "triples_text": "[E5] a --x--> b\n[E6] c --y--> d", "meta": {"variant": "e4"}}}]
    if leak:
        calls.append({"name": "get_mechanism", "args": {"disease_curie": "MONDO:0005005"}, "by": "llm", "ms": 1,
                      "result": {"disease": "MONDO:0005005", "chains": [{"chain_id": "c", "steps": []}]}})
    claims = claims if claims is not None else [
        ("c1", "VHL loss stabilizes HIF-2alpha [E5].", ["E5"], "supported"),
        ("c2", "Sunitinib blocks VEGF-driven angiogenesis [E6].", ["E6"], "supported"),
        ("c3", "Aerobic glycolysis is increased [E99].", ["E99"], "removed"),
        ("c4", "The tumor shows growth factor signalling [" + pt + "].", [pt], "supported"),
        ("c5", "Lenvatinib is another option [E6].", ["E6"], "supported")]
    return {"contract_version": "1", "run_id": "r", "qid": "E4", "mode": "agent", "patient_id": pid,
            "question": E4.QUESTION, "model": "m", "variant": "e4", "notes": [], "draft": "d", "answer": "a",
            "tool_calls": calls, "evidence": {"edge_ids": [pt, "E5", "E6"], "pmids": []},
            "claims": [{"claim_id": c, "text": q.split(" [")[0], "quote": q, "status": st,
                        "support": {"edge_ids": ids, "pmids": []}} for c, q, ids, st in claims],
            "verifier": {}, "e4_cohort": {"group": group}}


NO_AUDIT = lambda t, k: {}  # noqa: E731  (the real audit needs edges.parquet)


def test_step_and_drug_rules():
    row, matches = K.score_trace(_trace(), KEY, audit=NO_AUDIT)
    s = lambda lab: row[f"step:{lab}"]  # noqa: E731
    assert row["valid"] and not row["leak"]
    assert s("VHL Tumor Suppressor Inactivation") and s("HIF-2alpha Stabilization") and s("VEGF-Driven Angiogenesis")
    assert s("Growth Factor Pathway Activation")                       # patient-ID citation counts for the main rule
    assert not row["step_kg:Growth Factor Pathway Activation"]          # ...but not for the KG-cited variant
    assert not s("Metabolic Reprogramming and Aerobic Glycolysis")     # only a REMOVED claim mentions glycolysis
    assert row["n_steps_recovered"] == 4 and row["n_steps_recovered_kg"] == 3
    assert row["drugs_surfaced"] == ["sunitinib"] and row["extra_drugs"] == ["lenvatinib"]


def test_supported_claim_must_cite_observed_evidence():
    t = _trace(claims=[("c1", "VHL loss drives this [E77].", ["E77"], "supported")])  # E77 never returned by a tool
    row, _ = K.score_trace(t, KEY, audit=NO_AUDIT)
    assert row["valid"] and not row["step:VHL Tumor Suppressor Inactivation"]


def test_leaks_are_reported_not_dropped(tmp_path):
    row, _ = K.score_trace(_trace(leak=True), KEY, audit=NO_AUDIT)
    assert not row["valid"] and row["leak"] and "ccRCC chain" in row["invalid_reason"]

    def leaky_audit(t, k):
        raise E4.E4Error("E4 returned 2 masked graph edges, e.g. E5")
    row, _ = K.score_trace(_trace(), KEY, audit=leaky_audit)
    assert not row["valid"] and row["leak"]

    plan = {"patients": [{"patient_id": "C3L-00001", "group": "vhl_mutated"},
                         {"patient_id": "C3L-00002", "group": "no_vhl_mutation"},
                         {"patient_id": "C3L-00003", "group": "vhl_mutated"},
                         {"patient_id": "C3L-00004", "group": "vhl_mutated"}]}
    for t in (_trace("C3L-00001"), _trace("C3L-00002", "no_vhl_mutation", claims=[]), _trace("C3L-00003", leak=True)):
        (tmp_path / f"{t['patient_id']}.json").write_text(json.dumps(t), encoding="utf-8")
    summary, rows, _ = K.score_cohort(plan, tmp_path, KEY, audit=NO_AUDIT)
    assert summary["n_traces"] == 3 and summary["missing"] == ["C3L-00004"]
    assert summary["n_valid"] == 2 and summary["n_leaky"] == 1 and summary["invalid"][0]["patient_id"] == "C3L-00003"
    assert summary["by_group"]["vhl_mutated"]["step_recovery"]["VHL Tumor Suppressor Inactivation"] == 1.0
    assert summary["by_group"]["no_vhl_mutation"]["step_recovery"]["VHL Tumor Suppressor Inactivation"] == 0.0
    assert summary["overall"]["mean_drugs_surfaced"] == 0.5


def test_handcheck_and_agreement(tmp_path):
    plan = {"patients": [{"patient_id": "C3L-00001", "group": "vhl_mutated"}]}
    (tmp_path / "C3L-00001.json").write_text(json.dumps(_trace()), encoding="utf-8")
    sheet = tmp_path / "hc.csv"
    path, rows = K.make_handcheck(plan, tmp_path, n=5, key=KEY, path=sheet, audit=NO_AUDIT)
    assert len(rows) == 5 and rows[0]["auto_verdict"] == "recovered" and "E5" in rows[0]["cited_ids"]
    meta = next(r for r in rows if r["step"].startswith("Metabolic"))
    assert meta["auto_verdict"] == "not_recovered" and "glycolysis" in meta["claim_quote"].lower()  # near-miss shown
    with open(sheet, encoding="utf-8") as f:
        data = list(csv.DictReader(f))
    human = ["recovered", "recovered", "not_recovered", "recovered", "yes"]  # disagrees on rows 3 and 5
    for r, h in zip(data, human):
        r["human_verdict"] = h
    with open(sheet, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, K.HAND_COLS)
        w.writeheader()
        w.writerows(data)
    a = K.agreement(sheet)
    assert a["n_rated"] == 5 and a["agreement"] == 0.6
    assert a["auto_recovered_human_no"] == 1 and a["auto_missed_human_yes"] == 1


# ---------------------------------------------------------------- run mechanics

def test_run_is_resumable_and_refuses_a_changed_setup(tmp_path, monkeypatch):
    _pin_env(monkeypatch)
    from agent import config as C
    from agent import graph_agent
    from eval import e3_batch
    monkeypatch.setattr(C, "VARIANT", "e4")
    monkeypatch.setattr(C, "DRUG_TARGETS_CCRCC", False)
    monkeypatch.setenv("BIOHACK_FAKE_LLM", "1")
    calls = []

    def fake_run(q, patient_id=None, qid=None, llm=None, run_id=None, **k):
        calls.append(patient_id)
        t = _trace(patient_id)
        t["run_id"] = run_id
        t["verifier"] = {"n_supported": 1, "n_claims": 1}
        return t
    monkeypatch.setattr(graph_agent, "run", fake_run)
    monkeypatch.setattr(e3_batch, "warm_up", lambda qs: None)
    commit = {"v": "aaa1111"}
    monkeypatch.setattr(K, "git", lambda *a: commit["v"] if a[0] == "rev-parse" else "")
    plan = {"patients": [{"patient_id": "C3L-00001", "group": "vhl_mutated"},
                         {"patient_id": "C3L-00002", "group": "no_vhl_mutation"}]}
    out = tmp_path / "traces"
    K.run_cohort(plan, "e4", workers=2, out_dir=out)
    assert sorted(calls) == ["C3L-00001", "C3L-00002"] and (out / "_manifest.jsonl").exists()
    m = json.loads((out / "_manifest.jsonl").read_text(encoding="utf-8"))
    assert m["fixed_settings"]["VERIFIER_STRICT"] == "1" and m["fixed_settings"]["AGENT_DRUG_TARGETS"] == "0"
    assert m["patients"] == ["C3L-00001", "C3L-00002"] and os.environ["BIOHACK_VARIANT"] == "e4"
    K.run_cohort(plan, "e4", workers=2, out_dir=out)          # resume: nothing re-run
    assert len(calls) == 2
    commit["v"] = "bbb2222"                                     # code changed since the run started
    with pytest.raises(SystemExit) as e:
        K.run_cohort(plan, "e4", out_dir=out)
    assert "aaa1111" in str(e.value)
