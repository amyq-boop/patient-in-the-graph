"""E4 across a pre-registered patient cohort: does the agent rediscover ccRCC's masked mechanism?

eval/e4_case_study.py stays the single, human-reviewed case (eval/results/e4.json). This script reuses its checks
(load_key, validate_trace, leak_check, audit_graph, supported_claims, mentions, QUESTION) without changing them.

    python -m eval.e4_cohort --plan                 # 1. pre-register the patients -> eval/e4_cohort_plan.json; commit it
    python -m eval.e4_cohort --run --limit 1        #    smoke run (allowed before the plan is committed) -> e4_cohort_smoke/
    python -m eval.e4_cohort --run                  # 2. all planned patients, 3 at a time, resumable
    python -m eval.e4_cohort --score                # 3-5. leak audit + auto-score -> eval/results/e4_cohort.{json,csv}
    python -m eval.e4_cohort --sample 5             # 6. hand-check sheet -> eval/results/e4_cohort_handcheck.csv
    python -m eval.e4_cohort --agreement            #    human vs auto agreement, once the sheet is filled in
    python -m eval.e4_cohort --run --variant full   # 7. optional ceiling on the unmasked graph -> e4_cohort_full/
    python -m eval.e4_cohort --reverify             #    re-check saved drafts with the current verifier (no LLM)
Exploratory arms (after the pre-registered run; each needs --tag, so it gets its own trace folder and results file):
    python -m eval.e4_cohort --run --hub 0 --tag hub0                 # same patients, AGENT_HUB=0 (E1's retriever setting)
    python -m eval.e4_cohort --plan --plan-file eval/e4_cohort_plan_b.json --seed 2027 --exclude-plan eval/e4_cohort_plan.json \
        --note "..."                                                   # a fresh, disjoint draw; commit it before running
    python -m eval.e4_cohort --run --plan-file eval/e4_cohort_plan_b.json --tag b            # plan B, default retrieval
    python -m eval.e4_cohort --run --plan-file eval/e4_cohort_plan_b.json --hub 0 --tag b_hub0
                                                    #    -> e4_cohort_rv/ and eval/results/e4_cohort_rv.{json,csv}

Settings are fixed for every patient: BIOHACK_VARIANT=e4 (full for the ceiling), BIOHACK_MOCK=0, VERIFIER_STRICT=1,
VERIFIER_SPLITTER=rules, AGENT_DRUG_TARGETS=0 (the setup E3, the demo and the single E4 case used). Conflicting
values already in the environment stop the run instead of being overridden.

Auto-scoring rules (written before any run):
- A step (each core_chain step and each downstream_of_hif2 item) is recovered iff a verifier-SUPPORTED claim mentions
  one of the step's accept_terms or its label, AND cites >= 1 ID present in that trace's observed evidence (evidence
  rebuilt from the tool results by e4_case_study.supported_claims). `recovered_kg` additionally requires one of those
  IDs to be a graph edge (E...) or PMID, not only a patient or model ID; it is reported alongside, not instead.
- drugs_surfaced: supported claims naming sunitinib, pazopanib, axitinib or cabozantinib (belzutifan's only links
  are masked on e4). e4_case_study.EXTRA_DRUGS are reported separately as a bonus.
- A trace that fails the leak audit (leak_check or audit_graph) is invalid: it is listed with its reason and left
  out of the rates, never dropped silently. Other validation failures are reported the same way.
- Added after the full-graph ceiling run (Nov 8, ~00:00), a parser fix, not a rule change: citations written in
  parentheses, e.g. "(PMID:37085424)", count like [brackets], as they do in the verifier (bracket_paren_citations).
  It made 11 of 20 ceiling traces invalid and changes no masked-run result.
- Added Nov 8 (~01:20), a matching fix, not a rule change: a hyphen between two letters reads as a space when a
  claim is matched to a step or drug (mentions), so "Growth-factor pathway activation" mentions "growth factor".
  e4_case_study's matcher missed these. Pre-registered masked result unchanged; ceiling growth factor 25% -> 80%.
"""
import argparse
import csv
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from common.contracts import P, ROOT
from eval import e4_case_study as E4

PLAN = ROOT / "eval" / "e4_cohort_plan.json"
TRACE_DIRS = {"e4": P.traces / "e4_cohort", "full": P.traces / "e4_cohort_full", "smoke": P.traces / "e4_cohort_smoke"}
SEED = 2026
N_VHL, N_NON_VHL = 15, 5
VHL = "NCBIGene:7428"
SCORED_DRUGS = ("sunitinib", "pazopanib", "axitinib", "cabozantinib")
RULE = (f"From patients with GNN-A predictions (gnn_a/predictions.parquet, model == 'gnn'): sort IDs, then with "
        f"random.Random({SEED}) draw {N_VHL} without replacement from those with a somatic VHL mutation ({VHL} in "
        f"patients/patient_mutations.parquet), then {N_NON_VHL} from those that have mutation data but no VHL "
        f"mutation. Patients with no mutation data are excluded from the second group.")
SETTINGS = {"BIOHACK_MOCK": "0", "VERIFIER_STRICT": "1", "VERIFIER_SPLITTER": "rules", "AGENT_DRUG_TARGETS": "0"}
_lock = threading.Lock()


def log(msg):
    with _lock:
        print(msg, flush=True)


def sha_file(p):
    p = Path(p)
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else None


def git(*args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        return subprocess.run(["git", "-c", "core.autocrlf=true", *args], cwd=ROOT, capture_output=True, text=True,
                              env=env, timeout=20).stdout.rstrip()
    except Exception:
        return ""


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- 1. plan

def eligible_pools(predictions=None, mutations=None):
    """(VHL-mutated, mutation data but no VHL, no mutation data), each a sorted list of patient IDs with predictions."""
    import pandas as pd
    pred = pd.read_parquet(predictions or P.predictions)
    mut = pd.read_parquet(mutations or P.patient_mutations)
    with_pred = set(pred.loc[pred.model == "gnn", "patient_id"].astype(str))
    with_mut = set(mut.patient_id.astype(str))
    vhl = set(mut.loc[mut.curie == VHL, "patient_id"].astype(str))
    return sorted(with_pred & vhl), sorted((with_pred & with_mut) - vhl), sorted(with_pred - with_mut)


def draw(vhl, non_vhl, seed=SEED, n_vhl=N_VHL, n_non=N_NON_VHL):
    rng = random.Random(seed)
    return rng.sample(sorted(vhl), n_vhl), rng.sample(sorted(non_vhl), n_non)


def make_plan(force=False, predictions=None, mutations=None, path=PLAN, seed=SEED, exclude_plans=(), note=None):
    """exclude_plans: earlier plan files whose patients, and every patient used in development, are left out of the
    pools (a fresh, disjoint draw). Without it the pre-registered rule is unchanged."""
    path = Path(path)
    if path.exists() and not force:
        sys.exit(f"{path} already exists. A pre-registration is written once; pass --force only if it was never run.")
    vhl, non, none = eligible_pools(predictions, mutations)
    used = _patients_used_in_development()
    rule = RULE if seed == SEED else RULE.replace(f"random.Random({SEED})", f"random.Random({seed})")
    excluded = []
    if exclude_plans:
        prev = {p["patient_id"] for f in exclude_plans for p in load_plan(f)["patients"]}
        excluded = sorted((prev | used) & (set(vhl) | set(non)))
        vhl, non = [p for p in vhl if p not in excluded], [p for p in non if p not in excluded]
        rule += (" Before drawing, patients in " + ", ".join(str(Path(f).name) for f in exclude_plans)
                 + " and every patient used in development are removed from both pools.")
    a, b = draw(vhl, non, seed=seed)
    plan = {
        "eval": "E4 cohort: masked ccRCC mechanism recovery across patients",
        "rule": rule, "seed": seed,
        **({"excluded_before_draw": excluded, "excluded_plans": [str(Path(f).name) for f in exclude_plans]}
           if exclude_plans else {}),
        **({"note": note} if note else {}),
        "patients": [{"patient_id": p, "group": "vhl_mutated"} for p in a]
                    + [{"patient_id": p, "group": "no_vhl_mutation"} for p in b],
        "pool_sizes": {"vhl_mutated": len(vhl), "no_vhl_mutation_with_data": len(non), "no_mutation_data": len(none)},
        "question": E4.QUESTION, "qid": E4.QID,
        "settings": {**SETTINGS, "BIOHACK_VARIANT": "e4"},
        "scoring": __doc__.split("Auto-scoring rules (written before any run):", 1)[1].strip(),
        "answer_key_sha256": sha_file(E4.KEY_PATH),
        "git_commit": git("rev-parse", "--short", "HEAD") or None,
        "git_dirty_files": [l[3:] for l in git("status", "--porcelain", "--untracked-files=no").splitlines()],
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note_development_overlap": ("Sampled patients that were also used while building the agent (E3 questions, demo, "
                                     "single E4 case). Reported, not excluded: " + (", ".join(
                                         p for p in a + b if p in used) or "none")),
    }
    write_json(path, plan)
    return plan


def _patients_used_in_development():
    used = {"C3L-00448", "C3N-01649", "C3L-00416", "C3N-00310"}  # demo candidates + single E4 case
    q = ROOT / "eval" / "questions.jsonl"
    if q.exists():
        for line in q.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("patient_id"):
                used.add(json.loads(line)["patient_id"])
    return used


def load_plan(path=PLAN):
    path = Path(path)
    if not path.exists():
        sys.exit(f"No plan at {path}. Run --plan first, commit it, then --run.")
    return json.loads(path.read_text(encoding="utf-8"))


def plan_committed(path=PLAN):
    rel = str(Path(path).resolve().relative_to(ROOT)).replace("\\", "/")
    tracked = bool(git("ls-files", "--", rel))
    dirty = bool(git("status", "--porcelain", "--", rel))
    return tracked and not dirty


# ---------------------------------------------------------------- 2. run

def apply_settings(variant, hub=None):
    """hub: AGENT_HUB for an exploratory arm. Without it, an AGENT_HUB already in the environment is a clash."""
    want = {**SETTINGS, "BIOHACK_VARIANT": variant}
    if hub is not None:
        want["AGENT_HUB"] = f"{hub:g}"
    elif os.environ.get("AGENT_HUB", "").strip():
        sys.exit(f"AGENT_HUB={os.environ['AGENT_HUB']!r} is set; unset it, or pass --hub and --tag for an exploratory arm")
    clash = {k: (os.environ[k], v) for k, v in want.items() if k in os.environ and os.environ[k] != v}
    if clash:
        sys.exit("Environment conflicts with the fixed E4 cohort settings (have, need): "
                 + ", ".join(f"{k}={a!r} vs {b!r}" for k, (a, b) in clash.items()))
    os.environ.update(want)
    return want


def run_cohort(plan, variant="e4", workers=3, limit=None, out_dir=None, force=False, hub=None, tag=None):
    want = apply_settings(variant, hub)
    from agent import config as C  # imported only after the settings are in the environment
    from agent import prompts
    from agent.graph_agent import run
    from agent.llm import get_llm
    from eval.e3_batch import manifest, warm_up, write_manifest
    assert C.VARIANT == variant and not C.DRUG_TARGETS_CCRCC, "agent config did not pick up the E4 settings"
    assert C.AGENT_HUB == hub, "agent config did not pick up AGENT_HUB"

    smoke = limit is not None
    out = Path(out_dir or TRACE_DIRS["smoke" if smoke else ("full" if variant == "full" else "e4")])
    pts = plan["patients"][: limit] if smoke else plan["patients"]
    head = git("rev-parse", "--short", "HEAD") or None
    mpath = out / "_manifest.jsonl"
    if mpath.exists() and not force:
        old = json.loads(mpath.read_text(encoding="utf-8").splitlines()[0])
        if old.get("git_commit") != head or old.get("prompt_sha") != _prompt_sha(prompts):
            sys.exit(f"{out} was started on commit {old.get('git_commit')} (prompt {old.get('prompt_sha')}); now "
                     f"{head} (prompt {_prompt_sha(prompts)}). Use a new --out-dir, or --force to restart it.")
    else:
        m = manifest(PLAN, {"eval": "e4_cohort", "variant": variant, "smoke": smoke, "tag": tag,
                            "plan_sha": sha_file(PLAN), "prompt_sha": _prompt_sha(prompts),
                            "answer_key_sha256": sha_file(E4.KEY_PATH), "fixed_settings": want,
                            "patients": [p["patient_id"] for p in pts]})
        m["git_commit"] = head  # the value the resume check compares against
        write_manifest(out, m)
    todo = [p for p in pts if force or not _done(out / f"{p['patient_id']}.json")]
    log(f"E4 cohort: {len(pts)} patient(s), {len(pts) - len(todo)} already done, variant={variant}, "
        f"settings={want} -> {out}")
    if not todo:
        return out
    warm_up([{"patient_id": todo[0]["patient_id"]}])

    def one(p):
        t0 = time.perf_counter()
        trace = run(E4.QUESTION, patient_id=p["patient_id"], qid=E4.QID, llm=get_llm(),
                    run_id=f"e4_cohort_{variant}{'_smoke' if smoke else ''}{'_' + tag if tag else ''}")
        trace["e4_cohort"] = {"group": p["group"], "plan_sha": sha_file(PLAN), "plan": PLAN.name, "tag": tag,
                              "agent_hub": hub}
        write_json(out / f"{p['patient_id']}.json", trace)
        return trace, time.perf_counter() - t0

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, p): p for p in todo}
        for i, f in enumerate(as_completed(futs), 1):
            p = futs[f]
            try:
                t, secs = f.result()
                v = t["verifier"]
                log(f"[{i}/{len(todo)}] {p['patient_id']} ({p['group']})  supported {v['n_supported']}/{v['n_claims']}"
                    f"  {secs:.0f}s" + ("".join(f"  !! {n}" for n in t.get("notes", [])
                                                if n.startswith(("MOCK", "LLM output cut", "tool error")))))
            except Exception as e:
                with _lock, open(out / "_errors.jsonl", "a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"patient_id": p["patient_id"], "error": f"{type(e).__name__}: {e}",
                                         "traceback": traceback.format_exc()}) + "\n")
                log(f"[{i}/{len(todo)}] {p['patient_id']} FAILED: {type(e).__name__}: {str(e)[:150]}")
    return out


def reverify_cohort(src, dst):
    """Re-run only the verifier on each saved draft (deterministic with VERIFIER_SPLITTER=rules: no LLM). The draft,
    tool calls and evidence are unchanged; claims, answer and verifier stats are replaced, and the trace records
    where it came from and the previous stats. The original folder is not touched."""
    from agent import graph_agent
    from agent.verifier import verify
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(p for p in Path(src).glob("*.json") if not p.name.startswith("_")):
        t = json.loads(f.read_text(encoding="utf-8"))
        calls = t.get("tool_calls", [])
        claims, answer, stats, splitter = verify(t["draft"], t["evidence"], None,
                                                 edge_names=graph_agent._edge_names(calls))
        new = {**t, "claims": claims, "answer": answer, "verifier": stats, "verifier_splitter": splitter,
               "reverified": {"from": f"{Path(src).name}/{f.name}", "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              "git_commit": git("rev-parse", "--short", "HEAD"), "verifier_sha": sha_file(ROOT / "agent" / "verifier.py"),
                              "previous_verifier": {k: v for k, v in t.get("verifier", {}).items() if k != "non_claims"}}}
        write_json(dst / f.name, new)
        n += 1
    log(f"re-verified {n} trace(s): {src} -> {dst}")
    return n


def _prompt_sha(prompts):
    from agent import config as C
    return hashlib.sha256(prompts.agent_system("ccrcc", C.drug_targets_enabled("ccrcc")).encode()).hexdigest()[:16]


def _done(path):
    try:
        t = json.loads(Path(path).read_text(encoding="utf-8"))
        return "verifier" in t and "claims" in t
    except Exception:
        return False


# ---------------------------------------------------------------- 3-5. audit + score

def bracket_paren_citations(trace):
    """A copy of `trace` whose claim quotes write parenthesised citation groups as brackets: "(PMID:1, E2)" ->
    "[PMID:1, E2]". The verifier has counted (...) citations since Nov 7 (agent/verifier.py, CONTRACTS §8), while
    e4_case_study.cited_in_quote reads [...] only, so a supported "(PMID:...)" made the trace invalid. Scoring
    rules are unchanged; only the quote text handed to e4_case_study is normalised. Parentheses without an ID
    ("(stage III)") are left alone."""
    import copy
    from agent.verifier import ID_TOKEN, PAREN
    t = copy.deepcopy(trace)
    for c in t.get("claims", []):
        if c.get("quote"):
            c["quote"] = PAREN.sub(lambda m: f"[{m.group(1)}]" if ID_TOKEN.search(m.group(1)) else m.group(0), c["quote"])
    return t


_WORD_HYPHEN = re.compile(r"(?<=[A-Za-z])[-\u2010\u2011](?=[A-Za-z])")


def mentions(text, terms):
    """e4_case_study.mentions, also reading a hyphen between two letters as a space on both sides, so
    "Growth-factor pathway activation" mentions "growth factor". Hyphens next to a digit (HIF-2alpha, C3N-00852)
    are left as they are."""
    if E4.mentions(text, terms):
        return True
    return E4.mentions(_WORD_HYPHEN.sub(" ", text or ""), [_WORD_HYPHEN.sub(" ", t) for t in terms])


def score_trace(trace, key, audit=E4.audit_graph, ceiling=False):
    """One patient's row. Invalid traces come back with valid=False and the reason; nothing raises."""
    trace = bracket_paren_citations(trace)
    row = {"patient_id": trace.get("patient_id"), "group": (trace.get("e4_cohort") or {}).get("group"),
           "variant": trace.get("variant"), "valid": True, "leak": False, "invalid_reason": ""}
    try:
        if not ceiling:
            E4.validate_trace(trace)
            problems = E4.leak_check(trace, key)
            if problems:
                row.update(valid=False, leak=True, invalid_reason="; ".join(problems))
                return row, []
            try:
                audit(trace, key)
            except E4.E4Error as e:
                row.update(valid=False, leak="masked graph edges" in str(e), invalid_reason=f"graph audit: {e}")
                return row, []
        claims = E4.supported_claims(trace)
    except E4.E4Error as e:
        row.update(valid=False, invalid_reason=str(e))
        return row, []

    quotes = {c["claim_id"]: c.get("quote") or c["text"] for c in trace.get("claims", [])}
    matches = []
    for s in key["core_chain"] + key["downstream_of_hif2"]:
        terms = list(s["accept_terms"]) + [s["label"]]
        hit = [cid for cid, c in claims.items() if mentions(c["text"], terms) or mentions(quotes.get(cid, ""), terms)]
        kg = [cid for cid in hit if claims[cid]["mechanism_ids"]]
        row[f"step:{s['label']}"] = bool(hit)
        row[f"step_kg:{s['label']}"] = bool(kg)
        matches.append({"step": s["label"], "claim_ids": hit, "kg_claim_ids": kg})

    def named(names):
        return [n for n in names if any(mentions(c["text"], [n]) or mentions(quotes.get(cid, ""), [n])
                                        for cid, c in claims.items())]
    row["n_steps_recovered"] = sum(bool(m["claim_ids"]) for m in matches)
    row["n_steps_recovered_kg"] = sum(bool(m["kg_claim_ids"]) for m in matches)
    row["drugs_surfaced"] = named(SCORED_DRUGS)
    row["n_drugs_surfaced"] = len(row["drugs_surfaced"])
    row["extra_drugs"] = named(E4.EXTRA_DRUGS)
    row["n_supported_claims"] = len(claims)
    return row, matches


def score_cohort(plan, trace_dir, key=None, audit=E4.audit_graph, ceiling=False):
    key = key or E4.load_key()
    trace_dir = Path(trace_dir)
    labels = [s["label"] for s in key["core_chain"] + key["downstream_of_hif2"]]
    rows, missing, details = [], [], {}
    for p in plan["patients"]:
        f = trace_dir / f"{p['patient_id']}.json"
        if not f.exists():
            missing.append(p["patient_id"])
            continue
        t = json.loads(f.read_text(encoding="utf-8"))
        t.setdefault("e4_cohort", {"group": p["group"]})
        row, matches = score_trace(t, key, audit=audit, ceiling=ceiling)
        row["group"] = p["group"]
        row["trace"] = str(f)
        rows.append(row)
        details[p["patient_id"]] = matches
    valid = [r for r in rows if r["valid"]]

    def rate(rs, col):
        return round(sum(bool(r.get(col)) for r in rs) / len(rs), 3) if rs else None

    def block(rs):
        return {"n": len(rs),
                "step_recovery": {lab: rate(rs, f"step:{lab}") for lab in labels},
                "step_recovery_kg": {lab: rate(rs, f"step_kg:{lab}") for lab in labels},
                "mean_steps_recovered": round(sum(r["n_steps_recovered"] for r in rs) / len(rs), 2) if rs else None,
                "mean_drugs_surfaced": round(sum(r["n_drugs_surfaced"] for r in rs) / len(rs), 2) if rs else None,
                "patients_with_any_extra_drug": sum(bool(r["extra_drugs"]) for r in rs)}

    man = trace_dir / "_manifest.jsonl"
    summary = {
        "eval": "e4_cohort" + ("_full_ceiling" if ceiling else ""), "trace_dir": str(trace_dir),
        "plan_sha": sha_file(PLAN), "plan_rule": plan.get("rule"), "seed": plan.get("seed"),
        "answer_key_sha256": sha_file(E4.KEY_PATH),
        "run_manifest": json.loads(man.read_text(encoding="utf-8").splitlines()[0]) if man.exists() else None,
        "n_planned": len(plan["patients"]), "n_traces": len(rows), "missing": missing,
        "n_valid": len(valid), "n_invalid": len(rows) - len(valid), "n_leaky": sum(bool(r["leak"]) for r in rows),
        "invalid": [{"patient_id": r["patient_id"], "leak": r["leak"], "reason": r["invalid_reason"]}
                    for r in rows if not r["valid"]],
        "overall": block(valid),
        "by_group": {g: block([r for r in valid if r["group"] == g]) for g in ("vhl_mutated", "no_vhl_mutation")},
        "scoring_rules": __doc__.split("Auto-scoring rules (written before any run):", 1)[1].strip(),
        "scored_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return summary, rows, details


def write_results(summary, rows, details, stem):
    out = P.results / stem
    write_json(out.with_suffix(".json"), {**summary, "patients": rows, "step_matches": details})
    cols = (["patient_id", "group", "valid", "leak", "invalid_reason", "n_steps_recovered", "n_steps_recovered_kg"]
            + [k for k in (rows[0] if rows else {}) if k.startswith("step:")]
            + ["n_drugs_surfaced", "drugs_surfaced", "extra_drugs", "n_supported_claims", "trace"])
    out.with_suffix(".csv").parent.mkdir(parents=True, exist_ok=True)
    with open(out.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({**r, **{k: "; ".join(r[k]) for k in ("drugs_surfaced", "extra_drugs") if isinstance(r.get(k), list)}})
    return out.with_suffix(".json"), out.with_suffix(".csv")


# ---------------------------------------------------------------- 6. hand-check

HAND_COLS = ["patient_id", "group", "step", "auto_verdict", "claim_quote", "cited_ids", "human_verdict", "human_note"]


def make_handcheck(plan, trace_dir, n=5, key=None, seed=SEED, path=None, audit=E4.audit_graph):
    """n valid patients x 5 steps. Recovered steps show the claim that recovered them; missed steps show the closest
    claim of any status that mentions the step (or none), so the reviewer can check misses as well as hits."""
    key = key or E4.load_key()
    summary, rows, details = score_cohort(plan, trace_dir, key, audit=audit)
    valid = sorted(r["patient_id"] for r in rows if r["valid"])
    pick = random.Random(seed + 1).sample(valid, min(n, len(valid)))
    out_rows = []
    for pid in pick:
        t = json.loads((Path(trace_dir) / f"{pid}.json").read_text(encoding="utf-8"))
        claims = {c["claim_id"]: c for c in t["claims"]}
        group = next(r["group"] for r in rows if r["patient_id"] == pid)
        for s, m in zip(key["core_chain"] + key["downstream_of_hif2"], details[pid]):
            terms = list(s["accept_terms"]) + [s["label"]]
            if m["claim_ids"]:
                c = claims[m["claim_ids"][0]]
                verdict = "recovered"
            else:
                near = [c for c in t["claims"] if mentions(c.get("quote") or c["text"], terms)]
                c = near[0] if near else None
                verdict = "not_recovered"
            out_rows.append({"patient_id": pid, "group": group, "step": s["label"], "auto_verdict": verdict,
                             "claim_quote": (c.get("quote") or c["text"]) if c else "(no claim mentions this step)",
                             "cited_ids": " ".join(c["support"]["edge_ids"] + c["support"]["pmids"]) if c else "",
                             "human_verdict": "", "human_note": ""})
    path = Path(path or P.results / "e4_cohort_handcheck.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, HAND_COLS)
        w.writeheader()
        w.writerows(out_rows)
    return path, out_rows


def agreement(path=None):
    """Human vs auto on the filled hand-check sheet. human_verdict: recovered / not_recovered (yes/no also accepted)."""
    path = Path(path or P.results / "e4_cohort_handcheck.csv")
    norm = {"recovered": 1, "yes": 1, "y": 1, "1": 1, "true": 1, "not_recovered": 0, "no": 0, "n": 0, "0": 0, "false": 0}
    pairs = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            h = str(r.get("human_verdict", "")).strip().lower()
            if h in norm:
                pairs.append((1 if r["auto_verdict"] == "recovered" else 0, norm[h]))
    n = len(pairs)
    if not n:
        return {"n_rated": 0, "note": "no human_verdict filled in yet"}
    agree = sum(a == h for a, h in pairs)
    tp = sum(a == h == 1 for a, h in pairs)
    fp = sum(a == 1 and h == 0 for a, h in pairs)
    fn = sum(a == 0 and h == 1 for a, h in pairs)
    tn = n - tp - fp - fn
    po = agree / n
    pa = (sum(a for a, _ in pairs) / n) * (sum(h for _, h in pairs) / n)
    pn = (1 - sum(a for a, _ in pairs) / n) * (1 - sum(h for _, h in pairs) / n)
    pe = pa + pn
    kappa = round((po - pe) / (1 - pe), 3) if pe < 1 else None
    return {"n_rated": n, "agreement": round(po, 3), "cohen_kappa": kappa,
            "auto_recovered_human_yes": tp, "auto_recovered_human_no": fp,
            "auto_missed_human_yes": fn, "auto_missed_human_no": tn}


# ---------------------------------------------------------------- CLI

def main(argv=None):
    global PLAN
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="pre-register the patients (writes eval/e4_cohort_plan.json)")
    g.add_argument("--run", action="store_true", help="run the planned patients, then score them")
    g.add_argument("--score", action="store_true", help="audit + score existing traces")
    g.add_argument("--sample", type=int, metavar="N", help="write a hand-check sheet for N patients")
    g.add_argument("--agreement", action="store_true", help="human vs auto agreement on the filled sheet")
    g.add_argument("--reverify", action="store_true", help="re-check saved drafts with the current verifier, then score")
    ap.add_argument("--variant", choices=["e4", "full"], default="e4", help="full = unmasked ceiling")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--limit", type=int, help="smoke run on the first N planned patients (-> e4_cohort_smoke/)")
    ap.add_argument("--out-dir", help="trace folder (default by variant)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--plan-file", help=f"plan to write or use (default {PLAN.relative_to(ROOT)})")
    ap.add_argument("--seed", type=int, default=SEED, help="--plan only: draw seed")
    ap.add_argument("--exclude-plan", action="append", default=[], help="--plan only: leave these plans' patients out")
    ap.add_argument("--note", help="--plan only: free text stored in the plan (e.g. the planned arms and comparison)")
    ap.add_argument("--hub", type=float, help="exploratory arm: AGENT_HUB for the agent's retrieval (needs --tag)")
    ap.add_argument("--tag", help="exploratory arm name: traces in <default folder>_<tag>, results e4_cohort_<tag>")
    a = ap.parse_args(argv)
    if a.plan_file:
        PLAN = Path(a.plan_file).resolve()
    if (a.hub is not None or a.plan_file) and not a.tag and not a.plan:
        sys.exit("--hub and --plan-file runs need a --tag, so they can't write into the pre-registered folders")

    if a.plan:
        plan = make_plan(force=a.force, path=PLAN, seed=a.seed, exclude_plans=a.exclude_plan, note=a.note)
        log(json.dumps({k: plan[k] for k in ("rule", "seed", "pool_sizes", "git_commit", "created_utc")}, indent=1))
        log("patients: " + ", ".join(f"{p['patient_id']} ({'VHL' if p['group'] == 'vhl_mutated' else 'no VHL'})"
                                     for p in plan["patients"]))
        log(f"wrote {PLAN}. Commit it before the full --run.")
        return 0
    if a.agreement:
        log(json.dumps(agreement(), indent=1))
        return 0

    plan = load_plan(PLAN)
    ceiling = a.variant == "full"
    tdir = Path(a.out_dir) if a.out_dir else TRACE_DIRS["smoke" if a.limit else ("full" if ceiling else "e4")]
    if a.tag and not a.out_dir:
        tdir = tdir.with_name(f"{tdir.name}_{a.tag}")
    if a.reverify:
        apply_settings(a.variant, a.hub)
        src, tdir = tdir, tdir.with_name(tdir.name + "_rv")
        reverify_cohort(src, tdir)
    if a.run:
        if a.limit is None and not plan_committed(PLAN):
            sys.exit(f"{PLAN} is not committed (or has changes). Commit the pre-registration before the full run; "
                     "use --limit N for a smoke run.")
        run_cohort(plan, a.variant, a.workers, a.limit, tdir, a.force, hub=a.hub, tag=a.tag)
    if a.sample:
        path, rows = make_handcheck(plan, tdir, a.sample)
        log(f"wrote {path} ({len(rows)} rows). Fill human_verdict with recovered / not_recovered, then --agreement.")
        return 0
    sub = {**plan, "patients": plan["patients"][: a.limit]} if a.limit else plan
    summary, rows, details = score_cohort(sub, tdir, ceiling=ceiling)
    stem = ("e4_cohort" + ("_full" if ceiling else "") + ("_smoke" if a.limit else "") + (f"_{a.tag}" if a.tag else "")
            + ("_rv" if a.reverify else ""))
    j, c = write_results(summary, rows, details, stem)
    o = summary["overall"]
    log(f"\n{summary['n_valid']}/{summary['n_traces']} valid traces ({summary['n_leaky']} leaky, "
        f"{summary['n_invalid']} invalid in total, {len(summary['missing'])} missing)")
    for lab, r in (o["step_recovery"] or {}).items():
        by = {gname: b["step_recovery"][lab] for gname, b in summary["by_group"].items()}
        log(f"  {lab:48s} {r}   VHL {by['vhl_mutated']}   no-VHL {by['no_vhl_mutation']}   (KG-cited {o['step_recovery_kg'][lab]})")
    log(f"  mean drugs surfaced {o['mean_drugs_surfaced']}   patients with an EXTRA_DRUG {o['patients_with_any_extra_drug']}")
    for inv in summary["invalid"]:
        log(f"  INVALID {inv['patient_id']}{' (leak)' if inv['leak'] else ''}: {inv['reason'][:140]}")
    log(f"wrote {j} and {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
