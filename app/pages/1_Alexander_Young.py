"""Independent patient-led colorectal case: no transfer of ccRCC model outputs."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import streamlit as st

from app.external_case import BASE_URL, FILES, PublicCaseError, joined_findings, load_case

st.set_page_config(page_title="Alexander Young | Patient-in-the-Graph", page_icon="🧬", layout="wide")
st.title("Alexander Young: an independent real-world cancer case")
st.caption("Public, patient-led metastatic rectal cancer dataset · Research exploration, not medical advice")
st.markdown(
    "[Original case dataset](https://youngcrc.com/) · "
    "[Data and agent documentation](https://github.com/AlexTISYoung/youngcrc) · "
    "[Quantitative proteomics report](https://youngcrc.com/reports/proteomics/quantitative-2026-10-29/index.html)"
)
st.info(
    "The GNN risk model and E1–E4 experiments in this repository were developed for CPTAC "
    "clear-cell kidney cancer. Their predictions are **not** applied to this colorectal case. "
    "The page below displays source-linked observations and exploratory comparisons only."
)

@st.cache_data(ttl=3600, show_spinner=False)
def get_case():
    return load_case()

try:
    with st.spinner("Checking published file hashes and loading source-linked observations…"):
        case = get_case()
except (PublicCaseError, OSError, TimeoutError, ValueError) as exc:
    st.error(f"Could not verify the public dataset: {exc}")
    st.markdown("You can examine the [source manifest](https://youngcrc.com/manifest.json) directly.")
    st.stop()

findings = joined_findings(case)
qc = case["proteomics_qc"]
st.caption(f"Live website manifest: release {case['release']}. All seven selected tables passed published size/SHA-256 checks.")

m1, m2, m3 = st.columns(3)
m1.metric("Source-linked molecular findings", len(findings))
m2.metric("Proteomics: detected protein groups", f"{qc.get('tumour_detected_groups', 0):,}")
m3.metric("Higher-support protein groups", f"{qc.get('high_support_groups', 0):,}")
st.caption("Proteomics specimen: treated rectal resection, 22 October 2024; not a current lymph-node biopsy or matched patient-normal comparison.")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Molecular observations", "Protein panels", "Clinical chronology", "Evidence check", "Live GNN + verifier"])

with tab1:
    st.subheader("What did the reports actually observe?")
    terms = [f"{f.get('feature') or 'Unnamed'} · {f.get('sample_id')} · {f.get('finding_id')}" for f in findings]
    if not terms:
        st.warning("No molecular findings were released in the selected table.")
    else:
        selected_idx = st.selectbox("Select an observation", range(len(terms)), format_func=lambda i: terms[i])
        f = findings[selected_idx]
        st.write("**Reported finding:**", f"{f['feature']}: {f['value']} {f['unit'] or ''}")
        st.write("**Specimen:**", f"{f['sample_label']} · collected {f['sample_date'] or 'date unknown'} ({f['sample_id']})")
        st.write("**Assay:**", f"{f['assay_label']} · reference build {f['reference_build'] or 'not specified'}")
        st.write("**Evidence class:**", f.get("evidence_class") or "unclassified")
        st.write("**Current metastasis status:**", f.get("current_node_status") or "not evaluated")
        st.write("**Provenance IDs:**", ", ".join(f["source_ids"]) or "none")
        st.write("**Source-record join:**", f["provenance_status"])
        if f["missing_source_ids"]:
            st.error("Unresolved source identifiers: " + ", ".join(f["missing_source_ids"]))
        if f["notes"]:
            st.caption(f["notes"])
        with st.expander("Source records behind this observation"):
            for src in f["sources"]:
                st.markdown(f"**{src.get('source_id')}** — {src.get('label') or 'Unnamed source'}")
                st.caption(src.get("verification") or "No verification narrative supplied")
                url = src.get("public_prepared_report_url")
                if url and src.get("public_prepared_report_available"):
                    st.markdown(f"[Prepared provider report]({BASE_URL + url.lstrip('/')})")
        st.markdown(f"[Raw curated findings JSON]({BASE_URL + FILES['findings']})")

with tab2:
    st.subheader("Archival tumor proteomics")
    st.write(
        "These are measured protein signals and exploratory reference-cohort comparisons. "
        "Rank shifts are not protein concentrations, patient-matched fold changes, or validated clinical effects."
    )
    panels = case["proteomics_panels"]
    refs = sorted({r.get("reference", "") for r in panels if r.get("reference")})
    if refs:
        ref = st.selectbox("Published reference dataset", refs)
        filtered = [r for r in panels if r.get("reference") == ref]
        st.dataframe(filtered, use_container_width=True, hide_index=True)
    st.caption(
        "Reference groups include other patients and/or public controls. HPA colon is one donor; "
        "none is a matched normal sample for Alexander Young."
    )
    st.markdown(f"[Proteomics QC JSON]({BASE_URL + FILES['proteomics_qc']}) · "
                f"[Source table]({BASE_URL + FILES['proteomics_panels']})")

with tab3:
    st.subheader("Dated clinical observations, not future outcomes")
    events = sorted(case["timeline"], key=lambda row: row.get("date") or "")
    categories = sorted({str(r.get('category')) for r in events if r.get('category')})
    category = st.selectbox("Event category", ["All"] + categories)
    shown = [r for r in events if category == "All" or r.get("category") == category]
    st.dataframe(
        [{"date": r.get("date"), "event": r.get("title"), "evidence_status": r.get("status"),
          "description": r.get("summary"), "sources": ", ".join(r.get("source_ids") or []),
          "limitations": "; ".join(r.get("limitations") or [])}
         for r in shown],
        use_container_width=True, hide_index=True,
    )
    st.caption("A reported plan is not an administered treatment; a later scan is not a verified future outcome.")
    st.markdown(f"[Original timeline JSON]({BASE_URL + FILES['timeline']})")

with tab4:
    st.subheader("A falsifiable provenance check")
    st.write(
        "**Accepted as source-linked:** The selected finding is attributed to a known sample, "
        "assay, and source IDs, provided its join status is 'linked'. This validates references, "
        "not the scientific correctness of the underlying statement."
    )
    if findings:
        linked = sum(1 for f in findings if f["provenance_status"] == "linked")
        st.write(f"**Resolved finding joins:** {linked}/{len(findings)}")
    st.write(
        "**Rejected as unsupported:** 'The October 2024 proteomics proves today's metastatic "
        "nodes express the same proteins and identifies an effective treatment.' "
        "The released measurement is from a historical treated primary tumor; the necessary "
        "current-node protein measurements and clinical efficacy evidence are absent."
    )
    st.caption("This is an explicit rule-based evidence demonstration, not a claim that the LLM verifier has been run on this case.")


with tab5:
    st.subheader("Run the real graph retriever and evidence verifier")
    st.caption(
        "Research only · Uses the repository's trained GNN-B retriever and actual graph edge IDs. "
        "Does not use the kidney-cancer risk head or make treatment recommendations."
    )
    from app.external_reasoner import (DEFAULT_QUESTION, ExternalReasoningError,
                                       available_markers, graph_prerequisites, run_case, run_source_check)
    selectable = available_markers(case)
    if not selectable:
        st.error("No joined, source-backed IHC marker observation is available.")
    else:
        marker_by_id = {r["finding_id"]: r for r in selectable}
        chosen = st.selectbox("Measured marker (2024 liver-metastasis specimen)",
                              list(marker_by_id), format_func=lambda fid:
                              f"{marker_by_id[fid]['feature']} = {marker_by_id[fid]['value']} · {fid}")
        st.caption(
            f"Source: {marker_by_id[chosen]['sample_label']} · "
            f"{marker_by_id[chosen]['sample_date']}; "
            f"assay {marker_by_id[chosen]['assay_label']}. "
            "These are report extractions, not verified measurements of today's metastases."
        )
        q = st.text_input("Research question", DEFAULT_QUESTION, max_chars=750)
        from agent import config as agent_config
        can_llm = bool(agent_config.NIM_API_KEY)
        use_llm = st.checkbox("Use NVIDIA LLM for evidence-cited synthesis", value=False,
                              disabled=not can_llm, help="Requires NVIDIA_API_KEY in .env")
        if not can_llm:
            st.caption("No NVIDIA key detected. Real GNN retrieval + the real verifier still work without one.")
        inject = st.checkbox("Show verifier rejecting a deliberately false claim", value=True)
        missing = graph_prerequisites()
        if missing:
            st.warning("GNN-B cannot start without the processed graph files: "
                       + ", ".join(missing) + ". This case never substitutes mock graph data.")
        run_col, src_col = st.columns(2)
        if run_col.button("Run GNN → synthesis → verifier", type="primary", disabled=bool(missing)):
            with st.spinner("Running real GNN-B retrieval and strict citation verifier…"):
                try:
                    st.session_state["youngcrc_reasoner_trace"] = run_case(
                        case, chosen, q, use_llm=use_llm, demo_rejection=inject)
                except Exception as exc:
                    st.session_state.pop("youngcrc_reasoner_trace", None)
                    st.error(f"Graph reasoning could not complete: {exc}")
        if src_col.button("Run source-only verifier (no GNN)"):
            try:
                st.session_state["youngcrc_reasoner_trace"] = run_source_check(
                    case, chosen, demo_rejection=inject)
            except Exception as exc:
                st.session_state.pop("youngcrc_reasoner_trace", None)
                st.error(f"Source-only check failed: {exc}")
        t = st.session_state.get("youngcrc_reasoner_trace")
        if t:
            if t["finding_id"] != chosen:
                st.info("Change detected. Click Run to analyze the currently selected marker.")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Real graph edges retrieved", t["n_graph_edges"])
                c2.metric("Verifier-supported claims", t["verifier"].get("n_supported", 0))
                c3.metric("Rejected claims", t["verifier"].get("n_removed", 0))
                st.caption(f"Execution mode: {t['mode']}")
                st.markdown("#### Verified-output draft (citation-ID check)")
                st.markdown(t["answer"] or "No claims survived the verifier.")
                st.caption("Citation checks confirm ID presence and a simple entity match, not "
                           "scientific/clinical correctness. Independent review is still necessary.")
                st.markdown("#### Traceable graph connections")
                st.dataframe(t["selected_edges"], use_container_width=True, hide_index=True)
                with st.expander("Verifier audit: all claims, including removed"):
                    st.dataframe(t["claims"], use_container_width=True, hide_index=True)
                with st.expander("Full reproducible trace and data provenance"):
                    st.json({k:v for k,v in t.items() if k != "tool_calls"})
                st.download_button("Download trace JSON",
                                   data=__import__('json').dumps(t, indent=2, default=str),
                                   file_name=f"youngcrc_{chosen}_graph_trace.json",
                                   mime="application/json")

st.divider()
st.caption(
    "Source: Alexander Young, Open Cancer Data, website release " + case["release"]
    + ". Selected source files were verified against SHA-256 hashes in the public manifest. "
    "Third-party report rights remain with their respective owners."
)
with st.expander("File-level SHA-256 provenance"):
    for p, digest in case["sha256"].items():
        st.code(f"{digest}  {p}", language="text")
