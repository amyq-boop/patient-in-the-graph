"""E3 answer quality: gold recall and abstention accuracy (the two metrics in eval/README.md that
the verifier counts don't cover).

    python -m eval.e3_score eval/traces/e3_agent eval/traces/e3_llm_only
    python -m eval.e3_score eval/traces/e3_agent_rv eval/traces/e3_llm_only_rv --workers 4
    python -m eval.e3_score ... --no-llm          # keyword check only (no NIM calls)

Gold recall      share of a question's `must_mention` points the final answer covers. It stops an
                 agent from scoring well on grounding by saying less.
Abstention       on trap questions (`"trap": true`: P02, P09, P10) the right answer is "the evidence
                 can't settle this". We also count false abstentions on non-trap questions.

How it's judged
- An LLM judge (the same NIM model, temperature 0, responses cached in cache/llm/) reads the
  question, the gold notes, the must_mention points and the answer, and returns per-point verdicts.
- It judges blind: citations ([E...], [PMID:...], [pt:...]) and the verifier footer are stripped first,
  so it can't tell agent answers from LLM-only answers.
- A "covered" verdict counts only if the judge's supporting quote really occurs in the answer;
  otherwise it's recorded as `unverified` and counted as not covered.
- A plain keyword match runs alongside as a sanity check (`keyword_recall`); a big gap between
  the two is worth a look in e3_quality_detail.jsonl.

Outputs (eval/results/): e3_quality.csv (one row per mode x qid), e3_quality_detail.jsonl
(every per-point verdict and quote, for the hand-check), e3_summary.json (both metrics plus the
verifier's grounding numbers, per mode).
"""
import argparse
import csv
import json
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from agent import config as C

QUESTIONS = C.ROOT / "eval" / "questions.jsonl"
CITE = re.compile(r"\s*\[[^\]]*\b(?:E\d+|PMID:\s*\d+|pt:|pred:|contrib:|rank:)[^\]]*\]", re.I)
FOOTER = re.compile(r"_Verifier:.*?_\s*$", re.S | re.M)
_lock = threading.Lock()

JUDGE_SYSTEM = """You grade answers to biomedical questions against a gold standard. Be strict and literal.

You get: the QUESTION, the GOLD NOTES (what a correct answer says), the POINTS the answer must cover, and the ANSWER.

For each point decide whether the ANSWER covers it, in substance (paraphrase is fine; the exact words are not required). A point is covered only if the answer actually states it, not if it merely mentions a related word. For each covered point, copy a short verbatim quote (at most 25 words) from the ANSWER that shows it. Copy it exactly, character for character.

Then decide "abstains": true if the answer's main conclusion is that the available evidence cannot determine or settle the question asked (for example "cannot be determined", "insufficient evidence", "requires germline testing"), false if it gives a substantive answer. Hedging or caveats alone are not abstention. Give a verbatim quote for it too.

Return JSON only:
{"points": [{"point": "<the point, as given>", "covered": true, "quote": "<verbatim quote or empty>"}], "abstains": false, "abstain_quote": "<verbatim quote or empty>"}"""


def log(msg):
    with _lock:
        print(msg, flush=True)


def norm(s):
    s = (s or "").lower()
    s = re.sub(r"[‐-―−]", "-", s)            # unicode hyphens/dashes -> '-'
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    s = s.replace("α", "alpha").replace("β", "beta")
    s = re.sub(r"[*_`#>]", "", s)                            # markdown
    return re.sub(r"\s+", " ", s).strip()


def blind(answer):
    """The answer the judge sees: no citations, no verifier footer."""
    return re.sub(r"[ \t]+\n", "\n", CITE.sub("", FOOTER.sub("", answer or ""))).strip()


def keyword_covered(point, answer):
    """Crude check: any alternative of the point ('A or B', 'A/B') appears in the answer."""
    a = norm(answer)
    alts = [norm(x) for x in re.split(r"\s+or\s+|/", point) if x.strip()]
    return any(x and x in a for x in alts)


def parse_json(s):
    s = re.sub(r"^```(?:json)?|```$", "", (s or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", s, re.S)
    return json.loads(m.group(0) if m else s)


def judge(llm, q, answer):
    points = q.get("must_mention") or []
    user = (f"QUESTION:\n{q['question']}\n\nGOLD NOTES:\n{q.get('expected', {}).get('notes', '')}\n\n"
            f"POINTS:\n" + "\n".join(f"- {p}" for p in points) + f"\n\nANSWER:\n{answer}")
    out = llm.chat([{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}],
                   json_mode=True, purpose="judge")
    j = parse_json(out["content"])
    got = {norm(p.get("point", "")): p for p in j.get("points", []) if isinstance(p, dict)}
    verdicts = []
    for i, p in enumerate(points):
        v = got.get(norm(p)) or (j["points"][i] if i < len(j.get("points", [])) and isinstance(j["points"][i], dict) else {})
        quote = str(v.get("quote") or "")
        said = bool(v.get("covered"))
        found = bool(quote) and norm(quote) in norm(answer)
        verdicts.append({"point": p, "judge_says": said, "quote": quote,
                         "verdict": "covered" if said and found else ("unverified" if said else "missing")})
    aq = str(j.get("abstain_quote") or "")
    abst = bool(j.get("abstains"))
    if abst and aq and norm(aq) not in norm(answer):
        abst_verdict = "unverified"
    else:
        abst_verdict = "abstains" if abst else "answers"
    return verdicts, abst_verdict, aq


def score_trace(path, qs, llm_factory, use_llm):
    t = json.loads(path.read_text(encoding="utf-8"))
    q = qs.get(t["qid"]) or next((x for x in qs.values() if norm(x["question"]) == norm(t.get("question"))), None)
    if q is None:  # ad-hoc runs (qid "adhoc") are matched by question text
        return None, f"qid {t['qid']} not in questions file and question text doesn't match one"
    answer = blind(t.get("answer", ""))
    points = q.get("must_mention") or []
    kw = [keyword_covered(p, answer) for p in points]
    verdicts, abst, aq, judge_name, err = None, None, "", "keyword", None
    if use_llm and answer:
        llm = llm_factory()
        try:
            verdicts, abst, aq = judge(llm, q, answer)
            judge_name = getattr(llm, "model", "llm")
        except Exception as e:
            err = f"judge failed ({type(e).__name__}: {str(e)[:120]}); keyword check only"
    if verdicts is None:  # keyword-only fallback
        verdicts = [{"point": p, "judge_says": None, "quote": "", "verdict": "covered" if k else "missing"}
                    for p, k in zip(points, kw)]
    n_cov = sum(v["verdict"] == "covered" for v in verdicts)
    trap = bool(q.get("trap"))
    row = {
        "mode": t["mode"], "qid": q["qid"], "kind": q.get("kind"), "trap": trap,
        "n_points": len(points), "n_covered": n_cov,
        "gold_recall": round(n_cov / len(points), 3) if points else "",
        "keyword_recall": round(sum(kw) / len(points), 3) if points else "",
        "n_unverified_quotes": sum(v["verdict"] == "unverified" for v in verdicts),
        "abstains": "" if abst is None else abst,
        "abstention_correct": "" if abst is None or not trap else abst == "abstains",
        "false_abstention": "" if abst is None or trap else abst == "abstains",
        "judge": judge_name,
        "answer_chars": len(answer),
        "n_supported": t.get("verifier", {}).get("n_supported", ""),
        "n_claims": t.get("verifier", {}).get("n_claims", ""),
    }
    detail = {"mode": t["mode"], "qid": q["qid"], "trace": str(path), "points": verdicts,
              "abstains": abst, "abstain_quote": aq, "judge": judge_name, "error": err}
    return (row, detail), err


def summarize(rows):
    out = {}
    for mode in sorted({r["mode"] for r in rows}):
        rs = [r for r in rows if r["mode"] == mode]
        pts = sum(r["n_points"] for r in rs)
        traps = [r for r in rs if r["trap"] and r["abstains"] != ""]
        non = [r for r in rs if not r["trap"] and r["abstains"] != ""]
        claims = sum(int(r["n_claims"] or 0) for r in rs)
        out[mode] = {
            "answers": len(rs),
            "gold_recall": round(sum(r["n_covered"] for r in rs) / pts, 3) if pts else None,
            "gold_recall_non_trap": (lambda p: round(sum(r["n_covered"] for r in rs if not r["trap"]) / p, 3) if p else None)(
                sum(r["n_points"] for r in rs if not r["trap"])),
            "keyword_recall": round(sum(r["keyword_recall"] * r["n_points"] for r in rs if r["n_points"]) / pts, 3) if pts else None,
            "abstention_accuracy": f"{sum(r['abstention_correct'] is True for r in traps)}/{len(traps)}" if traps else None,
            "false_abstentions": f"{sum(r['false_abstention'] is True for r in non)}/{len(non)}" if non else None,
            "claims_supported": f"{sum(int(r['n_supported'] or 0) for r in rs)}/{claims}" if claims else None,
            "unverified_judge_quotes": sum(r["n_unverified_quotes"] for r in rs),
        }
    return out


def main(argv=None, llm_factory=None):
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="E3 gold recall + abstention accuracy")
    ap.add_argument("dirs", nargs="+", help="trace dirs, e.g. eval/traces/e3_agent eval/traces/e3_llm_only")
    ap.add_argument("--questions", default=str(QUESTIONS))
    ap.add_argument("--out-dir", default=str(C.RESULTS))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-llm", action="store_true", help="keyword check only")
    ap.add_argument("--allow-mock", action="store_true", help="keep traces whose tools fell back to mock data")
    a = ap.parse_args(argv)

    if llm_factory is None:
        from agent.llm import get_llm
        llm_factory = get_llm
    qs = {q["qid"]: q for q in (json.loads(l) for l in Path(a.questions).read_text(encoding="utf-8").splitlines() if l.strip())}
    paths = []
    for d in a.dirs:
        for p in sorted(Path(d).glob("*.json")):
            if p.name.startswith("_"):
                continue
            t = json.loads(p.read_text(encoding="utf-8"))
            if not a.allow_mock and any("MOCK" in str(n) for n in t.get("notes", [])):
                log(f"  ! skipped {p}: tools fell back to mock data")
                continue
            paths.append(p)
    if not paths:
        sys.exit("no traces to score")
    log(f"scoring {len(paths)} answer(s) {'(keyword only)' if a.no_llm else 'with the LLM judge'}")

    rows, details = [], []
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(score_trace, p, qs, llm_factory, not a.no_llm): p for p in paths}
        for f in as_completed(futs):
            res, err = f.result()
            if err:
                log(f"  ! {futs[f].name}: {err}")
            if res:
                rows.append(res[0])
                details.append(res[1])
    if not rows:
        sys.exit("no answers could be scored (see messages above)")
    rows.sort(key=lambda r: (r["mode"], r["qid"]))
    details.sort(key=lambda d: (d["mode"], d["qid"]))

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "e3_quality.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    with open(out / "e3_quality_detail.jsonl", "w", encoding="utf-8") as f:
        for d in details:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    summary = {"created": datetime.now().isoformat(timespec="seconds"), "questions": str(a.questions),
               "trace_dirs": [str(d) for d in a.dirs], "by_mode": summarize(rows)}
    (out / "e3_summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")

    log(f"\nwrote {out / 'e3_quality.csv'}, e3_quality_detail.jsonl, e3_summary.json")
    for mode, s in summary["by_mode"].items():
        log(f"  {mode:9s} gold recall {s['gold_recall']}  (non-trap {s['gold_recall_non_trap']}, keyword {s['keyword_recall']})   "
            f"abstention {s['abstention_accuracy']}   false abstentions {s['false_abstentions']}   "
            f"claims supported {s['claims_supported']}")
        if s["unverified_judge_quotes"]:
            log(f"            {s['unverified_judge_quotes']} judge verdict(s) had a quote not found in the answer (counted as missing)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
