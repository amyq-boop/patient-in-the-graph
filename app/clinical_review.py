"""Pure presentation model for a public, provenance-linked oncology case review.

Does not infer clinical eligibility, drug response or future outcomes. All case
observations remain attributed to a sample and dated source. No EHR connectivity.
"""
from __future__ import annotations

from datetime import date
from html import escape


CASE_NAME = "Alexander Young"


def txt(value) -> str:
    """Escape public dataset strings before embedding in Streamlit HTML."""
    if value is None or value == "":
        return "Not reported"
    return escape(str(value), quote=True)


def format_value(finding: dict) -> str:
    value = finding.get("value")
    unit = finding.get("unit")
    return f"{value} {unit}".strip() if value is not None and unit else str(value) if value is not None else "Not reported"


def is_linked(finding: dict) -> bool:
    return finding.get("provenance_status") == "linked" and bool(finding.get("source_ids"))


def assay_family(finding: dict) -> str:
    name = str(finding.get("assay_label") or "").casefold()
    if "ihc" in name or "valius" in name or "immunohisto" in name:
        return "Protein / immunohistochemistry"
    if "rna" in name:
        return "RNA sequencing / expression"
    if "dna" in name or "caris" in name or "tempus" in name:
        return "DNA molecular profiling"
    return "Molecular assay (see original report)"


def selected_lineage(finding: dict) -> list[dict]:
    """Four observed, citable steps. Biological graph assertions are deliberately separate."""
    return [
        {"step": "01", "title": "Tissue specimen", "value": finding.get("sample_label") or "Unknown",
         "sub": f"Collected {finding.get('sample_date') or 'date not reported'} · {finding.get('sample_id') or 'no specimen ID'}"},
        {"step": "02", "title": "Laboratory assay", "value": finding.get("assay_label") or "Unknown",
         "sub": f"{assay_family(finding)} · {finding.get('assay_id') or 'no assay ID'}"},
        {"step": "03", "title": "Reported finding", "value": f"{finding.get('feature') or 'Unnamed'}: {format_value(finding)}",
         "sub": f"Source extraction · {finding.get('finding_id') or 'no finding ID'}"},
        {"step": "04", "title": "Provenance", "value": "Source-linked" if is_linked(finding) else "Incomplete provenance",
         "sub": f"{len(finding.get('source_ids') or [])} source record(s) · reporting status only"},
    ]


def clinical_questions(finding: dict) -> list[str]:
    """Review prompts, NOT personalized medical advice or drug suggestions."""
    return [
        "Does this archived specimen represent the disease site and time point under discussion?",
        "Is the assay and its interpretation clinically validated for the proposed use?",
        "Do any research graph connections warrant independent review against clinical guidelines and primary evidence?",
        "What contemporary molecular or pathology evidence would be needed before acting on a hypothesis?",
    ]


def build_handoff(case: dict, finding: dict, trace: dict | None = None) -> str:
    """Export an evidence-linked draft for clinician review, not a clinical decision record."""
    lines = [
        "# Case review — Alexander Young (public research dataset)",
        "", "**Status:** Research preview; not a clinical order, decision support recommendation, or EHR record.",
        f"**Public dataset release:** {case.get('release', 'unknown')}",
        "**Source:** https://youngcrc.com/", "",
        "## Reviewed molecular observation",
        f"- Finding: {finding.get('feature', 'Unknown')} = {format_value(finding)}",
        f"- Specimen: {finding.get('sample_label', 'Unknown')} ({finding.get('sample_id', 'unassigned')})",
        f"- Specimen collection date: {finding.get('sample_date') or 'not reported'}",
        f"- Assay: {finding.get('assay_label', 'Unknown')} ({finding.get('assay_id', 'unassigned')})",
        f"- Evidence class: {finding.get('evidence_class', 'unclassified')}",
        f"- Source IDs: {', '.join(finding.get('source_ids') or []) or 'none'}",
        f"- Provenance join: {finding.get('provenance_status', 'unknown')}",
        f"- Present-day metastatic status from this assay: {finding.get('current_node_status') or 'not assessed'}",
        f"- Original report caveat: {finding.get('notes') or 'see original report'}", "",
        "## Research graph review",
    ]
    if trace and trace.get("n_graph_edges", 0):
        lines.extend([f"- Execution: {trace.get('mode', 'unspecified')}",
                      f"- Graph edges returned: {trace.get('n_graph_edges', 0)}",
                      "- Retrieved connections are graph assertions, not clinical efficacy evidence."])
        for row in (trace.get("selected_edges") or [])[:8]:
            lines.append(f"- {row.get('source')} --{row.get('relation')}--> {row.get('target')} [{row.get('edge_id')}]")
    else:
        lines.append("- No patient-specific biological graph inference was performed or verified for this handoff.")
    lines.extend(["", "## Questions for a qualified clinical reviewer"])
    for q in clinical_questions(finding):
        lines.append(f"- {q}")
    lines.extend(["", "## Interpretation boundaries",
                  "- This case includes historical samples; do not infer current metastatic expression or drug sensitivity.",
                  "- Citation ID validation does not establish biological causality or therapeutic benefit.",
                  "- The repository's clear-cell kidney-cancer risk model is not applied to this colorectal case.",
                  "- All interpretations require independent qualified clinical review."])
    return "\n".join(lines) + "\n"


def evidence_status(event: dict) -> str:
    return str(event.get("status") or "not classified").replace("_", " ")


def recent_events(case: dict, category: str = "All", count: int = 12) -> list[dict]:
    events = [r for r in case.get("timeline", []) if isinstance(r, dict)]
    events = [r for r in events if category == "All" or r.get("category") == category]
    # sorted chronological, newest first; no claim that last record is a verified outcome
    return sorted(events, key=lambda x: x.get("date") or "", reverse=True)[:count]
