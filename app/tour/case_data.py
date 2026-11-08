"""Alexander Young's public case for the tour: live site first, then a hash-verified local snapshot.

Reuses `app.external_case.load_case` unchanged for the seven core tables, and applies the same manifest rule
(byte count + SHA-256 from the release manifest) to three extra tables the tour needs. Nothing here computes
risk, response or prognosis; values are passed through with their specimen, assay and source.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import threading
from pathlib import Path
from typing import Callable

from app.external_case import BASE_URL, MAX_BYTES, PublicCaseError, _fetch, joined_findings, load_case
from common.contracts import P

SNAPSHOT = Path(__file__).resolve().parent / "snapshot" / "youngcrc"
LIVE_CACHE = P.cache / "youngcrc"
EXTRA = {
    "variants": "data/molecular/variants.json",
    "clinical_sources": "data/clinical/sources.json",
    "rank_comparisons": "data/proteomics/b7_43_2026-10-29/analysis/reference_rank_comparisons.tsv",
}
PRIMARY_REFERENCE = "Astral_CRC"  # 27 CRC tumours; highest median correlation with the query in qc_summary
PROTEOMICS_SAMPLE = "M-SAMPLE-010"  # "Rectal resection block B7", the B7_43 proteomics block
IHC_SAMPLE = "M-SAMPLE-003"

_lock = threading.Lock()
_cached: dict | None = None


class _Memo:
    """One fetch per URL per load, so the manifest the tables are checked against is the one we read."""

    def __init__(self, fetch: Callable[[str], bytes], keep: Path | None = None):
        self.fetch, self.keep, self.seen = fetch, keep, {}

    def __call__(self, url: str) -> bytes:
        if url not in self.seen:
            data = self.fetch(url)
            self.seen[url] = data
            if self.keep is not None:  # CONTRACTS.md rule 4: cache external calls under cache/
                path = self.keep / url[len(BASE_URL):]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        return self.seen[url]


def snapshot_fetch(url: str) -> bytes:
    if not url.startswith(BASE_URL):
        raise PublicCaseError(f"Not a youngcrc.com URL: {url}")
    path = (SNAPSHOT / url[len(BASE_URL):]).resolve()
    if SNAPSHOT.resolve() not in path.parents or not path.exists():
        raise PublicCaseError(f"Not in the offline snapshot: {url[len(BASE_URL):]}")
    return path.read_bytes()


def _verified_extra(fetch: Callable[[str], bytes]) -> dict:
    manifest = json.loads(fetch(BASE_URL + "manifest.json").decode("utf-8"))
    inventory = {item.get("path"): item for item in manifest.get("files", []) if isinstance(item, dict)}
    out, hashes = {}, {}
    for key, path in EXTRA.items():
        entry = inventory.get(path)
        if not entry or not entry.get("sha256") or not isinstance(entry.get("bytes"), int):
            raise PublicCaseError(f"Missing manifest hash/size for {path}")
        if entry["bytes"] > MAX_BYTES:
            raise PublicCaseError(f"Refusing oversized table: {path}")
        data = fetch(BASE_URL + path)
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != entry["bytes"] or digest != entry["sha256"]:
            raise PublicCaseError(f"Release mismatch for {path}")
        hashes[path] = digest
        if path.endswith(".json"):
            payload = json.loads(data.decode("utf-8"))
            out[key] = payload.get("records", payload) if isinstance(payload, dict) else payload
        else:
            out[key] = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig")), delimiter="\t"))
    out["sha256_extra"] = hashes
    return out


def load(prefer_live: bool = True, live_fetch: Callable[[str], bytes] = _fetch) -> dict:
    """Return the raw verified case plus `data_source` ('live' or 'snapshot') and any live-fetch error."""
    attempts = ([("live", live_fetch, LIVE_CACHE)] if prefer_live else []) + [("snapshot", snapshot_fetch, None)]
    errors = []
    for name, fetch, keep in attempts:
        memo = _Memo(fetch, keep)
        try:
            case = load_case(memo)
            case.update(_verified_extra(memo))
            case["data_source"] = name
            case["load_errors"] = errors
            return case
        except (PublicCaseError, OSError, TimeoutError, ValueError) as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
    raise PublicCaseError("; ".join(errors))


def get_case(refresh: bool = False) -> dict:
    global _cached
    with _lock:
        if _cached is None or refresh:
            _cached = load()
        return _cached


# ------------------------------------------------------------------------------------------- shaping for the UI

def gene_symbol(label: str | None) -> str | None:
    """'APC_S1411fs' -> 'APC', 'SOX9;SOX9-AS1' -> 'SOX9'. Loci without a gene symbol return None."""
    if not label:
        return None
    sym = re.split(r"[;_ ]", str(label).strip())[0]
    return sym if re.fullmatch(r"[A-Z0-9][A-Z0-9\-]*", sym) and not sym.startswith("LINC") else None


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def rank_table(case: dict) -> dict:
    """{gene: {cohort: {...}}} from reference_rank_comparisons.tsv. Exploratory rank positions, not fold changes."""
    out: dict = {}
    for r in case.get("rank_comparisons", []):
        g, cohort = r.get("gene"), r.get("reference_cohort")
        if not g or not cohort:
            continue
        out.setdefault(g, {})[cohort] = {
            "query_rank_pct": _num(r.get("query_rank_pct_common_set")),
            "reference_median_rank_pct": _num(r.get("reference_median_rank_pct")),
            "rank_shift_points": _num(r.get("rank_shift_points")),
            "reference_q25": _num(r.get("reference_rank_q25")),
            "reference_q75": _num(r.get("reference_rank_q75")),
            "fraction_reference_below": _num(r.get("fraction_reference_ranks_below_query")),
            "n_reference_detected": int(_num(r.get("n_reference_detected")) or 0),
        }
    return out


def _src_url(src: dict) -> str | None:
    url = src.get("public_prepared_report_url") if src.get("public_prepared_report_available") else None
    return (BASE_URL + url.lstrip("/")) if url else None


def patient_payload(case: dict) -> dict:
    """Everything chapter 3/4 and the workspace show about the patient, each value with its provenance."""
    findings = joined_findings(case)
    samples = {s["sample_id"]: s for s in case["samples"] if s.get("sample_id")}
    clin_src = {s.get("id"): s for s in case.get("clinical_sources", []) if isinstance(s, dict)}
    mol_src = {s["source_id"]: s for s in case["sources"] if s.get("source_id")}
    ranks = rank_table(case)

    def mol_source(sid):
        s = mol_src.get(sid, {})
        return {"id": sid, "label": s.get("label") or sid, "date": s.get("evidence_date"),
                "verification": s.get("verification"), "url": _src_url(s), "resolved": sid in mol_src}

    def clin_source(sid):
        s = clin_src.get(sid, {})
        return {"id": sid, "label": s.get("title") or sid, "date": s.get("document_date"), "kind": s.get("kind"),
                "url": _src_url(s), "resolved": sid in clin_src}

    timeline = []
    for e in sorted(case["timeline"], key=lambda r: r.get("date") or ""):
        timeline.append({"id": e.get("id"), "date": e.get("date"), "end_date": e.get("end_date"),
                         "date_precision": e.get("date_precision"), "category": e.get("category"),
                         "title": e.get("title"), "summary": e.get("summary"), "status": e.get("status"),
                         "limitations": e.get("limitations") or [],
                         "sources": [clin_source(s) for s in e.get("source_ids") or []]})

    fnd = []
    for f in findings:
        fnd.append({"finding_id": f["finding_id"], "feature": f["feature"], "value": f["value"], "unit": f["unit"],
                    "evidence_class": f["evidence_class"], "current_node_status": f["current_node_status"],
                    "sample_id": f["sample_id"], "sample_label": f["sample_label"], "sample_date": f["sample_date"],
                    "assay_id": f["assay_id"], "assay_label": f["assay_label"],
                    "reference_build": f["reference_build"], "provenance_status": f["provenance_status"],
                    "missing_source_ids": f["missing_source_ids"], "notes": f["notes"],
                    "sources": [mol_source(s) for s in f["source_ids"]]})

    variants = []
    for v in case.get("variants", []):
        variants.append({"variant_id": v.get("variant_id"), "gene_label": v.get("gene_label"),
                         "symbol": gene_symbol(v.get("gene_label")),
                         "protein_annotation": v.get("protein_annotation"),
                         "annotation_status": v.get("annotation_status"), "evidence_class": v.get("evidence_class"),
                         "reference_build": v.get("reference_build"), "notes": v.get("notes"),
                         "sample_id": None,  # the release does not attach these research-audit loci to a specimen
                         "sources": [mol_source(s) for s in v.get("source_ids") or []]})

    panels = []
    for r in case["proteomics_panels"]:
        panels.append({"panel": r.get("panel"), "reference": r.get("reference"),
                       "markers": [m for m in (r.get("markers") or "").split(";") if m],
                       "median_rank_shift_points": _num(r.get("median_rank_shift_points")),
                       "median_centered_log2_signal_contrast": _num(r.get("median_centered_log2_signal_contrast"))})

    spec = []
    for sid, s in samples.items():
        spec.append({"sample_id": sid, "label": s.get("label"), "date": s.get("collection_date"),
                     "site": s.get("anatomical_site"), "role": s.get("role"),
                     "date_precision": s.get("date_precision"), "preservation": s.get("preservation"),
                     "n_findings": sum(1 for f in fnd if f["sample_id"] == sid),
                     "proteomics": sid == PROTEOMICS_SAMPLE})
    spec.sort(key=lambda s: (s["date"] is None, s["date"] or ""))

    prot_sample = samples.get(PROTEOMICS_SAMPLE, {})
    return {
        "case_id": "youngcrc",
        "display_name": "Alexander Young",
        "release": case.get("release"),
        "data_source": case.get("data_source"),
        "load_errors": case.get("load_errors", []),
        "sha256": {**case.get("sha256", {}), **case.get("sha256_extra", {})},
        "base_url": BASE_URL,
        "timeline": timeline,
        "findings": fnd,
        "variants": variants,
        "specimens": spec,
        "proteomics": {
            "sample_id": PROTEOMICS_SAMPLE if prot_sample else None,
            "sample_label": prot_sample.get("label"), "sample_date": prot_sample.get("collection_date"),
            "qc": case["proteomics_qc"], "panels": panels, "primary_reference": PRIMARY_REFERENCE,
            "n_genes_ranked": len(ranks),
            "report_url": BASE_URL + "reports/proteomics/quantitative-2026-10-29/index.html",
            "caveat": ("Rank positions of one treated 2024 primary-tumour block against public reference cohorts. "
                       "Not concentrations, not matched-normal fold changes, no differential-expression test, "
                       "and not a measurement of current metastatic nodes."),
        },
    }


def gene_ranks(case: dict, symbols) -> dict:
    ranks = rank_table(case)
    return {s: ranks[s] for s in symbols if s in ranks}


def top_rank_shifts(case: dict, n: int = 12, cohort: str = PRIMARY_REFERENCE, min_ref: int = 20) -> dict:
    """Largest |rank shift| genes vs one reference cohort, for exploration only (no significance is implied)."""
    rows = []
    for g, by in rank_table(case).items():
        r = by.get(cohort)
        if r and r["rank_shift_points"] is not None and r["n_reference_detected"] >= min_ref:
            rows.append((g, r["rank_shift_points"]))
    rows.sort(key=lambda x: x[1])
    return {"cohort": cohort, "min_reference_detected": min_ref,
            "lowest": [g for g, _ in rows[:n]], "highest": [g for g, _ in rows[-n:][::-1]]}
