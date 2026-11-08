# Alexander Young independent public-case demo

This adds a **separate Streamlit page** to Patient-in-the-Graph, without modifying the CPTAC ccRCC data contracts or model. It is a source-linked biomedical research viewer, **not** a colorectal risk or response predictor.

## Install

From the **root** of `amyq-boop/patient-in-the-graph`, copy the three files in this package to the same paths:

- `app/external_case.py`
- `app/pages/1_Alexander_Young.py`
- `tests/test_external_case.py`

Then run:

```bash
python -m pytest tests/test_external_case.py -q
streamlit run app/streamlit_app.py
```

Use the Streamlit multipage navigation menu to open **Alexander Young**. If you only want this page, run `streamlit run app/pages/1_Alexander_Young.py` from the repository root.

## Actual inputs

Fetched over HTTPS from <https://youngcrc.com/>, then validated against the live site's `manifest.json`:

- `data/clinical/timeline.json`
- `data/molecular/findings.json`
- `data/molecular/samples.json`
- `data/molecular/assays.json`
- `data/molecular/sources.json`
- `data/proteomics/b7_43_2026-10-29/analysis/qc_summary.json`
- `data/proteomics/b7_43_2026-10-29/analysis/marker_panel_summary.tsv`

Every selected file must match the manifest byte count and SHA-256. The page displays the live release number and exact checked hashes. If the publisher deploys a new release midway through fetching, the view fails closed rather than mixing versions.

No raw FASTQs, BAMs, DICOM images or third-party proprietary reports are mirrored into your repository. The source website's curated content is CC BY 4.0, while third-party outputs retain their own rights.

## Demo positioning

1. Choose a finding and show its report classification, specimen, assay, source links, and date.
2. Show the **real proteomics** QC and reference-marker table from the treated **2024 primary rectal resection**. These are exploratory rank comparisons, not matched-normal fold changes or measurements of current metastases.
3. Show documented treatment/imaging events on the dated clinical timeline.
4. Show the rule-based provenance audit; reject the claim that historical proteomics proves current-node expression or effective treatment.

Do **not** connect the existing CPTAC **ccRCC** stage-risk model to this unrelated colorectal case. A genuine prediction demo would require a suitable colorectal model trained/validated on independent cohorts, with a strictly held-out target and no leakage; the external case alone cannot establish response-prediction performance.

## Source citation

Alexander Young, *Open Cancer Data*, <https://youngcrc.com/>. Cite the live release version, exact dataset paths, SHA-256 hashes shown in the page, and specimen/source IDs when reporting a finding. See <https://github.com/AlexTISYoung/youngcrc> for detailed limitations and companion tools.
