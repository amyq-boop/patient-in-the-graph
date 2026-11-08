"""Standard-library HTTP server for the guided tour. No new dependencies.

    python -m app.tour [--port 8600] [--host 127.0.0.1] [--no-live]

GET  /                      the tour (static/index.html)
GET  /api/bootstrap         stage graph, model evidence, patient, mapping, capabilities (one payload)
GET  /api/neighbours?curie= real edges for one entity from the active graph index
GET  /api/traces            saved Alexander Young traces (cache/demo/youngcrc)
GET  /api/trace?name=       one saved trace (real GNN runs only, or source-only runs)
POST /api/investigate       {finding_id, question, use_llm, inject, budget} -> real GNN-B + verifier, or a refusal
POST /api/source_check      {finding_id, inject} -> real verifier on the source observation only
POST /api/review            {name} or {trace} -> markdown case review
"""
from __future__ import annotations

import json
import mimetypes
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.tour import case_data, investigate, results
from app.tour.graph_index import get_index

STATIC = Path(__file__).resolve().parent / "static"
_boot_lock = threading.Lock()
_boot: dict | None = None
_run_lock = threading.Lock()  # one live GNN run at a time (the retriever holds large tensors)

CLINICAL_GENES = {"KRAS/NRAS/BRAF clinical status": ["KRAS", "NRAS", "BRAF"],
                  "ERBB2 amplification": ["ERBB2"], "HLA-A": ["HLA-A"], "HLA-B": ["HLA-B"], "HLA-C": ["HLA-C"],
                  "HLA-A loss of heterozygosity": ["HLA-A"]}


def build_mapping(case: dict, patient: dict, index) -> list[dict]:
    """Every patient measurement that names a gene, resolved to a graph CURIE (or marked unresolved)."""
    from app.external_reasoner import MARKERS

    items = []

    def add(kind, mid, label, value, symbol, curie, specimen, sources, note, evidence_class):
        items.append({"kind": kind, "measurement_id": mid, "label": label, "value": value, "symbol": symbol,
                      "curie": curie, "specimen": specimen, "sources": sources, "note": note,
                      "evidence_class": evidence_class})

    spec = {s["sample_id"]: s for s in patient["specimens"]}
    for f in patient["findings"]:
        sp = spec.get(f["sample_id"])
        if f["feature"] in MARKERS:
            sym, curie = MARKERS[f["feature"]]
            add("ihc", f["finding_id"], f["feature"], f"{f['value']} {f['unit'] or ''}".strip(), sym, curie, sp,
                f["sources"], "Immunohistochemistry on the archived 2024 liver-metastasis specimen.", f["evidence_class"])
        for g in CLINICAL_GENES.get(f["feature"], []):
            add("clinical_report", f["finding_id"], f["feature"], str(f["value"]), g, index.resolve(g), sp,
                f["sources"], "Clinical molecular report extraction.", f["evidence_class"])
    for v in patient["variants"]:
        if v["symbol"]:
            add("variant", v["variant_id"], f"{v['gene_label']} {v['protein_annotation'] or ''}".strip(),
                v["protein_annotation"] or "locus listed", v["symbol"], index.resolve(v["symbol"]), None, v["sources"],
                "Research-audit locus; the release attaches no specimen and makes no clinical call.", v["evidence_class"])
    prot = patient["proteomics"]
    ref = prot["primary_reference"]
    seen = set()
    for p in prot["panels"]:
        if p["reference"] != ref:
            continue
        for g in p["markers"]:
            if g in seen:
                continue
            seen.add(g)
            add("proteomics", f"panel:{p['panel']}:{g}", f"{g} ({p['panel'].replace('_', ' ')} panel)", None, g,
                index.resolve(g), spec.get(prot["sample_id"]), [], "Exploratory rank vs reference cohort.",
                "independent_reanalysis")
    ranks = case_data.gene_ranks(case, {i["symbol"] for i in items})
    for i in items:
        i["ranks"] = ranks.get(i["symbol"])
        i["in_graph"] = bool(i["curie"]) and bool(index.neighbours(i["curie"], 1) or index.node(i["curie"]))
    return items


def bootstrap(refresh: bool = False) -> dict:
    global _boot
    with _boot_lock:
        if _boot is not None and not refresh:
            return _boot
        t0 = time.time()
        case = case_data.get_case(refresh)
        patient = case_data.patient_payload(case)
        index = get_index()
        mapping = build_mapping(case, patient, index)
        curies = {m["curie"] for m in mapping if m["curie"]}
        index.extract_rows_for(curies)
        for m in mapping:
            m["n_graph_edges"] = len(index.neighbours(m["curie"], 200)) if m["curie"] else 0
            m["in_graph"] = m["n_graph_edges"] > 0 or bool(m["curie"] and index.node(m["curie"]))
        focus = [m["curie"] for k in ("ihc", "variant", "clinical_report", "proteomics")
                 for m in mapping if m["kind"] == k and m["curie"]]
        stage = index.stage_graph(list(dict.fromkeys(focus)))
        _boot = {
            "graph_index": index.describe(),
            "stage": stage,
            "mechanisms": index.mechanism_chains,
            "results": results.bundle(),
            "patient": patient,
            "mapping": mapping,
            "markers": investigate.markers(case),
            "top_rank_shifts": case_data.top_rank_shifts(case),
            "capabilities": investigate.capabilities(),
            "saved_traces": investigate.saved(),
            "built_s": round(time.time() - t0, 2),
        }
        return _boot


class Handler(BaseHTTPRequestHandler):
    server_version = "PatientInTheGraphTour/1.0"

    def log_message(self, fmt, *args):  # quieter console: one line per API call
        if self.path.startswith("/api/"):
            super().log_message(fmt, *args)

    def _json(self, obj, code=200):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 1_000_000:
            raise ValueError("request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/api/bootstrap":
                return self._json(bootstrap(q.get("refresh") == "1"))
            if u.path == "/api/neighbours":
                idx = get_index()
                edges = idx.neighbours(q.get("curie", ""), min(int(q.get("limit", 30)), 80))
                nodes = {}
                for e in edges:
                    for c in (e["src"], e["dst"]):
                        nodes[c] = idx.node(c) or e.get("_dst_node") or {"curie": c, "name": c, "type": "unknown"}
                return self._json({"edges": [{k: v for k, v in e.items() if not k.startswith("_")} for e in edges],
                                   "nodes": list(nodes.values()), "index": idx.describe().get("source")})
            if u.path == "/api/traces":
                return self._json(investigate.saved())
            if u.path == "/api/trace":
                r = investigate.load_saved(q.get("name", ""))
                return self._json({**r, **(investigate.view(r["trace"]) if r.get("ok") else {})})
            return self._static(u.path)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        try:
            body = self._body()
            case = case_data.get_case()
            if u.path == "/api/investigate":
                if not _run_lock.acquire(blocking=False):
                    return self._json({"ok": False, "reason": "busy", "error": "A run is already in progress."}, 409)
                try:
                    r = investigate.run(case, str(body.get("finding_id", "")), str(body.get("question") or ""),
                                        bool(body.get("use_llm")), bool(body.get("inject", True)),
                                        int(body.get("budget", 50)))
                finally:
                    _run_lock.release()
                if r.get("ok"):
                    r.update(investigate.view(r["trace"]))
                    with _boot_lock:
                        if _boot is not None:
                            _boot["saved_traces"] = investigate.saved()
                return self._json(r)
            if u.path == "/api/source_check":
                r = investigate.source_check(case, str(body.get("finding_id", "")), bool(body.get("inject", True)))
                if r.get("ok"):
                    r.update(investigate.view(r["trace"]))
                return self._json(r)
            if u.path == "/api/review":
                if body.get("name"):
                    r = investigate.load_saved(str(body["name"]))
                    if not r.get("ok"):
                        return self._json(r, 404)
                    trace = {**r["trace"], "saved_as": r["saved_as"]}
                else:
                    trace = body.get("trace") or {}
                    if trace.get("case_id") != "youngcrc":
                        return self._json({"ok": False, "error": "not a youngcrc trace"}, 400)
                    if not trace.get("tool_calls") and trace.get("graph_backend") == "gnn_pcst":
                        return self._json({"ok": False, "error": "GNN trace without tool calls"}, 400)
                md = investigate.review_markdown(trace, case_data.patient_payload(case))
                return self._json({"ok": True, "markdown": md})
            return self._json({"ok": False, "error": "unknown endpoint"}, 404)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            return self._json({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, 500)

    def _static(self, path: str):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        f = (STATIC / rel).resolve()
        if STATIC.resolve() not in f.parents and f != STATIC.resolve() or not f.is_file():
            self.send_error(404)
            return
        data = f.read_bytes()
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or
                                                  ctype.endswith("javascript") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def serve(host="127.0.0.1", port=8600, prefer_live=True, warm=True):
    if not prefer_live:
        case_data._cached = case_data.load(prefer_live=False)
    if warm:
        b = bootstrap()
        print(f"  patient data: release {b['patient']['release']} ({b['patient']['data_source']})")
        print(f"  graph index:  {b['graph_index']['source']}, {b['graph_index'].get('n_edges')} edges")
        c = b["capabilities"]
        print(f"  live GNN-B:   {'ready' if c['gnn_ready'] else 'not available (missing graph assets or packages)'}")
        print(f"  NVIDIA LLM:   {'ready' if c['llm_ready'] else 'no NVIDIA_API_KEY'}")
        try:
            import resource
            print(f"  memory:       {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024} MB peak so far", flush=True)
        except Exception:  # noqa: BLE001 - not available on Windows
            pass
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"\n  Patient-in-the-Graph tour: http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
