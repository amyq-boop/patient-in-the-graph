"""Claim verifier (CONTRACTS.md §8 support rule).

1. The LLM splits the draft into atomic claims (JSON). A deterministic splitter is the fallback.
2. Support is checked in code, not by the LLM: a claim is "supported" only if an edge ID it
   cites is in evidence.edge_ids (what retrieve_subgraph and get_patient returned in this trace)
   or a PMID it cites is in evidence.pmids (what get_mechanism returned). Only IDs written in
   the claim's own sentence count, so the splitter can't add any.
3. Unsupported claims become "hypothesis" if the draft framed them that way, else "removed".
4. The answer is the draft with removed claims cut out and unverified hypotheses labelled.

Extra check (beyond the contract): `entity_match` says whether a supporting edge actually
involves something the claim talks about (an endpoint name appears in the claim). The contract
rule alone would accept "VHL drives ccRCC [<any retrieved edge>]". With VERIFIER_STRICT=1 a
claim also needs entity_match (or a PMID) to count as supported.
"""
import json
import os
import re

from agent.prompts import CLAIMS_SYSTEM

ID_TOKEN = re.compile(r"\bE\d+\b|\bpt:[^\s,;\]\)]+|\bPMID:\s*\d+", re.I)
BRACKET = re.compile(r"\[([^\]]+)\]")
HYP = re.compile(r"(?i)\W*(hypothes|speculat)")


def norm_id(x):
    x = str(x).strip().rstrip(".")
    m = re.match(r"(?i)pmid:\s*(\d+)$", x)
    if m:
        return f"PMID:{m.group(1)}"
    m = re.match(r"(?i)e(\d+)$", x)
    if m:
        return f"E{m.group(1)}"
    m = re.match(r"(?i)pt:(.+)$", x)
    if m:
        return f"pt:{m.group(1)}"
    return x


def cited_ids(text):
    """Edge IDs and PMIDs cited in square brackets, e.g. [E12, E7] or [PMID:123] or [pt:C3L-1:protein_up:NCBIGene:7]."""
    ids = []
    for inner in BRACKET.findall(text or ""):
        ids += [norm_id(t) for t in ID_TOKEN.findall(inner)]
    return list(dict.fromkeys(ids))


def split_ids(ids):
    return [i for i in ids if not i.startswith("PMID:")], [i for i in ids if i.startswith("PMID:")]


def deterministic_claims(answer):
    """Fallback splitter: one claim per non-header sentence or bullet."""
    claims = []
    for line in (answer or "").splitlines():
        s = line.strip().lstrip("-*•").strip()
        if not s or (s.startswith("**") and s.endswith("**")) or len(s) < 12:
            continue
        for sent in re.split(r"(?<=[.!?])\s+(?=[A-Z])", s):
            sent = sent.strip()
            if len(sent) < 12:
                continue
            e, p = split_ids(cited_ids(sent))
            claims.append({"claim_id": f"c{len(claims) + 1}", "text": BRACKET.sub("", sent).strip(), "quote": sent,
                           "edge_ids": e, "pmids": p, "is_hypothesis": bool(HYP.match(sent))})
    return claims


def _parse_json(s):
    s = re.sub(r"^```(?:json)?|```$", "", (s or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", s, re.S)
    return json.loads(m.group(0) if m else s)


def extract_claims(draft, llm):
    try:
        out = llm.chat([{"role": "system", "content": CLAIMS_SYSTEM},
                        {"role": "user", "content": "ANSWER:\n" + draft}], json_mode=True, purpose="claims")
        claims = _parse_json(out["content"]).get("claims", [])
        if not isinstance(claims, list) or (draft.strip() and not claims):
            raise ValueError("no claims")
        return claims, "llm"
    except Exception:
        return deterministic_claims(draft), "deterministic"


def _mentions(claim, names):
    c = claim.lower()
    for n in names:
        n = str(n).lower()
        if n.startswith("patient:") or n.startswith("patient "):
            if "patient" in c:
                return True
        elif len(n) >= 3 and n in c:
            return True
    return False


def verify(draft, evidence, llm, edge_names=None, strict=None):
    """edge_names: {edge_id: [names/curies of both endpoints]} from this trace's tool results."""
    strict = os.environ.get("VERIFIER_STRICT", "0") == "1" if strict is None else strict
    edge_names = {norm_id(k): v for k, v in (edge_names or {}).items()}
    ev_edges = {norm_id(e) for e in evidence.get("edge_ids", [])}
    ev_pmids = {norm_id(p) for p in evidence.get("pmids", [])}
    in_draft = {norm_id(t) for t in ID_TOKEN.findall(draft or "")}
    raw, splitter = extract_claims(draft or "", llm)

    claims, answer = [], draft or ""
    for i, c in enumerate(raw):
        text = str(c.get("text", "")).strip()
        quote = str(c.get("quote") or text).strip()
        if not text:
            continue
        if quote and quote in answer or (quote and quote in (draft or "")):
            ids = cited_ids(quote)
        else:  # quote not verbatim: keep listed IDs only if they really occur in the draft
            listed = [norm_id(x) for x in list(c.get("edge_ids", [])) + list(c.get("pmids", []))]
            ids = [x for x in listed if x in in_draft]
        e_ids, p_ids = split_ids(ids)
        ok_e = [x for x in e_ids if x in ev_edges]
        ok_p = [x for x in p_ids if x in ev_pmids]
        hyp = bool(c.get("is_hypothesis")) or bool(HYP.match(text))
        ent = (bool(ok_p) or any(_mentions(text, edge_names.get(x, [])) for x in ok_e)) if edge_names else None
        backed = bool(ok_e or ok_p) and (ent is not False or not strict)
        status = "supported" if backed else ("hypothesis" if hyp else "removed")
        claims.append({
            "claim_id": c.get("claim_id") or f"c{i + 1}", "text": text,
            "support": {"edge_ids": e_ids, "pmids": p_ids},
            "status": status,
            "quote": quote,
            "unmatched_ids": [x for x in e_ids + p_ids if x not in ev_edges and x not in ev_pmids],
            "entity_match": ent,
        })
        if quote and quote in answer:
            if status == "removed":
                answer = answer.replace(quote, "", 1)
            elif status == "hypothesis" and not re.search(r"(?i)hypothes|speculat", quote):
                answer = answer.replace(quote, f"Hypothesis (unverified): {quote}", 1)

    answer = _tidy(answer)
    n_rm = sum(c["status"] == "removed" for c in claims)
    if n_rm:
        answer += f"\n\n_Verifier: removed {n_rm} claim(s) that had no supporting edge or PMID._"
    stats = {"n_claims": len(claims), "n_supported": sum(c["status"] == "supported" for c in claims),
             "n_hypothesis": sum(c["status"] == "hypothesis" for c in claims), "n_removed": n_rm,
             "n_supported_entity_match": sum(c["status"] == "supported" and c["entity_match"] is not False
                                             for c in claims),
             "strict": strict}
    return claims, answer, stats, splitter


def _tidy(text):
    lines = [l.rstrip() for l in text.splitlines() if not (l.strip() and re.fullmatch(r"\s*[-*•]?\s*", l))]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
