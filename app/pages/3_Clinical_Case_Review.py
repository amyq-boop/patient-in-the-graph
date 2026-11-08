"""Clinician-facing *research demo* of longitudinal public case evidence.

From specimen -> assay -> observed result -> citable graph assertion -> review
question. This is NOT a clinical decision support system or an EHR connector.

Run from repo root:
    python -m streamlit run app/pages/3_Clinical_Case_Review.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402
from app.clinical_review import (  # noqa: E402
    assay_family, build_handoff, evidence_status, format_value, is_linked,
    recent_events, selected_lineage, txt,
)
from app.external_case import BASE_URL, FILES, PublicCaseError, joined_findings, load_case  # noqa: E402
from app.external_reasoner import (  # noqa: E402
    DEFAULT_QUESTION, available_markers, graph_prerequisites, run_case, run_source_check,
)

st.set_page_config(page_title="Case Review | Patient-in-the-Graph", page_icon="🧬", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Manrope:wght@500;600;700;800&display=swap');
:root { --navy:#10213c; --ink:#172942; --muted:#62718a; --cyan:#dff4f4; --accent:#087f8c; --rose:#a83556; --light:#f5f8fb; --border:#dde6ee; }
html, body, [class*="css"], [data-testid="stApp"] { font-family: 'DM Sans',sans-serif; }
[data-testid="stAppViewContainer"] { background:#f5f8fb; }
[data-testid="stHeader"] { background:rgba(245,248,251,.94); }
.block-container { max-width:1330px; padding-top:1.1rem!important; padding-bottom:4rem!important; }
h1,h2,h3,h4 { font-family:'Manrope',sans-serif!important; color:#172942; letter-spacing:-.035em; }
[data-testid="stMetric"] { background:white; border:1px solid #dde6ee; border-radius:16px; padding:18px 21px; }
[data-testid="stMetricLabel"] { font-size:.82rem; color:#65758c; }
[data-testid="stMetricValue"] { font-family:'Manrope',sans-serif; font-weight:800; }
[data-testid="stTabs"] [data-baseweb="tab-list"] { gap:10px; border-bottom:1px solid #dde6ee; }
[data-testid="stTabs"] button { padding:.8rem 1rem; }
[data-testid="stTabs"] [aria-selected="true"] { color:#087f8c; }
[data-testid="stAlert"] { border-radius:12px; }
[data-testid="stSelectbox"]>div { border-radius:12px; }
.hero { background:radial-gradient(circle at 89% 14%,#315e71 0%,#112946 43%,#0a1930 100%);color:#eef9fa;border-radius:22px;padding:30px 35px 28px;position:relative;overflow:hidden;margin-bottom:17px; }
.hero:after {content:'';position:absolute;right:-40px;top:-120px;width:440px;height:440px;border:1px solid #6db1b954;border-radius:50%;box-shadow:0 0 0 42px #82cad10b,0 0 0 92px #82cad108;pointer-events:none; }
.hero .eyebrow{color:#a7d8e2;letter-spacing:.16em;font-weight:700;font-size:.7rem;text-transform:uppercase;margin-bottom:13px;}
.hero h1{color:#f8fdff!important;font-size:clamp(1.8rem,3.2vw,3rem);line-height:1.12;margin:0 0 13px;max-width:760px;font-weight:800;}
.hero .sub{color:#bed1df;max-width:760px;font-size:1.02rem;line-height:1.55;}
.hero .tags{display:flex;flex-wrap:wrap;gap:8px;margin-top:19px;}
.hero .tag{font-size:.76rem;background:#ffffff16;border:1px solid #ffffff31;color:#e2f1f4;border-radius:999px;padding:6px 10px;}
.kicker {text-transform:uppercase;font-size:.73rem;color:#087f8c;letter-spacing:.11em;font-weight:800;margin-top:18px;}
.section-intro{color:#586b80;margin-top:-8px;margin-bottom:16px;max-width:1000px;line-height:1.5;}
.panel {background:#fff;border:1px solid #dde6ee;border-radius:17px;padding:20px 23px;margin-bottom:16px;}
.panel h3{font-size:1.12rem;margin:0 0 10px;}
.label {font-size:.71rem;color:#68798e;text-transform:uppercase;letter-spacing:.075em;font-weight:800;margin-bottom:6px;}
.big-value {font-family:'Manrope',sans-serif;font-size:1.35rem;line-height:1.4;color:#192c48;font-weight:800;}
.small {color:#667791;font-size:.83rem;line-height:1.55;}
.pipeline{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin:12px 0 18px;}
.pipe-step {background:#fff;border:1px solid #dce7ee;border-radius:16px;padding:17px 15px;min-height:164px;box-shadow:0 4px 11px #10305004;position:relative;}
.pipe-step::after{content:'›';position:absolute;right:-12px;top:54px;z-index:1;font-size:29px;color:#2c91a1;font-weight:700;}
.pipe-step:last-child::after{content:'';}
.pipe-num {display:inline-flex;align-items:center;justify-content:center;background:#e5f4f3;color:#147984;border-radius:8px;font-size:.73rem;width:31px;height:25px;font-weight:800;margin-bottom:10px;}
.pipe-title{font-size:.77rem;font-weight:700;color:#65758c;text-transform:uppercase;letter-spacing:.06em;}
.pipe-value{font-size:.98rem;font-weight:800;line-height:1.36;margin:9px 0;color:#193048;word-break:break-word;}
.pipe-sub{font-size:.76rem;color:#718098;line-height:1.48;word-break:break-word;}
.callout {border-left:4px solid #087f8c;background:#ebf6f5;border-radius:12px;padding:13px 17px;color:#2a5362;line-height:1.55;}
.callout-warn{background:#fff7e8;border-left-color:#ba7a22;color:#704b25;}
.callout-red{background:#fff0f2;border-left-color:#bc4266;color:#783448;}
.graph-edge{display:grid;grid-template-columns:minmax(0,1fr) 190px minmax(0,1fr);align-items:center;gap:10px;background:#fff;border:1px solid #dce6ed;border-radius:13px;padding:13px 15px;margin-bottom:9px;}
.graph-node{font-weight:750;color:#17304d;overflow-wrap:anywhere;}
.graph-link{text-align:center;color:#0a8491;font-size:.73rem;font-weight:750;overflow-wrap:anywhere;}
.graph-link small{color:#71849b;font-weight:600;display:block;margin-top:3px;}
.timeline-item{display:grid;grid-template-columns:115px 1fr;gap:19px;align-items:start;padding:16px 3px 17px;border-bottom:1px solid #edf0f3;}
.timeline-date{color:#087f8c;font-family:'Manrope',sans-serif;font-size:.87rem;font-weight:800;}
.timeline-name{color:#172c49;font-weight:800;font-size:1.01rem;line-height:1.35;}
.timeline-desc{color:#4f6079;font-size:.87rem;margin-top:6px;line-height:1.55;}
.timeline-src{font-size:.73rem;color:#7a8a9c;margin-top:5px;}
.audit{font-family:ui-monospace,SFMono-Regular,monospace;color:#456079;font-size:.73rem;word-break:break-all;}
@media(max-width:950px){.pipeline{grid-template-columns:repeat(2,minmax(0,1fr));}.pipe-step:nth-child(2)::after{content:'';}.graph-edge{grid-template-columns:1fr;}.graph-link{text-align:left;}}
@media(max-width:580px){.pipeline{grid-template-columns:1fr;}.pipe-step::after{content:'';}.timeline-item{grid-template-columns:1fr;gap:4px;}.hero{padding:24px;}}
</style>
""", unsafe_allow_html=True)

@st.cache_data(ttl=3600, show_spinner=False)
def get_case():
    return load_case()

try:
    with st.spinner("Verifying the public case release and source hashes…"):
        case = get_case()
except (PublicCaseError, OSError, ValueError, TimeoutError) as exc:
    st.error(f"Could not verify the public dataset: {exc}")
    st.markdown("[Open the original source manifest](https://youngcrc.com/manifest.json)")
    st.stop()

findings = joined_findings(case)
linked = sum(is_linked(f) for f in findings)
qc = case["proteomics_qc"]
markers = available_markers(case)
by_marker = {f["finding_id"]: f for f in markers}

st.markdown(f"""
<div class="hero">
  <div class="eyebrow">PATIENT-IN-THE-GRAPH &nbsp; / &nbsp; TUMOR BOARD WORKBENCH &nbsp; / &nbsp; RESEARCH PREVIEW</div>
  <h1>From a tumor sample to a traceable clinical question.</h1>
  <div class="sub"><b>Alexander Young · Metastatic rectal cancer</b><br>
  Explore the longitudinal record, specimen-specific DNA / RNA and protein findings,
  and the biological graph relationships that may warrant review.</div>
  <div class="tags"><span class="tag">Public patient-led record</span>
  <span class="tag">Dataset release {txt(case.get('release'))}</span>
  <span class="tag">No EHR connection</span><span class="tag">Not for treatment decisions</span></div>
</div>
""", unsafe_allow_html=True)

m1,m2,m3,m4 = st.columns(4)
m1.metric("Traceable molecular findings", f"{linked}/{len(findings)}")
m2.metric("Protein groups detected", f"{qc.get('tumour_detected_groups', 0):,}")
m3.metric("Higher-support protein groups", f"{qc.get('high_support_groups', 0):,}")
m4.metric("Dated clinical events", len(case.get("timeline", [])))

st.markdown("<div class='kicker'>CASE WORKFLOW</div>", unsafe_allow_html=True)
st.subheader("What can we learn from this patient's documented record?")
st.markdown("<div class='section-intro'>Follow the original observation from a dated tissue specimen through the laboratory assay to the published finding. Then review research graph connections without mistaking them for evidence of treatment benefit.</div>", unsafe_allow_html=True)

# Put the four IHC graph-compatible observations first, followed by the full public panel.
marker_ids = [f["finding_id"] for f in markers]
all_ids = marker_ids + [f["finding_id"] for f in findings if f["finding_id"] not in marker_ids]
by_id = {f["finding_id"]: f for f in findings}
selected_id = st.selectbox(
    "Explore a measured tumor feature",
    all_ids,
    format_func=lambda fid: (f"{by_id[fid]['feature']} · {format_value(by_id[fid])}"
                             f"   [{by_id[fid]['sample_label']}, {by_id[fid]['sample_date']}]")
)
f = by_id[selected_id]

st.markdown('<div class="kicker">EVIDENCE LINEAGE · CLICK A DIFFERENT FINDING ABOVE TO REBUILD</div>', unsafe_allow_html=True)
steps = selected_lineage(f)
step_html = "".join(
    f'<div class="pipe-step"><span class="pipe-num">{txt(s["step"])}</span>'
    f'<div class="pipe-title">{txt(s["title"])}</div>'
    f'<div class="pipe-value">{txt(s["value"])}</div>'
    f'<div class="pipe-sub">{txt(s["sub"])}</div></div>'
    for s in steps
)
st.markdown(f'<div class="pipeline">{step_html}</div>', unsafe_allow_html=True)

col_obs, col_bound = st.columns([1.25,1])
with col_obs:
    st.markdown('<div class="label">WHAT THE REPORT ACTUALLY SAYS</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="panel"><div class="big-value">{txt(f["feature"])}: {txt(format_value(f))}</div>'
        f'<div class="small" style="margin-top:10px">{txt(f.get("notes") or "The record does not supply further interpretation.")}</div>'
        f'<div class="small" style="margin-top:8px">Finding ID: <b>{txt(f["finding_id"])}</b> '
        f'· Evidence class: {txt(f.get("evidence_class"))}</div></div>', unsafe_allow_html=True,
    )
with col_bound:
    st.markdown('<div class="label">INTERPRETATION BOUNDARY</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="callout callout-warn"><b>Historical tissue ≠ current disease.</b><br>'
        'The sample date and site matter. This finding does not by itself establish the current '
        'state of metastatic lesions, drug sensitivity, or clinical benefit.</div>', unsafe_allow_html=True,
    )
    st.caption(f"Current-node status in source record: {f.get('current_node_status') or 'not assessed'}")

# High-level, chronological clinical context: human-readable and source-specific.
analysis_tab, timeline_tab, protein_tab, audit_tab = st.tabs([
    "01 · Molecular interactions", "02 · Clinical course", "03 · Protein context", "04 · Clinician handoff",
])

with analysis_tab:
    st.subheader("Explore the graph around a measured protein")
    st.markdown("<div class='section-intro'>A marker measurement is a patient-level observation. The graph adds separate, literature-derived connections. Neither is a verified patient-specific mechanism or drug recommendation.</div>", unsafe_allow_html=True)
    if selected_id not in by_marker:
        st.markdown('<div class="callout">This selected observation has no reviewed mapping to a gene seed in the existing demo. Choose CEACAM5, MET, TROP2, or HER2 to inspect knowledge-graph relationships. The source lineage above remains valid.</div>', unsafe_allow_html=True)
    else:
        marker = by_marker[selected_id]
        st.markdown(f'<div class="panel"><div class="label">PATIENT OBSERVATION → KG SEED</div>'
                    f'<div class="big-value">{txt(marker["feature"])} = {txt(format_value(marker))} '
                    f'&nbsp; → &nbsp; {txt(marker["symbol"])} ({txt(marker["curie"])})</div>'
                    f'<div class="small" style="margin-top:9px">The mapping identifies the named gene for retrieval. It does not convert IHC expression into a DNA mutation or drug sensitivity.</div></div>',
                    unsafe_allow_html=True)
        missing = graph_prerequisites()
        if missing:
            st.warning("The large knowledge-graph assets are absent in this Codespace, so live GNN retrieval is unavailable. Source-only verification remains enabled. Missing: " + ", ".join(Path(p).name for p in missing))
        q = st.text_input("Question for the research graph", DEFAULT_QUESTION, max_chars=750)
        try:
            from agent import config as ac
            llm_ready = bool(ac.NIM_API_KEY)
        except Exception:
            llm_ready = False
        use_llm = st.checkbox("Include NVIDIA-generated research synthesis", value=False, disabled=not llm_ready,
                              help="Requires NVIDIA_API_KEY. Deterministic graph summary is the default.")
        c_run,c_src = st.columns(2)
        if c_run.button("Run real GNN → evidence verifier", disabled=bool(missing), type="primary", use_container_width=True):
            with st.spinner("Retrieving real graph edges and checking citations…"):
                try:
                    st.session_state["clinical_review_trace"] = run_case(case, selected_id, q, use_llm=use_llm, demo_rejection=True)
                except Exception as exc:
                    st.session_state.pop("clinical_review_trace", None)
                    st.error(f"Graph run failed: {exc}")
        if c_src.button("Run source verifier only", use_container_width=True):
            try:
                st.session_state["clinical_review_trace"] = run_source_check(case, selected_id, demo_rejection=True)
            except Exception as exc:
                st.session_state.pop("clinical_review_trace", None)
                st.error(f"Source check failed: {exc}")
        trace = st.session_state.get("clinical_review_trace")
        if trace and trace.get("finding_id") == selected_id:
            t1,t2,t3 = st.columns(3)
            t1.metric("Graph edges", trace.get("n_graph_edges", 0))
            t2.metric("Citation-supported", trace.get("verifier",{}).get("n_supported",0))
            t3.metric("Rejected assertions", trace.get("verifier",{}).get("n_removed",0))
            st.caption("Run mode: " + trace.get("mode", "unknown") + ". 'Citation-supported' confirms presence of evidence IDs, not clinical scientific validity.")
            edges = trace.get("selected_edges") or []
            if edges:
                st.markdown("#### Retrieved interactions · each edge citable")
                for e in edges[:10]:
                    st.markdown(
                        f'<div class="graph-edge"><div class="graph-node">{txt(e.get("source"))}</div>'
                        f'<div class="graph-link">{txt(str(e.get("relation", "relation")).replace("_", " "))} →'
                        f'<small>{txt(e.get("edge_id"))} · {txt(e.get("origin"))}</small></div>'
                        f'<div class="graph-node">{txt(e.get("target"))}</div></div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.info("Source-only verification completed. No graph interactions were calculated.")
            with st.expander("Verifier output · kept and removed claims"):
                st.markdown(trace.get("answer") or "No claims were retained.")
                st.dataframe(trace.get("claims") or [], use_container_width=True, hide_index=True)
            st.download_button("Download full machine-readable audit", data=json.dumps(trace, indent=2, default=str),
                               file_name="alexander_young_evidence_trace.json", mime="application/json")
        elif trace:
            st.info("The selected marker changed; rerun the check to update the graph and citations.")

with timeline_tab:
    st.subheader("Longitudinal treatment and disease observations")
    st.markdown("<div class='section-intro'>Dates, treatments, pathology and imaging are taken from the published case chronology. A planned therapy is not counted as administered; observations are not future outcomes.</div>", unsafe_allow_html=True)
    categories = sorted({r.get("category") for r in case.get("timeline", []) if r.get("category")})
    cat = st.selectbox("Focus on", ["All"] + categories, key="course_category")
    count = st.slider("Events to display", min_value=4, max_value=30, value=10, step=2)
    events = recent_events(case, cat, count)
    timeline_items = []
    for r in events:
        limitation_html = ""
        if r.get("limitations"):
            limitation_html = (
                '<div class="timeline-src">Limitations: '
                + txt("; ".join(r.get("limitations") or []))
                + '</div>'
            )

        timeline_items.append(
            f'<div class="timeline-item"><div class="timeline-date">{txt(r.get("date") or "date unknown")}</div>'
            f'<div><div class="timeline-name">{txt(r.get("title"))}</div>'
            f'<div class="timeline-desc">{txt(r.get("summary"))}</div>'
            f'<div class="timeline-src">{txt(evidence_status(r))} · Sources: {txt(", ".join(r.get("source_ids") or []))}</div>'
            + limitation_html + '</div></div>'
        )

    item_html = "".join(timeline_items)
    st.markdown(f'<div class="panel">{item_html}</div>', unsafe_allow_html=True)
    st.link_button("View the full primary clinical timeline", BASE_URL + FILES["timeline"])

with protein_tab:
    st.subheader("Protein signatures in their proper experimental context")
    st.markdown("<div class='section-intro'>Bulk mass-spectrometry signal from the treated 2024 rectal specimen is compared with external reference cohorts. The table below is not a same-patient before/after measurement and cannot establish a targetable current tumor alteration.</div>", unsafe_allow_html=True)
    panels = case.get("proteomics_panels") or []
    refs = sorted({r.get("reference", "") for r in panels if r.get("reference")})
    if refs:
        ref = st.selectbox("Reference cohort", refs, key="prot_ref")
        shown = [r for r in panels if r.get("reference") == ref]
        st.markdown("#### Summary contrasts by published marker panel")
        st.dataframe([{
            "Panel": str(r.get("panel", "")).replace("_", " ").title(),
            "Markers": r.get("n_markers"),
            "Centered log2 signal contrast": round(float(r.get("median_centered_log2_signal_contrast") or 0), 2),
            "Rank shift (percentage points)": round(float(r.get("median_rank_shift_points") or 0), 1),
        } for r in shown], use_container_width=True, hide_index=True)
        with st.expander("Panel member genes"):
            for r in shown:
                st.markdown(f"**{r.get('panel')}**: {r.get('markers')}")
    st.markdown('<div class="callout callout-warn"><b>Scientific boundary:</b> centered between-cohort contrasts are exploratory and not patient-matched fold changes, drug response readouts, or validated treatment biomarkers.</div>', unsafe_allow_html=True)
    st.markdown(f"[Original proteomics report]({BASE_URL}reports/proteomics/quantitative-2026-10-29/index.html) · [Machine-readable table]({BASE_URL + FILES['proteomics_panels']})")

with audit_tab:
    st.subheader("A clinician-reviewable evidence handoff")
    st.markdown("<div class='section-intro'>Prepare a concise record of the selected historical finding, supporting provenance, graph audit if one was run, and questions for qualified review. This is an exportable research brief, not an EHR note or decision recommendation.</div>", unsafe_allow_html=True)
    st.markdown("#### Three questions this workbench can help answer")
    prompts = [
        ("What was actually measured?", "Date, specimen, assay platform, reported value, study limitations, and linked documents."),
        ("How might it relate to biology?", "Knowledge-graph connections with original edge IDs, visually separated from patient measurements."),
        ("What still needs review?", "Whether the result is current, clinically validated, and supported by independent evidence."),
    ]
    pcols = st.columns(3)
    for col, (heading, desc) in zip(pcols, prompts):
        with col:
            st.markdown(f'<div class="panel" style="min-height:165px"><h3>{txt(heading)}</h3><div class="small">{txt(desc)}</div></div>', unsafe_allow_html=True)
    t = st.session_state.get("clinical_review_trace")
    if t and t.get("finding_id") != selected_id:
        t = None
    note = build_handoff(case, f, t)
    st.download_button("Export tumor board research brief (.md)", note,
                       file_name=f"youngcrc_case_review_{selected_id}.md", mime="text/markdown", type="primary")
    with st.expander("Preview exported brief"):
        st.markdown(note)
    with st.expander("Provenance and original report links"):
        st.markdown(f"**Finding:** {f['finding_id']} · **Specimen:** {f['sample_id']} · **Assay:** {f['assay_id']}")
        for src in f.get("sources", []):
            st.markdown(f"**{src.get('source_id')} — {src.get('label', 'Source')}**")
            st.caption(src.get("verification") or "No verification summary")
            url = src.get("public_prepared_report_url")
            if url and src.get("public_prepared_report_available"):
                st.markdown(f"[Prepared report]({BASE_URL + url.lstrip('/')})")
    with st.expander("Reproducibility · published SHA-256 manifest"):
        st.markdown(f"Case release: **{case.get('release')}**. Seven selected files were checked against the live public manifest.")
        for path, digest in case.get("sha256", {}).items():
            st.markdown(f"<div class='audit'>{txt(digest)} &nbsp; {txt(path)}</div>", unsafe_allow_html=True)

st.divider()
st.caption("Patient-led public research case · https://youngcrc.com/ · This proof of concept has no EHR integration, clinical validation, or therapeutic approval logic. Historical sample measurements are not transferred to present-day lesions. The existing kidney-cancer risk model is not used for this case.")
