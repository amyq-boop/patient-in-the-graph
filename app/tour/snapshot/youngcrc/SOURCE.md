# Alexander Young public case: verified offline snapshot

Used by the guided tour only when https://youngcrc.com/ cannot be reached (venue Wi-Fi, firewalled hosts).
The tour always tries the live site first.

- Publisher: Alexander Young, *Open Cancer Data*, https://youngcrc.com/
- Release: **1.53** (`manifest.json` is the site's `release/website-manifest.json`)
- Copied from https://github.com/AlexTISYoung/youngcrc at commit `35fcae2013ffc80bfc8ed09139dd6c0a0b594441` (2026-11-02)
- Every file here is re-checked at load time against the byte count and SHA-256 in `manifest.json`;
  a mismatch stops the load (same rule as `app/external_case.py`).
- License: curated factual datasets and explanatory text are CC BY 4.0. Third-party reports keep their own rights;
  none are mirrored here. Cite the release number, dataset paths and hashes when reporting a finding.

Files: the seven tables `app/external_case.py` reads, plus `data/clinical/sources.json` (timeline source labels),
`data/molecular/variants.json` (30 research-audit loci) and
`data/proteomics/b7_43_2026-10-29/analysis/reference_rank_comparisons.tsv` (per-gene rank comparisons).
