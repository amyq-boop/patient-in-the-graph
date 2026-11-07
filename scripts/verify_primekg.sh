#!/usr/bin/env bash
# Check that data/raw/kg.csv is byte-identical to the PrimeKG copy used to build the team graph.
set -euo pipefail
f=${1:-data/raw/kg.csv}
expected=$(cut -d' ' -f1 data/primekg_extracts/kg.csv.sha256)
actual=$( (sha256sum "$f" 2>/dev/null || shasum -a 256 "$f") | cut -d' ' -f1)
if [ "$actual" = "$expected" ]; then echo "OK: $f matches the team's PrimeKG (sha256 $expected)"; else echo "MISMATCH: got $actual, expected $expected"; exit 1; fi
