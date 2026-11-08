"""E4 case study: audited reconstruction of the masked ccRCC mechanism.

Run (from repository root):
    BIOHACK_VARIANT=e4 BIOHACK_MOCK=0 python -m eval.e4_case_study
    BIOHACK_VARIANT=e4 BIOHACK_MOCK=0 python -m eval.e4_case_study --patient C3L-00004
    python -m eval.e4_case_study --score eval/traces/<run>/E4.json

A new trace produces a review TEMPLATE, not a certified recovery score.
Review evidence for each candidate step (including causal relationships), then:
    python -m eval.e4_case_study --score eval/traces/<run>/E4.json \
        --review eval/traces/<run>/E4.review.json

Only a completed, independently reviewed trace can write eval/results/e4.json.
The contract's patient_id/qid/expected_steps/recovered_steps/drugs_surfaced
fields are preserved; extra audit metadata is additive.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from common.contracts import CCRCC_ID, GRAPH_VARIANTS, P, ROOT

KEY_PATH = ROOT / "eval" / "e4_answer_key.json"
QUESTION = "What drives this patient's predicted risk, and what would you target?"
QID = "E4"
E4_VARIANTS = ("e4", "e4_dismech")
HTN = "HP:0000822"
EXTRA_DRUGS = ("lenvatinib", "sorafenib", "tivozanib", "everolimus", "temsirolimus")
EID = re.compile(r"^E(\d+)$")
CITATION = re.compile(r"\[([^]]+)\]")
CITED = re.compile(r"\bE\d+\b|\bPMID:\s*\d+\b|\b(?:pt|pred|contrib|rank):[^\s,;\])]+", re.I)


class E4Error(ValueError):
    """Expected input/provenance failure, not a valid zero-recovery result."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise E4Error(message)


def read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise E4Error(f"Could not read JSON {path}: {exc}") from exc
    require(isinstance(obj, dict), f"{path}: JSON root must be an object")
    return obj


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", encoding="utf-8", delete=False) as fp:
            tmp = Path(fp.name)
            json.dump(data, fp, indent=2, ensure_ascii=False)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        tmp.replace(path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def archive_previous(path: Path) -> None:
    """Prevent an invalid new run from leaving a stale live result on disk."""
    if path.exists():
        archive_dir = path.parent / "e4_history"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived = archive_dir / f"{path.stem}_{uuid.uuid4().hex[:12]}{path.suffix}"
        shutil.move(str(path), str(archived))
        print(f"Archived prior E4 result: {archived}")


def normalized_id(value: Any) -> str:
    s = str(value).strip().rstrip(".")
    m = re.fullmatch(r"(?i)pmid:\s*(\d+)", s)
    if m:
        return f"PMID:{m.group(1)}"
    m = re.fullmatch(r"(?i)e(\d+)", s)
    if m:
        return f"E{m.group(1)}"
    return s


def ids_list(value: Any, context: str) -> list[str]:
    require(isinstance(value, list) and all(isinstance(x, str) for x in value),
            f"{context} must be a list of strings")
    return [normalized_id(x) for x in value]


def cited_in_quote(quote: str) -> set[str]:
    return {normalized_id(m.group(0)) for b in CITATION.findall(quote)
            for m in CITED.finditer(b)}


def load_key(path: Path = KEY_PATH) -> dict:
    key = read_json(path)
    require(key.get("disease") == CCRCC_ID, "Answer key is not the ccRCC key")
    require(isinstance(key.get("core_chain"), list) and len(key["core_chain"]) == 2,
            "E4 answer key must have two core steps")
    require(isinstance(key.get("downstream_of_hif2"), list) and len(key["downstream_of_hif2"]) == 3,
            "E4 answer key must have three downstream steps")
    steps = key["core_chain"] + key["downstream_of_hif2"]
    require(all(isinstance(s, dict) and isinstance(s.get("label"), str)
                and isinstance(s.get("mechanism"), str)
                and isinstance(s.get("accept_terms"), list) for s in steps),
            "Each E4 step needs label, mechanism and accept_terms")
    labels = [s["label"] for s in steps]
    require(len(set(labels)) == 5, "E4 step labels must be unique")
    require(isinstance(key.get("drugs"), list), "Answer key is missing drugs")
    require(all(isinstance(d, dict) and isinstance(d.get("name"), str) for d in key["drugs"]),
            "Every drug needs a name")
    return key


def validate_trace(trace: dict) -> None:
    require(trace.get("variant") in E4_VARIANTS, f"Invalid E4 variant: {trace.get('variant')!r}")
    require(trace.get("mode") == "agent", "E4 must use agent mode")
    require(trace.get("qid") == QID, f"E4 qid must be {QID}")
    require(isinstance(trace.get("patient_id"), str) and bool(trace["patient_id"].strip()),
            "Missing patient_id")
    require(isinstance(trace.get("run_id"), str) and bool(trace["run_id"].strip()),
            "Missing run_id")
    require(isinstance(trace.get("draft"), str) and bool(trace["draft"].strip()),
            "Missing/empty draft")
    require(isinstance(trace.get("answer"), str), "Missing final answer")
    require(not any("MOCK DATA" in str(note) for note in trace.get("notes", [])),
            "Trace notes indicate mock data")
    calls = trace.get("tool_calls")
    claims = trace.get("claims")
    evidence = trace.get("evidence")
    require(isinstance(calls, list) and bool(calls), "tool_calls missing/empty")
    require(isinstance(claims, list), "claims must be a list (zero claims is valid)")
    require(isinstance(evidence, dict), "evidence must be an object")
    ids_list(evidence.get("edge_ids"), "evidence.edge_ids")
    ids_list(evidence.get("pmids"), "evidence.pmids")
    for idx, tc in enumerate(calls):
        require(isinstance(tc, dict) and isinstance(tc.get("name"), str),
                f"tool_calls[{idx}] invalid")
        require(isinstance(tc.get("args"), dict) and isinstance(tc.get("result"), dict),
                f"tool_calls[{idx}] args/result must be objects")
        result = tc["result"]
        meta = result.get("meta")
        require(meta is None or isinstance(meta, dict),
                f"tool_calls[{idx}] has malformed meta")
        require("_fallback" not in result and not (meta or {}).get("mock", False),
                f"Mock/fallback tool result in tool_calls[{idx}]")
    for idx, claim in enumerate(claims):
        require(isinstance(claim, dict) and isinstance(claim.get("claim_id"), str),
                f"claims[{idx}] needs claim_id")
        require(isinstance(claim.get("text"), str) and isinstance(claim.get("quote"), str),
                f"claims[{idx}] needs text and quote")
        require(claim.get("status") in ("supported", "hypothesis", "removed"),
                f"claims[{idx}] has invalid status")
        sup = claim.get("support")
        require(isinstance(sup, dict), f"claims[{idx}] needs support")
        ids_list(sup.get("edge_ids"), f"claims[{idx}].support.edge_ids")
        ids_list(sup.get("pmids"), f"claims[{idx}].support.pmids")
    require(len({c["claim_id"] for c in claims}) == len(claims), "Duplicate claim_id in trace")


def observed_evidence(trace: dict) -> tuple[set[str], set[str]]:
    """Reconstruct provenance from recorded tool results; never trust trace.evidence alone."""
    edges, pmids = set(), set()
    for tc in trace["tool_calls"]:
        name, r = tc["name"], tc["result"]
        if "error" in r:
            continue
        if name == "retrieve_subgraph":
            edges.update(ids_list(r.get("edge_ids", []), "retrieve_subgraph.edge_ids"))
        elif name == "get_patient":  # same lists the agent cites from (agent/graph_agent.py PATIENT_LISTS)
            for k in ("phenotypes", "proteins_up", "proteins_down", "mutations", "medications"):
                for obj in r.get(k, []):
                    if obj.get("edge_id"):
                        edges.add(normalized_id(obj["edge_id"]))
        elif name == "get_mechanism":
            for chain in r.get("chains", []):
                for step in chain.get("steps", []):
                    pmids.update(ids_list(step.get("pmids", []), "get_mechanism.pmids"))
                    if step.get("edge_id"):
                        edges.add(normalized_id(step["edge_id"]))
            for d in r.get("drug_evidence", []):
                pmids.update(ids_list(d.get("pmids", []), "get_mechanism.drug_evidence.pmids"))
                if d.get("edge_id"):
                    edges.add(normalized_id(d["edge_id"]))
        elif name == "get_drug_targets":
            for d in r.get("items", []) + r.get("other_drugs_on_mutated_genes", []):
                for t in d.get("targets", []):
                    for k in ("drug_target_edge_id", "patient_edge_id"):
                        if t.get(k):
                            edges.add(normalized_id(t[k]))
                for ind in d.get("indication", []):
                    if ind.get("edge_id"):
                        edges.add(normalized_id(ind["edge_id"]))
        elif name == "predict_patient":
            if r.get("evidence_id"):
                edges.add(normalized_id(r["evidence_id"]))
            for item in r.get("top_contributing", []):
                if item.get("evidence_id"):
                    edges.add(normalized_id(item["evidence_id"]))
        elif name == "rank_for_patient":
            for item in r.get("items", []):
                if item.get("evidence_id"):
                    edges.add(normalized_id(item["evidence_id"]))
    return edges, pmids


def audit_graph(trace: dict, key: dict, edges_path: Path = P.edges) -> dict:
    """Check local mask flags and every returned graph edge against the frozen graph."""
    import pandas as pd

    variant = trace["variant"]
    flags = GRAPH_VARIANTS[variant]
    require("e4_full" in flags or "e4_dismech" in flags,
            f"Variant {variant} does not mask the E4 disease")
    require(edges_path.exists(), f"Graph audit requires {edges_path}; cannot certify trace without it")
    try:
        df = pd.read_parquet(edges_path, columns=["edge_row", *flags])
    except Exception as exc:
        raise E4Error(f"Cannot inspect E4 graph mask: {exc}") from exc
    require(df["edge_row"].is_unique, "Graph has duplicate edge_row IDs")
    disease_flag = "e4_full" if variant == "e4" else "e4_dismech"
    require(df[disease_flag].fillna(False).astype(bool).any(),
            f"Graph has no {disease_flag} ccRCC ablation edges")
    mask = df[list(flags)].fillna(False).astype(bool).any(axis=1)
    mask_rows = set(df.loc[mask, "edge_row"].astype(int))
    retrieved = set()
    successful_retrievals = 0
    for tc in trace["tool_calls"]:
        if tc["name"] != "retrieve_subgraph":
            continue
        r = tc["result"]
        if "error" in r:
            continue
        successful_retrievals += 1
        require((r.get("meta") or {}).get("variant") == variant,
                "Retrieval result does not attest the active E4 graph variant")
        for identifier in r.get("edge_ids", []):
            eid = normalized_id(identifier)
            require(EID.fullmatch(eid) is not None,
                    f"Invalid KG edge identifier in retrieval: {identifier!r}")
            retrieved.add(int(EID.fullmatch(eid).group(1)))
        listed = {normalized_id(e.get("edge_id")) for e in r.get("edges", [])}
        require(listed.issubset({f"E{n}" for n in retrieved}),
                "Retrieved edges are not included in edge_ids")
        retrieved.update(int(e) for e in re.findall(r"\[E(\d+)\]", r.get("triples_text", "")))
    require(successful_retrievals > 0, "No successful retrieve_subgraph call; E4 retrieval was not tested")
    found = set(df.loc[df["edge_row"].isin(retrieved), "edge_row"].astype(int))
    require(retrieved == found, "Retrieved evidence contains edge IDs not found in graph")
    leaked = sorted(retrieved & mask_rows)
    if leaked:
        raise E4Error(f"E4 returned {len(leaked)} masked graph edges, e.g. E{leaked[0]}")
    digest = hashlib.sha256(",".join(map(str, sorted(mask_rows))).encode()).hexdigest()
    return {"variant": variant, "graph_path": str(edges_path),
            "masked_edge_count": len(mask_rows), "masked_edge_sha256": digest,
            "retrieved_graph_edges": len(retrieved), "retrieval_calls": successful_retrievals}


def leak_check(trace: dict, key: dict) -> list[str]:
    problems = []
    forbidden_mechanisms = {s["mechanism"] for s in key["core_chain"] + key["downstream_of_hif2"]}
    for i, tc in enumerate(trace["tool_calls"]):
        args, result = tc["args"], tc["result"]
        if "error" in result:
            continue
        if tc["name"] == "get_mechanism" and result.get("chains"):
            disease = result.get("disease") or args.get("disease_curie")
            if disease == CCRCC_ID:
                problems.append(f"tool_calls[{i}]: get_mechanism exposed the ccRCC chain")
        payload = json.dumps(result, ensure_ascii=False)
        if any(token in payload for token in forbidden_mechanisms):
            problems.append(f"tool_calls[{i}]: a forbidden ccRCC DisMech mechanism ID was returned")
    return problems


def normalize_text(text: str) -> str:
    text = text.casefold().replace("α", "alpha").replace("β", "beta")
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text)


def mentions(text: str, terms: list[str]) -> bool:
    text = normalize_text(text)
    for term in terms:
        term = normalize_text(term)
        if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text):
            return True
    return False


def supported_claims(trace: dict) -> dict[str, dict]:
    ev_edge, ev_pmid = observed_evidence(trace)
    recorded_edge = set(ids_list(trace["evidence"]["edge_ids"], "trace.evidence.edge_ids"))
    recorded_pmid = set(ids_list(trace["evidence"]["pmids"], "trace.evidence.pmids"))
    require(recorded_edge == ev_edge and recorded_pmid == ev_pmid,
            "Trace evidence does not match evidence reconstructed from tool results")
    out = {}
    for claim in trace["claims"]:
        if claim["status"] != "supported":
            continue
        cited = cited_in_quote(claim["quote"])
        ids = set(ids_list(claim["support"]["edge_ids"], "claim.edge_ids"))
        ids.update(ids_list(claim["support"]["pmids"], "claim.pmids"))
        require(ids.issubset(cited), f"Claim {claim['claim_id']} cites IDs absent from its own quote")
        usable = ids & (ev_edge | ev_pmid)
        if not usable:
            continue
        # Model risk/feature IDs and patient measurements do not establish mechanisms.
        knowledge = {i for i in usable if EID.fullmatch(i) or i.startswith("PMID:")}
        out[claim["claim_id"]] = {"text": claim["text"],
                                   "ids": sorted(usable), "mechanism_ids": sorted(knowledge)}
    return out


def make_review_template(trace: dict, key: dict, claims: dict[str, dict]) -> dict:
    """Pre-fill candidates only. A human independently checks entailment/causality."""
    steps = []
    for s in key["core_chain"] + key["downstream_of_hif2"]:
        terms = list(s["accept_terms"]) + [s["label"]]
        candidates = [{"claim_id": cid, "text": c["text"], "evidence_ids": c["mechanism_ids"]}
                      for cid, c in claims.items()
                      if c["mechanism_ids"] and mentions(c["text"], terms)]
        steps.append({"label": s["label"], "claim_id": None, "evidence_ids": [],
                      "verified": False, "causal_link_verified": False,
                      "candidates": candidates})
    drug_names = list(dict.fromkeys([d["name"] for d in key["drugs"]] + list(EXTRA_DRUGS)))
    drugs = []
    for name in drug_names:
        candidates = [{"claim_id": cid, "text": c["text"], "evidence_ids": c["mechanism_ids"]}
                      for cid, c in claims.items()
                      if c["mechanism_ids"] and mentions(c["text"], [name])]
        if candidates:
            drugs.append({"name": name, "claim_id": None, "evidence_ids": [],
                          "target_verified": False, "candidates": candidates})
    return {"run_id": trace["run_id"], "reviewer": "", "steps": steps,
            "drugs": drugs,
            "instructions": (
                "For each step, choose a SUPPORTED claim and its cited graph edge(s)/PMID(s). "
                "Set verified=true only if those citations substantively entail the step. "
                "For steps 2-5 also set causal_link_verified=true only if the answer correctly "
                "links the step to its parent (VHL->HIF2 or HIF2->downstream) with admissible evidence. "
                "For drugs, target_verified means the cited evidence establishes the correct drug-target link. "
                "Do not rely on the answer key's own hidden PMIDs/edges as evidence."
            )}


def score(trace: dict, key: dict, review: dict, claims: dict[str, dict]) -> dict:
    require(review.get("run_id") == trace["run_id"], "Review run_id does not match trace")
    require(isinstance(review.get("reviewer"), str) and bool(review["reviewer"].strip()),
            "Review requires a named independent reviewer")
    reviewed_steps = review.get("steps")
    require(isinstance(reviewed_steps, list), "Review is missing steps")
    labels = [s["label"] for s in key["core_chain"] + key["downstream_of_hif2"]]
    require({r.get("label") for r in reviewed_steps} == set(labels)
            and len(reviewed_steps) == 5, "Review must contain exactly the five answer-key labels")
    recovered, step_evidence = [], {}
    for index, label in enumerate(labels):
        item = next(r for r in reviewed_steps if r["label"] == label)
        if item.get("verified") is not True:
            continue
        if index > 0:
            require(item.get("causal_link_verified") is True,
                    f"{label}: a recovered downstream step requires an audited causal link")
        cid = item.get("claim_id")
        require(cid in claims, f"{label}: reviewed claim_id must refer to a supported claim")
        c = claims[cid]
        # The independent reviewer, not keyword matching, decides entailment.
        selected = set(ids_list(item.get("evidence_ids"), f"{label}.evidence_ids"))
        require(bool(selected) and selected.issubset(set(c["mechanism_ids"])),
                f"{label}: selected evidence must be real claim-level KG edges/PMIDs")
        recovered.append(label)
        step_evidence[label] = sorted(selected)
    known = {d["name"].casefold(): d["name"] for d in key["drugs"]}
    known.update({name.casefold(): name for name in EXTRA_DRUGS})
    surfaced = []
    seen_drugs = set()
    for item in review.get("drugs", []):
        if item.get("target_verified") is not True:
            continue
        name = str(item.get("name", "")).casefold()
        require(name in known, f"Unknown drug in review: {name!r}")
        cid = item.get("claim_id")
        require(cid in claims and mentions(claims[cid]["text"], [name]),
                f"{name}: drug must be named by a supported claim")
        selected = set(ids_list(item.get("evidence_ids"), f"{name}.evidence_ids"))
        require(bool(selected) and selected.issubset(set(claims[cid]["mechanism_ids"])),
                f"{name}: target evidence must come from the drug's supported claim")
        if name not in seen_drugs:
            surfaced.append(known[name])
            seen_drugs.add(name)
    return {"patient_id": trace["patient_id"], "qid": QID,
            "expected_steps": labels, "recovered_steps": recovered,
            "step_evidence": step_evidence, "drugs_surfaced": surfaced,
            "variant": trace["variant"], "n_supported_claims": len(claims),
            "reviewer": review["reviewer"], "scoring_method": "independently_reviewed_claim_and_causal_link"}


def pick_patient(dispatch) -> tuple[str, str]:
    """Highest out-of-fold GNN score; optionally prefer hypertension in top 15."""
    import pandas as pd

    path = P.predictions
    if not path.exists():
        alt = ROOT / "models" / "results" / "predictions_y_stage.csv"
        require(alt.exists(), f"Neither {path} nor {alt} exists")
        path = alt
    pred = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    require({"patient_id", "model", "risk", "fold"}.issubset(pred.columns),
            f"{path} needs patient_id, model, risk, fold columns")
    gnn = pred.loc[pred["model"] == "gnn", ["patient_id", "risk", "fold"]].copy()
    require(not gnn.empty, f"{path} has no gnn predictions")
    gnn["risk"] = pd.to_numeric(gnn["risk"], errors="coerce")
    require(gnn["risk"].notna().all() and gnn["risk"].map(math.isfinite).all()
            and gnn["risk"].between(0, 1).all(), "Invalid GNN risk scores")
    require(gnn["patient_id"].notna().all() and gnn["patient_id"].is_unique,
            "GNN prediction patients must be unique (OOF)")
    require(gnn["fold"].notna().all(), "Missing GNN folds")
    gnn = gnn.sort_values(["risk", "patient_id"], ascending=[False, True])
    candidates = list(gnn["patient_id"].head(15).astype(str))
    if P.patient_phenotypes.exists():
        phen = pd.read_parquet(P.patient_phenotypes, columns=["patient_id", "curie"])
        hypertension = set(phen.loc[phen["curie"] == HTN, "patient_id"].astype(str))
        for pid in candidates:
            if pid in hypertension:
                return pid, "top-15 risk with recorded hypertension (selected demo case)"
    else:
        for pid in candidates:
            info = dispatch("get_patient", {"patient_id": pid})
            require(isinstance(info, dict), f"get_patient returned invalid data for {pid}")
            require(not info.get("_fallback"), "Patient selection used mock fallback")
            if "error" not in info and any(p.get("curie") == HTN for p in info.get("phenotypes", [])):
                return pid, "top-15 risk with recorded hypertension (selected demo case)"
    return candidates[0], "highest out-of-fold GNN risk (selected demo case)"


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--patient", help="Patient ID; default selects a documented demonstration case")
    ap.add_argument("--score", type=Path, help="Score an existing E4 trace")
    ap.add_argument("--review", type=Path, help="Completed independent step/causal-link review JSON")
    ap.add_argument("--out", type=Path, default=P.results / "e4.json")
    args = ap.parse_args(argv)
    require(not args.review or args.score, "Use --review together with --score")
    out = args.out.resolve()
    archive_previous(out)  # Important: an invalid run must not leave an old live result.
    key = load_key()

    if args.score:
        tpath = args.score.resolve()
        trace = read_json(tpath)
    else:
        os.environ.setdefault("BIOHACK_VARIANT", "e4")
        os.environ.setdefault("BIOHACK_MOCK", "0")
        require(os.environ["BIOHACK_VARIANT"] in E4_VARIANTS, "Set BIOHACK_VARIANT=e4")
        require(os.environ["BIOHACK_MOCK"].lower() == "0", "Set BIOHACK_MOCK=0 for a measured E4 run")
        require(P.edges.exists(), f"Required masked graph edges file not found: {P.edges}")
        from agent.graph_agent import run
        from agent.tools import dispatch
        patient_id, why = ((args.patient, "specified patient") if args.patient
                           else pick_patient(dispatch))
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_e4_" + uuid.uuid4().hex[:8]
        print(f"Running E4 for {patient_id} ({why})")
        trace = run(QUESTION, patient_id=patient_id, qid=QID, run_id=run_id)
        trace["patient_choice"] = why
        tpath = P.traces / run_id / f"{QID}.json"
        atomic_json(tpath, trace)
        print(f"Saved trace: {tpath}")

    validate_trace(trace)
    problems = leak_check(trace, key)
    require(not problems, "; ".join(problems))
    audit = audit_graph(trace, key)
    claims = supported_claims(trace)
    review_path = tpath.with_suffix(".review.json")
    if not args.review:
        if not review_path.exists():
            atomic_json(review_path, make_review_template(trace, key, claims))
        print(f"E4 not yet scored. Complete independent evidence review: {review_path}")
        print(f"Then: python -m eval.e4_case_study --score {tpath} --review {review_path}")
        return

    review = read_json(args.review)
    result = score(trace, key, review, claims)
    result.update({"leak_check": "passed", "graph_audit": audit,
                   "gnn_training_leak_check": "not_verified_by_trace_or_mask_audit",
                   "ablation_scope": "retrieval graph edges and returned evidence; not GNN training weights",
                   "trace": str(tpath), "run_id": trace["run_id"],
                   "patient_choice": trace.get("patient_choice", "scored existing trace"),
                   "review_sha256": hashlib.sha256(args.review.read_bytes()).hexdigest(),
                   "answer_key_sha256": hashlib.sha256(KEY_PATH.read_bytes()).hexdigest(),
                   "scored_at_utc": datetime.now(timezone.utc).isoformat()})
    atomic_json(out, result)
    print(f"Recovered {len(result['recovered_steps'])}/{len(result['expected_steps'])} independently verified steps")
    print(f"Reviewed drug targets: {result['drugs_surfaced'] or 'none'}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    try:
        main()
    except (E4Error, OSError) as exc:
        print(f"E4 INVALID: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
