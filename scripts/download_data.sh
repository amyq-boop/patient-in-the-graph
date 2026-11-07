#!/bin/bash
mkdir -p data/raw data/processed cache
wget -O data/raw/kg.csv https://dataverse.harvard.edu/api/access/datafile/6180620
wget -P data/raw https://github.com/monarch-initiative/dismech/releases/latest/download/dismech_nodes.tsv
wget -P data/raw https://github.com/monarch-initiative/dismech/releases/latest/download/dismech_edges.tsv
python -c "import cptac; cc = cptac.Ccrcc(); cc.get_clinical('mssm'); cc.get_proteomics('umich')"
