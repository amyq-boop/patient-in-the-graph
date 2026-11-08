"""Offline test for `agent.run --demo-patients` (mock tools + FakeLLM).

    python -m pytest tests/test_demo_run.py -q
"""
import json
import os

os.environ["BIOHACK_FAKE_LLM"] = "1"

from agent import config as C  # noqa: E402
from agent import run as R  # noqa: E402

QS = [
    {"qid": "P01", "kind": "patient", "patient_id": "C3L-MOCK1", "demo": True, "question": "What drives this patient's risk?"},
    {"qid": "P02", "kind": "patient", "patient_id": "C3L-MOCK1", "demo": True, "question": "Is the hypertension drug-driven?"},
    {"qid": "P06", "kind": "patient", "patient_id": "C3L-MOCK1", "demo": False, "question": "Not a demo question."},
    {"qid": "D01", "kind": "disease", "patient_id": None, "question": "What causes ccRCC?"},
]


def _setup(tmp_path, monkeypatch):
    q = tmp_path / "q.jsonl"
    q.write_text("\n".join(json.dumps(x) for x in QS), encoding="utf-8")
    monkeypatch.setattr(C, "DEMO_CACHE", tmp_path / "demo")
    monkeypatch.setattr(C, "MOCK_MODE", "1")
    return q


def test_demo_refuses_mock_without_flag(tmp_path, monkeypatch):
    q = _setup(tmp_path, monkeypatch)
    try:
        R.main(["--questions", str(q), "--demo-patients", "C3L-MOCK1"])
    except SystemExit as e:
        assert "cache/demo" in str(e)
    else:
        raise AssertionError("should refuse BIOHACK_MOCK=1")


def test_demo_writes_traces_run_info_and_manifest(tmp_path, monkeypatch, capsys):
    q = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("VERIFIER_STRICT", "1")
    monkeypatch.setenv("VERIFIER_SPLITTER", "rules")
    R.main(["--questions", str(q), "--demo-patients", "C3L-MOCK1", "C3L-MOCK2", "--allow-mock", "--run-id", "t"])
    d = tmp_path / "demo"
    assert sorted(p.name for p in d.glob("*__*.json")) == [
        "C3L-MOCK1__P01.json", "C3L-MOCK1__P02.json", "C3L-MOCK2__P01.json", "C3L-MOCK2__P02.json"]  # demo:true only
    assert not [p for p in d.glob("*.json") if not p.name.count("__")]  # manifest isn't a .json the app would read
    m = json.loads((d / "_manifest.jsonl").read_text(encoding="utf-8"))
    assert m["demo_patients"] == ["C3L-MOCK1", "C3L-MOCK2"] and m["qids"] == ["P01", "P02"] and m["run_id"] == "t_demo"
    assert m["settings"]["VERIFIER_STRICT"] == "1" and m["settings"]["VERIFIER_SPLITTER"] == "rules"
    t = json.loads((d / "C3L-MOCK2__P01.json").read_text(encoding="utf-8"))
    assert t["patient_id"] == "C3L-MOCK2" and t["run_info"]["run_id"] == "t_demo"
    assert t["run_info"]["settings"]["VERIFIER_STRICT"] == "1" and "git_commit" in t["run_info"]
    assert "different verifier settings" not in capsys.readouterr().err


def test_demo_warns_on_non_e3_verifier_settings(tmp_path, monkeypatch, capsys):
    q = _setup(tmp_path, monkeypatch)
    monkeypatch.delenv("VERIFIER_STRICT", raising=False)
    monkeypatch.delenv("VERIFIER_SPLITTER", raising=False)
    R.main(["--questions", str(q), "--demo-patients", "C3L-MOCK1", "--allow-mock", "--only", "P01"])
    assert "different verifier settings" in capsys.readouterr().err
