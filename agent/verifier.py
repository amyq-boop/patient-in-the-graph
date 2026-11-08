"""Claim verifier (CONTRACTS.md §8 support rule).

1. The LLM splits the draft into atomic claims (JSON). A deterministic splitter is the fallback.
2. Support is checked in code, not by the LLM: a claim is "supported" only if an edge ID it
   cites is in evidence.edge_ids (what retrieve_subgraph and get_patient returned in this trace)
   or a PMID it cites is in evidence.pmids (what get_mechanism returned). Only IDs written in
   the claim's own sentence count, so the splitter can't add any.
   Citations count in [brackets] or (parentheses); a group naming a PMID makes its bare numbers PMIDs too.
   A range like [E12–E15] stands for every ID in it (at most RANGE_MAX), and it supports a claim only if all of
   them are in evidence; otherwise none of it counts (edge rows are not ordered by mechanism chain, so a range
   over a chain's first and last edge usually spans edges the agent never saw).
3. Unsupported claims become "hypothesis" if the draft framed them that way, else "removed".
4. The answer is the draft with removed claims cut out and unverified hypotheses labelled.

Not every sentence is a claim: section headers are skipped, and caveats (anything under a Caveats
header), statements that something cannot be determined, and proposed experiments are kept in the
answer but not scored. They're returned in stats["non_claims"] so the hand-check can see them.
Their citations are still checked: an ID that isn't in evidence, a failed range, or (strict mode) a KG edge that
names nothing the sentence mentions is replaced by "citation removed" in the answer and listed in that
non-claim's "bad_ids". Model outputs (pred:/contrib:/rank:) are exempt from the naming check here.
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
RANGE = re.compile(r"\bE(\d+)\s*[\u2010-\u2015-]\s*E?(\d+)\b", re.I)  # E12-E15, E12–E15, E12 – 15
CITE_TOKEN = re.compile(f"{RANGE.pattern}|{ID_TOKEN.pattern}", re.I)
RANGE_MAX = 10  # a longer range is never a list of edges the agent actually read
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
PAREN = re.compile(r"\(([^()]+)\)")
BARE_PMID = re.compile(r"(?<![\w:])(\d{7,9})(?![\w:])")  # 38540392 in "[PMID:39334956, 38540392]"
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


def _citation_groups(text):
    """Bracketed and parenthesised groups that contain at least one ID. Models sometimes cite as (E123, E456)
    instead of [E123, E456]; a parenthesis with no ID in it, like "(stage III)", is ordinary text."""
    groups = BRACKET.findall(text or "")
    groups += [g for g in PAREN.findall(BRACKET.sub(" ", text or "")) if ID_TOKEN.search(g)]
    return groups


def _parse_citations(text):
    """(IDs cited one by one, ranges) in citation order. A range is {"range": "E12–E15", "ids": [E12..E15]};
    "ids" is empty when the range is longer than RANGE_MAX."""
    singles, ranges = [], []
    for inner in _citation_groups(text):
        for m in CITE_TOKEN.finditer(inner):
            if m.group(1) is not None:
                lo, hi = sorted((int(m.group(1)), int(m.group(2))))
                ranges.append({"range": m.group(0), "ids": [f"E{i}" for i in range(lo, hi + 1)]
                               if hi - lo + 1 <= RANGE_MAX else []})
            else:
                singles.append(norm_id(m.group(0)))
        if re.search(r"(?i)\bPMID:", inner):
            singles += [f"PMID:{n}" for n in BARE_PMID.findall(inner)]
    return list(dict.fromkeys(singles)), ranges


def cited_ids(text):
    """Edge IDs and PMIDs cited in [brackets] or (parentheses), e.g. [E12, E7], (E12), [PMID:123, 456] or
    [pt:C3L-1:protein_up:NCBIGene:7]. In a group that names a PMID, the bare numbers after it are PMIDs too.
    A range [E12–E14] is expanded to E12, E13, E14 (see _check_ranges for when it counts)."""
    singles, ranges = _parse_citations(text)
    return list(dict.fromkeys(singles + [x for r in ranges for x in r["ids"]]))


def _check_ranges(text, ev_edges):
    """Ranges cited in `text`, each marked ok iff every ID in it is in evidence, and the IDs to drop: members of
    failed ranges that the text doesn't also cite on their own."""
    singles, ranges = _parse_citations(text)
    out, drop = [], set()
    for r in ranges:
        ok = bool(r["ids"]) and all(x in ev_edges for x in r["ids"])
        out.append({**r, "ok": ok})
        if not ok:
            drop |= set(r["ids"]) - set(singles)
    return out, drop


def _drop_ids(text, bad):
    """`text` with the cited IDs (or range strings) in `bad` cut out of their citation groups, and the group marked
    "citation removed" so the reader sees the statement lost its support: [E1, E2] -> [E1, citation removed]."""
    def fix(inner):
        parts = re.split(r"\s*[,;]\s*", inner)
        keep = [p for p in parts if not (CITE_TOKEN.search(p) and
                                         all((m.group(0) if m.group(1) is not None else norm_id(m.group(0))) in bad
                                             for m in CITE_TOKEN.finditer(p)))]
        return ", ".join(keep + (["citation removed"] if len(keep) < len(parts) else [])).strip()

    def group(m, open_, close):
        if not CITE_TOKEN.search(m.group(1)):
            return m.group(0)
        return f"{open_}{fix(m.group(1))}{close}"

    t = BRACKET.sub(lambda m: group(m, "[", "]"), text)
    t = PAREN.sub(lambda m: group(m, "(", ")"), t)
    return t


def strip_citations(text):
    """The sentence without its citation groups (for the claim's display text)."""
    t = BRACKET.sub("", text or "")
    t = PAREN.sub(lambda m: "" if ID_TOKEN.search(m.group(1)) else m.group(0), t)
    return re.sub(r"\s+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", t)).strip()


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
            claims.append({"claim_id": f"c{len(claims) + 1}", "text": strip_citations(sent), "quote": sent,
                           "edge_ids": e, "pmids": p, "is_hypothesis": bool(HYP.match(sent))})
    return claims


def _parse_json(s):
    s = re.sub(r"^```(?:json)?|```$", "", (s or "").strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", s, re.S)
    return json.loads(m.group(0) if m else s)


def extract_claims(draft, llm):
    """VERIFIER_SPLITTER=rules: split by sentence/bullet in code (instant, deterministic, no LLM in the verifier at
    all). Default llm: atomic claims from the model (slower: ~95 s and ~15k tokens for a long answer)."""
    if os.environ.get("VERIFIER_SPLITTER", "llm").lower() == "rules":
        return deterministic_claims(draft), "deterministic"
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
    in_draft = {norm_id(t) for t in ID_TOKEN.findall(draft or "")} | set(cited_ids(draft or ""))
    raw, splitter = extract_claims(draft or "", llm)

    claims, non_claims, answer = [], [], draft or ""
    for i, c in enumerate(raw):
        text = str(c.get("text", "")).strip()
        quote = str(c.get("quote") or text).strip()
        if not text or is_header(quote) or is_header(text):
            continue
        ranges, drop = [], set()
        if quote and quote in answer or (quote and quote in (draft or "")):
            ids = cited_ids(quote)
            ranges, drop = _check_ranges(quote, ev_edges)
        else:  # quote not verbatim: keep listed IDs only if they really occur in the draft
            listed = [norm_id(x) for x in list(c.get("edge_ids", [])) + list(c.get("pmids", []))]
            ids = [x for x in listed if x in in_draft]
        e_ids, p_ids = split_ids([x for x in ids if x not in drop])
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
        bad_ranges = [r["range"] for r in ranges if not r["ok"]]
        if status == "removed":
            why = non_claim_reason(quote, section_of(draft, quote)) or non_claim_reason(text)
            if why:  # kept in the answer, not scored; listed so the hand-check can audit it
                nc = {"text": text, "reason": why, "quote": quote}
                # its citations still have to hold: an ID not in evidence, or (strict) naming nothing it mentions
                bad = [x for x in e_ids + p_ids if x not in ev_edges and x not in ev_pmids]
                bad += [x for x in mism if strict and not x.startswith(MODEL_PREFIXES)] + bad_ranges
                if bad:
                    nc["bad_ids"] = bad
                    if quote in answer:
                        answer = answer.replace(quote, _drop_ids(quote, set(bad)), 1)
                non_claims.append(nc)
                continue
        model_only = backed and not ok_p and all(x.startswith(MODEL_PREFIXES) for x in ok_e)
        claim = {
            "claim_id": c.get("claim_id") or f"c{i + 1}", "text": text,
            "support": {"edge_ids": e_ids, "pmids": p_ids},
            "status": status,
            "quote": quote,
            "unmatched_ids": [x for x in e_ids + p_ids if x not in ev_edges and x not in ev_pmids] + bad_ranges,
            "entity_match": ent,
            "mismatched_ids": mism,
            "model_output_only": model_only,
        }
        if ranges:  # only claims that cite a range carry this key
            claim["range_citations"] = ranges
        claims.append(claim)
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
             "n_non_claims_bad_citations": sum(bool(n.get("bad_ids")) for n in non_claims),
             "n_range_citations_rejected": sum(not r["ok"] for c in claims for r in c.get("range_citations", [])),
             "strict": strict}
    return claims, answer, stats, splitter


def _tidy(text):
    # drop list items emptied by a removed claim: "-", "*", or a bare number like "3." left behind
    lines = [l.rstrip() for l in text.splitlines() if not (l.strip() and re.fullmatch(r"\s*(?:[-*•]|\d+[.)])?\s*", l))]
    # drop a section header whose whole section was removed (e.g. an uncited Summary sentence): a header followed
    # only by blank lines and then another header or the end. Display only; claims and stats are unaffected.
    keep = []
    for i, l in enumerate(lines):
        if is_header(l):
            nxt = next((x for x in lines[i + 1:] if x.strip()), None)
            if nxt is None or is_header(nxt):
                continue
        keep.append(l)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()
