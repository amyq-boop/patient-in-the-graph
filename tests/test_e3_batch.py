"""Offline tests for eval/e3_batch.py (mock tools + FakeLLM).

    BIOHACK_MOCK=1 BIOHACK_FAKE_LLM=1 python -m pytest tests/test_e3_batch.py -q
"""
import json
import os

os.environ["BIOHACK_MOCK"] = "1"
os.environ["BIOHACK_FAKE_LLM"] = "1"

from eval import e3_batch  # noqa: E402

QS = [
    {"qid": "P01", "kind": "patient", "patient_id": "C3L-MOCK1", "disease": None,
     "question": "What drives this patient's predicted risk, and what would you target?", "expected": {}},
    {"qid": "D01", "kind": "disease", "patient_id": None, "disease": "MONDO:0005005",
     "question": "What mechanisms explain clear cell renal carcinoma?", "expected": {}, "trap": False},
]


def _qfile(tmp_path):
    p = tmp_path / "q.jsonl"
    p.write_text("\n".join(json.dumps(q) for q in QS) + "\n", encoding="utf-8")
    return p


def test_refuses_mock_without_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(e3_batch.C, "MOCK_MODE", "1")  # don't depend on import order / the caller's env
    try:
        e3_batch.main(["--questions", str(_qfile(tmp_path)), "--traces-dir", str(tmp_path / "t")])
    except SystemExit as e:
        assert "real data" in str(e)
    else:
        raise AssertionError("should refuse BIOHACK_MOCK=1")


def test_batch_resume_and_reverify(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(e3_batch.C, "MOCK_MODE", "1")
    q, t = _qfile(tmp_path), tmp_path / "t"
    args = ["--questions", str(q), "--traces-dir", str(t), "--allow-mock", "--workers", "2"]
    assert e3_batch.main(args) == 0
    for mode in ("agent", "llm_only"):
        d = t / f"e3_{mode}"
        assert {p.name for p in d.glob("*.json")} == {"P01.json", "D01.json"}  # manifest is .jsonl
        tr = json.loads((d / "P01.json").read_text(encoding="utf-8"))
        assert tr["mode"] == mode and tr["run_id"] == f"e3_{mode}" and tr["e3_batch"]["kind"] == "patient"
        m = json.loads((d / "_manifest.jsonl").read_text(encoding="utf-8"))
        assert m["model"] == "fake-llm" and m["questions_sha"] and m["file_sha"]["agent/verifier.py"]
    agent_p01 = json.loads((t / "e3_agent" / "P01.json").read_text(encoding="utf-8"))
    assert agent_p01["tool_calls"] and agent_p01["verifier"]["n_supported"] >= 1

    capsys.readouterr()
    assert e3_batch.main(args) == 0  # resume: everything already done
    assert "nothing to run" in capsys.readouterr().out

    assert e3_batch.main(args + ["--reverify"]) == 0
    rv = json.loads((t / "e3_agent_rv" / "P01.json").read_text(encoding="utf-8"))
    assert rv["draft"] == agent_p01["draft"] and rv["tool_calls"] == agent_p01["tool_calls"]
    assert rv["verifier"] == agent_p01["verifier"]  # same verifier, same verdicts
    assert rv["reverified"]["previous_verifier"] == agent_p01["verifier"] and rv["run_id"] == "e3_agent_rv"


def test_failure_is_logged_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(e3_batch.C, "MOCK_MODE", "1")
    q, t = _qfile(tmp_path), tmp_path / "t"

    def boom(*a, **k):
        raise RuntimeError("NIM error 503")
    monkeypatch.setattr(e3_batch.graph_agent, "run", boom)
    rc = e3_batch.main(["--questions", str(q), "--traces-dir", str(t), "--allow-mock", "--mode", "llm_only"])
    assert rc == 1
    errs = (t / "e3_llm_only" / "_errors.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(errs) == 2 and "NIM error 503" in errs[0]
    assert not list((t / "e3_llm_only").glob("P01.json"))
