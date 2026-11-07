"""Claim verifier (CONTRACTS.md §8 support rule).

1. The LLM splits the draft into atomic claims (JSON). A deterministic splitter is the fallback.
2. Support is checked in code, not by the LLM: a claim is "supported" only if an edge ID it
   cites is in evidence.edge_ids (what retrieve_subgraph and get_patient returned in this trace)
   or a PMID it cites is in evidence.pmids (what get_mechanism returned). Only IDs written in
   the claim's own sentence count, so the splitter can't add any.
3. Unsupported claims become "hypothesis" if the draft framed them that way, else "removed".
4. The answer is the draft with removed claims cut out and unverified hypotheses labelled.

Not every sentence is a claim: section headers are skipped, and caveats (anything under a Caveats
header), statements that something cannot be determined, and proposed experiments are kept in the
answer but not scored. They're returned in stats["non_claims"] so the hand-check can see them.
Model outputs (pred:/contrib:/rank: IDs from predict_patient and rank_for_patient) count as
evidence; claims backed only by them are counted in stats["n_supported_model"].

Extra check (beyond the contract): `entity_match` says whether a supporting edge actually
involves something the claim talks about (an endpoint name appears in the claim). The contract
rule alone would accept "VHL drives ccRCC [<any retrieved edge>]". With VERIFIER_STRICT=1 a
claim also needs entity_match (or a PMID) to count as supported.
"""
import json
import os
import re

from agent.prompts import CLAIMS_SYSTEM

ID_TOKEN = re.compile(r"\bE\d+\b|\b(?:pt|pred|contrib|rank):[^\s,;\]\)]+|\bPMID:\s*\d+", re.I)
MODEL_PREFIXES = ("pred:", "contrib:", "rank:")
HEADER = re.compile(r"^\s*(?:#{1,6}\s+\S.*|\*\*[^*\n]{1,60}\*\*:?(?:\s*\([^)\n]{0,120}\))?|[A-Z][A-Za-z /&-]{1,40}:)\s*$")
ABSTAIN = re.compile(
    r"(?i)\b(?:can ?not|can't|could not|is not possible to)\s+(?:be\s+)?"
    r"(?:determined?|established?|inferred?|infer|assess(?:ed)?|distinguish(?:ed)?|tell|conclude[d]?)\b"
    r"|\binsufficient (?:evidence|data|information)\b"
    r"|\b(?:no|not enough) (?:supporting |direct )?(?:evidence|edges?|paths?|data)\b"
    r"|\b(?:does|do) not (?:contain|establish|include|show|prove|demonstrate|imply|connect|link)\b"
    r"|\brequires? (?:constitutional|germline|additional|further|dedicated)\b")
EXPERIMENT = re.compile(r"(?i)^\W*(?:experiment|to test this|validation experiment)\b")
META = re.compile(r"(?i)\b(?:model estimate|not a (?:clinical|diagnos)\w*|hypothesis-generation tool)\b")
BRACKET = re.compile(r"\[([^\]]+)\]")
HYP = re.compile(r"(?i)\W*(?:\w+\s+)?(hypothes|speculat)")  # also "**Additional hypothesis:**"


def norm_id(x):
    x = str(x).strip().rstrip(".")
    m = re.match(r"(?i)pmid:\s*(\d+)$", x)
    if m:
        return f"PMID:{m.group(1)}"
    m = re.match(r"(?i)e(\d+)$", x)
    if m:
        return f"E{m.group(1)}"
    m = re.match(r"(?i)(pt|pred|contrib|rank):(.+)$", x)
    if m:
        return f"{m.group(1).lower()}:{m.group(2)}"
    return x


def is_header(line):
    return bool(HEADER.match(line or ""))


def section_of(draft, quote):
    """Name of the section header above `quote` in the draft ('' if none or not found)."""
    i = (draft or "").find(quote or "\0")
    if i < 0:
        return ""
    name = ""
    for line in draft[:i].splitlines():
        if is_header(line):
            name = re.sub(r"[#*:]", "", line).strip()
    return name


def non_claim_reason(text, section=""):
    """Why a sentence is kept but not scored, or None if it's an ordinary claim."""
    if section.lower().startswith("caveat"):
        return "caveat"
    if EXPERIMENT.match(text or ""):
        return "experiment"
    if ABSTAIN.search(text or ""):
        return "abstention"
    if META.search(text or ""):
        return "meta"
    return None


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
        if is_header(line):  # check before stripping bullets: lstrip("-*") would eat the ** of "**Summary**"
            continue
        s = line.strip().lstrip("-*•").strip()
        if not s or len(s) < 12:
            continue
        for sent in re.split(r"(?<=[.!?])\s+(?=\**[A-Z])", s):  # also split before "**Hypothesis:** ..."
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


SOFT = "~soft"  # first element of an edge_names entry: check entity match, but never count it as mismatched
GENERIC = {"and", "the", "for", "via", "not", "are", "its", "has", "with", "from", "into", "that", "this", "their", "driven", "pathway", "process", "regulation", "positive",
           "negative", "cell", "cells", "cellular", "protein", "disease", "activity", "response", "signaling", "via",
           "involved", "associated", "type", "factor", "receptor", "complex", "system", "increased", "decreased"}


def _toks(s):
    """Distinctive words, crudely stemmed to 6 characters (inhibitor ~ inhibition); keeps 3-letter symbols like VHL."""
    return {t[:6] for t in re.findall(r"[a-z0-9\u0370-\u03ff]+", str(s).lower()) if len(t) >= 3 and t not in GENERIC}


def _mentions(claim, names):
    """True if the claim names one of the endpoints: the whole name, or at least half of its distinctive words
    (so long DisMech step labels like 'Immune Evasion via PD-L1 and ...' still match a paraphrase)."""
    c = str(claim).lower()
    ct = _toks(c)
    for n in names:
        n = str(n).lower()
        if n.startswith("patient:") or n.startswith("patient "):
            if "patient" in c:
                return True
        elif len(n) >= 3 and n in c:
            return True
        else:
            nt = _toks(n)
            if len(nt) >= 2 and len(nt & ct) * 2 >= len(nt):
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

    claims, non_claims, answer = [], [], draft or ""
    for i, c in enumerate(raw):
        text = str(c.get("text", "")).strip()
        quote = str(c.get("quote") or text).strip()
        if not text or is_header(quote) or is_header(text):
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
        said = f"{text} {quote}"  # the quote carries every name the cited IDs belong to
        ent = (bool(ok_p) or any(_mentions(said, edge_names.get(x, [])) for x in ok_e)) if edge_names else None
        # every cited edge must name something the sentence talks about: catches "Lenvatinib [<axitinib edge>]"
        mism = [x for x in ok_e if x in edge_names and edge_names[x][:1] != [SOFT]  # long DisMech labels: too loose
                and not _mentions(said, edge_names[x])]
        backed = bool(ok_e or ok_p) and (not strict or (ent is not False and not mism))
        status = "supported" if backed else ("hypothesis" if hyp else "removed")
        if status == "hypothesis" and (EXPERIMENT.match(quote) or EXPERIMENT.match(text)
                                       or re.match(r"(?i)\W*(?:treat|measure|compare|knock|perform|test)\w*\b", text)
                                       and "experiment" in section_of(draft, quote).lower()
                                       or EXPERIMENT.search(quote.split(":", 1)[0] + ":")):
            status = "removed"  # a proposed experiment is not a hypothesis claim: keep it unlabelled, as a non-claim
        if status == "removed":
            why = non_claim_reason(quote, section_of(draft, quote)) or non_claim_reason(text)
            if why:  # kept in the answer, not scored; listed so the hand-check can audit it
                non_claims.append({"text": text, "reason": why, "quote": quote})
                continue
        model_only = backed and not ok_p and all(x.startswith(MODEL_PREFIXES) for x in ok_e)
        claims.append({
            "claim_id": c.get("claim_id") or f"c{i + 1}", "text": text,
            "support": {"edge_ids": e_ids, "pmids": p_ids},
            "status": status,
            "quote": quote,
            "unmatched_ids": [x for x in e_ids + p_ids if x not in ev_edges and x not in ev_pmids],
            "entity_match": ent,
            "mismatched_ids": mism,
            "model_output_only": model_only,
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
             "n_supported_model": sum(bool(c.get("model_output_only")) for c in claims),
             "n_claims_mismatched": sum(bool(c.get("mismatched_ids")) for c in claims),
             "n_non_claims": len(non_claims), "non_claims": non_claims,
             "strict": strict}
    return claims, answer, stats, splitter


def _tidy(text):
    lines = [l.rstrip() for l in text.splitlines() if not (l.strip() and re.fullmatch(r"\s*[-*•]?\s*", l))]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
