"""Offline tests for eval/e3_score.py: a scripted judge, no network.

    python -m pytest tests/test_e3_score.py -q
"""
import csv
import json

from eval import e3_score as S

QS = [
    {"qid": "P10", "kind": "patient", "trap": True, "question": "Does this patient carry a germline VHL mutation?",
     "must_mention": ["cannot be determined", "germline testing"], "expected": {"notes": "needs germline DNA"}},
    {"qid": "D02", "kind": "disease", "trap": False, "question": "Why does VHL loss lead to tumor angiogenesis?",
     "must_mention": ["PHD hydroxylation", "proteasomal degradation", "VEGF"], "expected": {"notes": "PHD..."}},
]

ANS = {
    ("agent", "P10"): "**Summary**\nGermline VHL status cannot be determined from tumor proteomics [pt:X:protein_down:NCBIGene:7428]; "
                      "it requires germline testing of constitutional DNA.\n\n_Verifier: removed 1 claim(s) that had no supporting edge or PMID._",
    ("agent", "D02"): "VHL loss stabilizes HIF‑2α [E1], which drives VEGF transcription [PMID:123].",
    ("llm_only", "P10"): "Yes, the patient most likely carries a germline VHL mutation.",
    ("llm_only", "D02"): "Prolyl hydroxylases (PHD hydroxylation) mark HIF for proteasomal degradation; VHL loss lets VEGF rise.",
}


class Judge:
    """Scripted judge: returns covered points with a quote taken from the answer it was shown."""
    model = "fake-judge"
    seen = []

    def chat(self, messages, tools=None, json_mode=False, purpose="agent"):
        user = messages[-1]["content"]
        Judge.seen.append(user)
        answer = user.split("ANSWER:\n", 1)[1]
        pts = [l[2:] for l in user.split("POINTS:\n", 1)[1].split("\n\nANSWER:")[0].splitlines()]
        out = []
        for p in pts:
            key = {"cannot be determined": "cannot be determined", "germline testing": "germline testing",
                   "PHD hydroxylation": "PHD hydroxylation", "proteasomal degradation": "proteasomal degradation",
                   "VEGF": "VEGF"}[p]
            i = answer.find(key)
            out.append({"point": p, "covered": i >= 0, "quote": answer[max(0, i - 5):i + len(key) + 10] if i >= 0 else ""})
        if "germline VHL" in user and "Yes" in answer:  # a judge that hallucinates a quote
            out[0] = {"point": pts[0], "covered": True, "quote": "this text is not in the answer"}
        abst = "cannot be determined" in answer
        return {"content": json.dumps({"points": out, "abstains": abst,
                                       "abstain_quote": "cannot be determined" if abst else ""}), "tool_calls": []}


def _setup(tmp_path):
    qf = tmp_path / "q.jsonl"
    qf.write_text("\n".join(json.dumps(q) for q in QS), encoding="utf-8")
    dirs = []
    for mode in ("agent", "llm_only"):
        d = tmp_path / f"e3_{mode}"
        d.mkdir()
        for qid in ("P10", "D02"):
            (d / f"{qid}.json").write_text(json.dumps({
                "mode": mode, "qid": qid, "answer": ANS[(mode, qid)], "notes": [],
                "verifier": {"n_claims": 2, "n_supported": 2 if mode == "agent" else 0}}), encoding="utf-8")
        (d / "_manifest.jsonl").write_text("{}\n", encoding="utf-8")
        dirs.append(str(d))
    return qf, dirs


def test_blind_strips_citations_and_footer():
    b = S.blind(ANS[("agent", "P10")])
    assert "[" not in b and "_Verifier" not in b and "cannot be determined" in b


def test_keyword_alternatives():
    assert S.keyword_covered("HIF-2alpha or EPAS1", "the EPAS1 protein")
    assert S.keyword_covered("TSC1/TSC2", "loss of TSC2")
    assert S.keyword_covered("HIF-2alpha", "HIF‑2α is stable")  # unicode hyphen + alpha
    assert not S.keyword_covered("VEGFR", "VEGF only")


def test_scoring_end_to_end(tmp_path):
    Judge.seen.clear()
    qf, dirs = _setup(tmp_path)
    out = tmp_path / "res"
    assert S.main(dirs + ["--questions", str(qf), "--out-dir", str(out), "--workers", "2"], llm_factory=Judge) == 0
    rows = {(r["mode"], r["qid"]): r for r in csv.DictReader(open(out / "e3_quality.csv", encoding="utf-8"))}
    assert len(rows) == 4
    assert rows[("agent", "P10")]["abstention_correct"] == "True" and rows[("agent", "P10")]["gold_recall"] == "1.0"
    assert rows[("llm_only", "P10")]["abstention_correct"] == "False"
    assert rows[("llm_only", "P10")]["n_unverified_quotes"] == "1"  # hallucinated quote isn't credited
    assert rows[("llm_only", "P10")]["gold_recall"] == "0.0"
    assert rows[("agent", "D02")]["gold_recall"] == "0.333" and rows[("agent", "D02")]["false_abstention"] == "False"
    assert rows[("llm_only", "D02")]["gold_recall"] == "1.0"
    s = json.loads((out / "e3_summary.json").read_text(encoding="utf-8"))["by_mode"]
    assert s["agent"]["abstention_accuracy"] == "1/1" and s["llm_only"]["abstention_accuracy"] == "0/1"
    assert s["agent"]["claims_supported"] == "4/4"
    assert all("[E" not in u and "PMID" not in u and "_Verifier" not in u for u in Judge.seen)  # judged blind
    det = [json.loads(l) for l in open(out / "e3_quality_detail.jsonl", encoding="utf-8")]
    assert len(det) == 4 and all(d["judge"] == "fake-judge" for d in det)


def test_keyword_only_mode(tmp_path):
    qf, dirs = _setup(tmp_path)
    out = tmp_path / "res"

    def no_llm():
        raise AssertionError("must not call the LLM")
    assert S.main(dirs + ["--questions", str(qf), "--out-dir", str(out), "--no-llm"], llm_factory=no_llm) == 0
    rows = {(r["mode"], r["qid"]): r for r in csv.DictReader(open(out / "e3_quality.csv", encoding="utf-8"))}
    assert rows[("agent", "P10")]["judge"] == "keyword" and rows[("agent", "P10")]["abstains"] == ""


def test_judge_failure_falls_back(tmp_path):
    qf, dirs = _setup(tmp_path)

    class Broken:
        model = "broken"

        def chat(self, *a, **k):
            raise RuntimeError("NIM error 503")
    assert S.main(dirs + ["--questions", str(qf), "--out-dir", str(tmp_path / "r")], llm_factory=Broken) == 0
    rows = list(csv.DictReader(open(tmp_path / "r" / "e3_quality.csv", encoding="utf-8")))
    assert all(r["judge"] == "keyword" for r in rows)
