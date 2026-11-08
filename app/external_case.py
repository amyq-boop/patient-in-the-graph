"""Read Alexander Young's public, patient-led oncology data without touching CPTAC models.

The downloaded tables are checked against the website's release manifest. Every
finding retains specimen, assay, and source metadata. This module has no Streamlit
dependency, and intentionally does not calculate cancer risk or drug response.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from urllib.request import Request, urlopen
from typing import Callable

BASE_URL = "https://youngcrc.com/"
MAX_BYTES = 4_000_000  # Curated tables only: never fetch raw genomes or DICOM here.
FILES = {
    "timeline": "data/clinical/timeline.json",
    "findings": "data/molecular/findings.json",
    "samples": "data/molecular/samples.json",
    "assays": "data/molecular/assays.json",
    "sources": "data/molecular/sources.json",
    "proteomics_qc": "data/proteomics/b7_43_2026-10-29/analysis/qc_summary.json",
    "proteomics_panels": "data/proteomics/b7_43_2026-10-29/analysis/marker_panel_summary.tsv",
}


class PublicCaseError(ValueError):
    """A public dataset fetch failed or did not match its published manifest."""


def _fetch(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Patient-in-the-Graph research demo/1.0"})
    with urlopen(req, timeout=12) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise PublicCaseError(f"Data file exceeds the {MAX_BYTES:,}-byte safety limit")
    return data


def _read_json(data: bytes, label: str):
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise PublicCaseError(f"Invalid JSON in {label}") from exc


def _records(payload, name: str) -> list[dict]:
    records = payload if isinstance(payload, list) else payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
        raise PublicCaseError(f"Unexpected record structure in {name}")
    return records


def load_case(fetch_bytes: Callable[[str], bytes] = _fetch) -> dict:
    """Fetch selected small files, verifying each byte count and SHA-256.

    `fetch_bytes` is injectable to run tests offline. The live manifest's release
    number is shown in the interface rather than silently claiming an old pin.
    """
    raw_manifest = fetch_bytes(BASE_URL + "manifest.json")
    if len(raw_manifest) > MAX_BYTES:
        raise PublicCaseError("Manifest exceeds transfer limit")
    manifest = _read_json(raw_manifest, "manifest.json")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise PublicCaseError("Manifest is missing its files inventory")
    inventory = {item.get("path"): item for item in manifest["files"] if isinstance(item, dict)}

    loaded = {}
    hashes = {}
    for key, path in FILES.items():
        entry = inventory.get(path)
        if not entry or not entry.get("sha256") or not isinstance(entry.get("bytes"), int):
            raise PublicCaseError(f"Missing manifest hash/size for {path}")
        if entry["bytes"] > MAX_BYTES:
            raise PublicCaseError(f"Refusing oversized curated table: {path}")
        data = fetch_bytes(BASE_URL + path)
        digest = hashlib.sha256(data).hexdigest()
        if len(data) != entry["bytes"] or digest != entry["sha256"]:
            raise PublicCaseError(f"Release mismatch for {path}. Retry after the site update completes.")
        hashes[path] = digest
        if path.endswith(".json"):
            loaded[key] = _read_json(data, path)
        else:
            text = data.decode("utf-8-sig")
            loaded[key] = list(csv.DictReader(io.StringIO(text), delimiter="\t"))
    for key in ("timeline", "findings", "samples", "assays", "sources"):
        loaded[key] = _records(loaded[key], key)
    if not isinstance(loaded["proteomics_qc"], dict):
        raise PublicCaseError("Invalid proteomics quality-control summary")
    loaded["release"] = str(manifest.get("release", "unknown"))
    loaded["sha256"] = hashes
    return loaded


def joined_findings(case: dict) -> list[dict]:
    """Join selected report observations to their specimen, assay, and sources.

    `provenance_status` validates ID resolution only, *not* clinical or biological
    truth or treatment efficacy.
    """
    samples = {row["sample_id"]: row for row in case["samples"] if row.get("sample_id")}
    assays = {row["assay_id"]: row for row in case["assays"] if row.get("assay_id")}
    sources = {row["source_id"]: row for row in case["sources"] if row.get("source_id")}
    result = []
    for finding in case["findings"]:
        sample = samples.get(finding.get("sample_id"))
        assay = assays.get(finding.get("assay_id"))
        ids = list(dict.fromkeys(
            list(finding.get("source_ids") or [])
            + list((sample or {}).get("source_ids") or [])
            + list((assay or {}).get("source_ids") or [])
        ))
        missing = [src_id for src_id in ids if src_id not in sources]
        # A missing specimen or assay also blocks a complete join.
        complete = sample is not None and assay is not None and bool(ids) and not missing
        result.append({
            "finding_id": finding.get("finding_id"),
            "feature": finding.get("feature"),
            "value": finding.get("value"),
            "unit": finding.get("unit"),
            "evidence_class": finding.get("evidence_class"),
            "current_node_status": finding.get("current_node_status"),
            "sample_id": finding.get("sample_id"),
            "sample_label": (sample or {}).get("label", "Unknown specimen"),
            "sample_date": (sample or {}).get("collection_date"),
            "assay_id": finding.get("assay_id"),
            "assay_label": (assay or {}).get("label", "Unknown assay"),
            "reference_build": (assay or {}).get("reference_build"),
            "source_ids": ids,
            "sources": [sources[sid] for sid in ids if sid in sources],
            "missing_source_ids": missing,
            "provenance_status": "linked" if complete else "incomplete",
            "notes": finding.get("notes") or "",
        })
    return result
