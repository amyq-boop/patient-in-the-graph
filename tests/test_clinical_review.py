"""Safety and provenance tests for the clinician-facing research demo."""
from app.clinical_review import (
    assay_family, build_handoff, format_value, is_linked,
    recent_events, selected_lineage, txt,
)


def example():
    return {
        "feature": "CEACAM5 IHC H-score", "value": 300, "unit": None,
        "sample_label": "Liver metastasis resection specimen",
        "sample_id": "M-SAMPLE-003", "sample_date": "2024-10-22",
        "assay_label": "Valius IHC", "assay_id": "M-ASSAY-011",
        "finding_id": "M-FINDING-007", "evidence_class": "previous_research_report_extraction",
        "current_node_status": "not_assessed_here", "source_ids": ["M-SOURCE-003"],
        "provenance_status": "linked", "notes": "Historical metastatic specimen",
    }


def test_lineage_retains_dates_assay_ids():
    steps = selected_lineage(example())
    assert len(steps) == 4
    assert "2024-10-22" in steps[0]["sub"]
    assert "M-ASSAY-011" in steps[1]["sub"]
    assert "M-FINDING-007" in steps[2]["sub"]
    assert steps[-1]["value"] == "Source-linked"


def test_note_marks_historical_and_abstains():
    note = build_handoff({"release": "1.66"}, example())
    assert "2024-10-22" in note
    assert "M-SAMPLE-003" in note
    assert "M-SOURCE-003" in note
    assert "not a clinical order" in note
    assert "current" in note.lower()
    assert "no patient-specific biological graph inference" in note.lower()


def test_graph_edges_preserve_citations_without_promising_treatment():
    trace = {"n_graph_edges": 1, "mode": "real GNN", "selected_edges": [
        {"source": "CEACAM5", "relation": "gene_gene", "target": "X",
         "edge_id": "E1001"}]}
    note = build_handoff({"release": "1.66"}, example(), trace)
    assert "E1001" in note
    assert "clinical efficacy" in note.lower()
    assert "drug sensitivity" in note.lower()


def test_status_requires_provenance():
    f = example()
    assert is_linked(f)
    f["source_ids"] = []
    assert not is_linked(f)


def test_html_escapes_public_strings():
    assert txt("<script>alert('x')</script>").startswith("&lt;script&gt;")


def test_recent_events_order_and_filter():
    case = {"timeline": [
        {"date": "2024-01-02", "category": "imaging"},
        {"date": "2024-02-01", "category": "pathology"},
        {"date": "2024-03-02", "category": "imaging"},
    ]}
    assert [r["date"] for r in recent_events(case, "imaging")] == ["2024-03-02", "2024-01-02"]


def test_assay_family_does_not_call_ihc_sequencing():
    assert "immunohistochemistry" in assay_family(example()).lower()
    assert format_value(example()) == "300"
