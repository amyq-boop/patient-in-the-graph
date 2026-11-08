"""Run the real GNN-B + verifier once per measured marker and save the traces for replay.

    python -m app.tour.precompute [--llm] [--budget 50]

Run this where the graph assets live (Brev, or a laptop with the data kit in data/processed). It writes
cache/demo/youngcrc/<finding>__gnn__<utc>.json; commit those so a demo laptop without the 8.5M-edge graph can
replay them. The tour always labels a replay with its timestamp and never presents it as a live run.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.external_reasoner import DEFAULT_QUESTION  # noqa: E402
from app.tour import case_data, investigate  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--llm", action="store_true", help="draft with NVIDIA NIM (needs NVIDIA_API_KEY in .env)")
ap.add_argument("--budget", type=int, default=50)
ap.add_argument("--no-inject", action="store_true", help="do not plant the demo false claim")
a = ap.parse_args()

caps = investigate.capabilities()
if not caps["gnn_ready"]:
    sys.exit("GNN-B is not available here. Missing: " + ", ".join(caps["missing_files"] + caps["missing_packages"]))
case = case_data.get_case()
print(f"Alexander Young release {case['release']} ({case['data_source']})")
for m in investigate.markers(case):
    r = investigate.run(case, m["finding_id"], DEFAULT_QUESTION, a.llm, not a.no_inject, a.budget)
    if r.get("ok"):
        t = r["trace"]
        print(f"  {m['symbol']:<16} {t['n_graph_edges']:>3} edges  {t['verifier']['n_supported']} supported, "
              f"{t['verifier']['n_removed']} removed  -> cache/demo/youngcrc/{t['saved_as']}")
    else:
        print(f"  {m['symbol']:<16} failed: {r.get('error') or r.get('reason')}")
