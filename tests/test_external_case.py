"""Network-free verification of the public-case adapter."""
import hashlib
import json

import pytest

from app.external_case import BASE_URL, FILES, PublicCaseError, joined_findings, load_case


def fake_source(overrides=None):
    files = {
        "timeline": [{"id": "EVT1", "date": "2024-03-20", "title": "scan", "status": "documented"}],
        "findings": {"records": [{"finding_id": "F1", "feature": "gene", "value": "reported",
                                  "sample_id": "S1", "assay_id": "A1", "source_ids": ["SRC1"],
                                  "evidence_class": "previous_clinical_report_extraction",
                                  "current_node_status": "not_assessed_here"}]},
        "samples": {"records": [{"sample_id": "S1", "label": "2024 primary tumor",
                                 "collection_date": "2024-10-22", "source_ids": ["SRC2"]}]},
        "assays": {"records": [{"assay_id": "A1", "label": "test DNA", "reference_build": "GRCh37",
                                "source_ids": ["SRC3"]}]},
        "sources": {"records": [{"source_id": name, "label": name} for name in ("SRC1", "SRC2", "SRC3")]},
        "proteomics_qc": {"tumour_detected_groups": 7706, "high_support_groups": 6468},
        "proteomics_panels": "panel\treference\tn_markers\nimmune\tAstral_CRC\t5\n",
    }
    files.update(overrides or {})
    blobs = {}
    entries = []
    for key, path in FILES.items():
        body = files[key]
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        blobs[BASE_URL + path] = raw
        entries.append({"path": path, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    blobs[BASE_URL + "manifest.json"] = json.dumps({"release": "test", "files": entries}).encode()
    return blobs


def test_joins_preserve_specimen_and_evidence_class():
    blobs = fake_source()
    case = load_case(lambda url: blobs[url])
    result = joined_findings(case)[0]
    assert result["sample_date"] == "2024-10-22"
    assert result["reference_build"] == "GRCh37"
    assert result["evidence_class"] == "previous_clinical_report_extraction"
    assert result["current_node_status"] == "not_assessed_here"
    assert result["source_ids"] == ["SRC1", "SRC2", "SRC3"]
    assert result["provenance_status"] == "linked"
    assert case["proteomics_panels"][0]["reference"] == "Astral_CRC"


def test_rejects_hash_mismatch():
    blobs = fake_source()
    blobs[BASE_URL + FILES["findings"]] = b"[]"
    with pytest.raises(PublicCaseError, match="Release mismatch"):
        load_case(lambda url: blobs[url])


def test_missing_source_is_not_source_linked():
    blobs = fake_source({"sources": {"records": [{"source_id": "SRC1"}]}})
    case = load_case(lambda url: blobs[url])
    result = joined_findings(case)[0]
    assert result["missing_source_ids"] == ["SRC2", "SRC3"]
    assert result["provenance_status"] == "incomplete"


def test_missing_assay_is_not_source_linked():
    blobs = fake_source({"assays": {"records": []}})
    case = load_case(lambda url: blobs[url])
    assert joined_findings(case)[0]["provenance_status"] == "incomplete"
