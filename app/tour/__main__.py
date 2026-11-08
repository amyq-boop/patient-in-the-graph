"""python -m app.tour [--port 8600] [--host 127.0.0.1] [--no-live] [--open]"""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.tour.server import serve  # noqa: E402

ap = argparse.ArgumentParser(description="Patient-in-the-Graph guided tour")
ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 to share on the local network (e.g. a Brev port)")
ap.add_argument("--port", type=int, default=8600)
ap.add_argument("--no-live", action="store_true", help="skip youngcrc.com; use the hash-verified snapshot")
ap.add_argument("--open", action="store_true", help="open the browser when ready")
ap.add_argument("--graph", choices=["auto", "executed"], default="auto",
                help="auto: use data/processed when present; executed: only the fast index of executed retrievals")
a = ap.parse_args()
import os  # noqa: E402
os.environ["TOUR_GRAPH"] = a.graph
print("Patient-in-the-Graph tour: loading data (with the full graph this takes about 20 to 60 s) ...", flush=True)
if a.open:
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{a.port}")).start()
serve(a.host, a.port, prefer_live=not a.no_live)
