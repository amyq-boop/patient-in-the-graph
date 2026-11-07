# Evaluation question set

`questions.jsonl` holds the 20 questions used to build and score the agent (10 patient, 10 disease).
`questions_backup.jsonl` holds P11 (audit P8), which replaces P05 if the risk model cannot produce real feature attributions.

## What the questions are for
1. **Agent development (WS3):** the agent is built and debugged against these.
2. **E3 scoring:** each question runs through the agent and through the same LLM with no tools.
3. **Demo (WS4):** the 5 questions with `"demo": true` are the buttons in the Streamlit app and the cached demo traces (3 patients x 5 questions).
4. **Hand-check (WS3-09):** `expected.notes` (gold answer) and `must_mention` make grading fast.

## Fields
Contract fields (CONTRACTS.md section 8): `qid`, `kind`, `patient_id`, `disease`, `question`, `expected.curies`, `expected.notes` (the gold answer). Added keys:

| Field | Meaning |
|---|---|
| `source_id` | ID in the external audit (P1 to P15, D1 to D15) |
| `category` | target, phenotype, mechanism, genes, drug, pathway, trap, ... |
| `demo` | shown as a button in the demo app (P01 to P05) |
| `trap` | correct behavior is to say the evidence is insufficient (P02, P09, P10) |
| `must_mention` | points an answer must cover; used for gold recall |
| `note` / `backup` / `backup_for` | caveats; P11 in the backup file replaces P05 |

Patient questions use `C3L-MOCK1` until real patients exist; "this patient" = one CPTAC ccRCC tumor. D07 (TSC) and D08 (ADPKD) have `disease: null` until their CURIEs are resolved with `resolve_terms`.

## Scoring (lock before any run)
| Metric | Definition |
|---|---|
| **E3 (primary)** | claims whose cited edge ID or PMID actually supports them / all factual claims |
| **Abstention accuracy** | correct "insufficient evidence" answers / trap questions |
| **Gold recall** | `must_mention` points recovered; stops the agent scoring well by saying less |

Rules for the agent and graders:
1. Separate measured patient facts from disease-level knowledge and hypotheses.
2. A missing edge means unknown, never "not true".
3. Protein abundance is not mutation status, pathway activity, or drug response.
4. Freeze the graph and DisMech versions before comparing agent vs. LLM alone.
5. The LLM-alone baseline may use its general knowledge; the agent is restricted to the graph. Report this as part of the setup.

## Known caveats (from the external audit, Nov 7)
- PrimeKG has no phenotype edges for ccRCC (MONDO:0005005); phenotype seeds come from DisMech.
- DisMech names drugs with ChEBI/NCIT IDs; PrimeKG uses DrugBank. Map them or the agent cannot link a DisMech drug to a PrimeKG drug-target edge.
- D3: the scientific answer includes 2023 to 2026 belzutifan approvals that a 2022 PrimeKG snapshot may not contain. Grade graph-answerable content separately. Verify the 2026 approvals against FDA sources before citing them.
- CPTAC tumors were collected treatment-naive, so drug-caused phenotypes (P2) usually cannot be determined.
- Open check: whether the merged graph contains the paths each gold answer needs. Run the drug-membership check against `kg.csv` (belzutifan first).
