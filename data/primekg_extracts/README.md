# PrimeKG extracts (for eyeballing / knowledge checks)

The full PrimeKG `kg.csv` is 982 MB, over GitHub's 100 MB file limit, so it is **not** in this repo.
`scripts/download_data.sh` downloads the exact same file (Harvard Dataverse file 6180620);
`scripts/verify_primekg.sh` checks it is byte-identical to the copy used to build the graph
(sha256 in `kg.csv.sha256`).

| File | Rows | What |
|---|---|---|
| `primekg_ccrcc_direct_edges.csv` | 165 | Every PrimeKG row with clear cell renal carcinoma (node_index 36937, MONDO:0005005) as `x`: 149 genes, 11 drugs, 5 diseases. **No phenotype edges for ccRCC in PrimeKG.** |
| `primekg_ccrcc_neighbour_edges.csv` | 432 | Edges *among* those 165 neighbours (each pair once; hub relations `anatomy_protein_*` and `drug_drug` excluded) |
| `primekg_sample_1pct.csv` | 81,004 | Random 1% of all 8,100,498 rows (`random_state=0`); same columns as `kg.csv` |

Columns (as in `kg.csv`): `relation, display_relation, x_index, x_id, x_type, x_name, x_source, y_index, y_id, y_type, y_name, y_source`.
Every edge appears twice in `kg.csv` (x→y and y→x), so 8.1M rows = 4.05M undirected edges.
IDs are bare (MONDO `5005`, GO/HPO without prefix/zero-padding; grouped diseases look like `13924_12592`);
`graph/build_kg.py` turns them into CURIEs.

Source: PrimeKG, Chandak, Huang & Zitnik, *Building a knowledge graph to enable precision medicine*, Scientific Data 2023
(https://github.com/mims-harvard/PrimeKG). See the PrimeKG repository / Dataverse page for license and citation terms.
