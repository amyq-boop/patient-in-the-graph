# Alexander Young: clinician-facing research workbench

Add the two Python files and tests from this ZIP to the repository root. This adds
**a NEW Streamlit page**, rather than overwriting the two Alexander Young pages
that may already exist after rebasing with teammates.

```bash
source .venv/bin/activate
python -m pytest tests/test_clinical_review.py tests/test_external_case.py tests/test_external_reasoner.py -q
python -m streamlit run app/pages/3_Clinical_Case_Review.py --server.address 0.0.0.0 --server.port 8501
```

## Demo tour

1. Open with the summary: public patient-led research dataset, measured proteins,
   observed cases, limited to source-linked assertions.
2. Choose **CEACAM5 IHC H-score** in the first dropdown. Follow the chain:
   liver-metastasis specimen (Oct 2024) -> Valius IHC -> reported H-score -> source IDs.
3. Click **Clinical course** to relate the historical specimen to imaging and treatment events.
4. Return to **Molecular interactions**. If large graph assets are available, run
   **Real GNN → evidence verifier**. The UI displays citable `E...` graph edges
   and separates them from the patient's own measurement.
5. If graph assets are not available, use **source verifier only**. The screen
   explicitly displays zero graph edges, with no mock substitute.
6. Open **Clinician handoff**, download the research brief, and show what a clinician
   could review with appropriate clinical judgment and additional evidence.

## Scientific boundaries

- NOT an EHR or FDA-cleared clinical decision support tool.
- Not validated for prognosis, therapy selection or clinical deployment.
- No kidney-cancer outcome-model predictions are applied to this colorectal case.
- Molecular evidence, graph assertions, and clinician review questions are separate layers.
- No individual treatment response or current-metastasis molecular status is inferred.
- Sources are patient-led public data; provenance checks validate file hashes and source joins,
  not the factual correctness or clinical validity of all underlying findings.
