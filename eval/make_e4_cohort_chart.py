"""E4 cohort slide chart: share of patients whose answer recovered each ccRCC step, hidden vs visible mechanism.

    python -m eval.make_e4_cohort_chart        # -> eval/figures/e4_cohort.png

Inputs: eval/results/e4_cohort_rv.json (DisMech's ccRCC chain hidden, the pre-registered cohort) and
eval/results/e4_cohort_full_rv.json (full graph, the ceiling), both from eval/e4_cohort.py. Uses the strict column
(step_recovery_kg: the supported claim cites a graph edge or PMID). Every sentence with a number is computed from
files: the no-tools LLM's E3 drafts (eval/traces/e3_llm_only) and the retrieval diagnostic without the hub penalty
(eval/results/e4_retrieval_diag_hub0.json). Same look as eval/make_charts.py, whose helpers it imports;
make_charts.py itself is unchanged.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

from common.contracts import ROOT, P
from eval import make_charts as M

HIDDEN, FULL = P.results / "e4_cohort_rv.json", P.results / "e4_cohort_full_rv.json"
DIAG_HUB0 = P.results / "e4_retrieval_diag_hub0.json"   # retrieval re-run without the hub penalty
LLM_ONLY = P.traces / "e3_llm_only"                      # the same LLM with no tools (E3 baseline drafts)
HIF2 = "HIF-2alpha Stabilization"
LABELS = {  # answer-key label -> slide label (chain order, top to bottom)
    "VHL Tumor Suppressor Inactivation": "VHL inactivation",
    HIF2: "HIF-2α stabilization",
    "VEGF-Driven Angiogenesis": "VEGF-driven angiogenesis",
    "Growth Factor Pathway Activation": "Growth-factor signaling",
    "Metabolic Reprogramming and Aerobic Glycolysis": "Metabolic reprogramming",
}
MEMORY_TERMS = {"VHL": r"\bVHL\b", "HIF": r"\bHIF|EPAS1", "VEGF": r"VEGF"}


def load(path: Path) -> dict:
    M._need(path.exists(), f"{path} missing: run python -m eval.e4_cohort --reverify (and --variant full)")
    d = json.loads(path.read_text(encoding="utf-8"))
    o = d.get("overall") or {}
    M._need(set(LABELS) <= set(o.get("step_recovery_kg", {})) and set(LABELS) <= set(o.get("step_recovery", {})),
            f"{path}: step_recovery / step_recovery_kg lack the 5 answer-key steps")
    M._need(d.get("n_valid", 0) > 0, f"{path}: no valid traces")
    n = int(d["n_valid"])
    return {"steps": [float(o["step_recovery_kg"][k]) for k in LABELS], "drugs": float(o["mean_drugs_surfaced"]),
            "n": n, "n_traces": int(d["n_traces"]), "leaky": int(d.get("n_leaky", 0)),
            # answers stating the HIF-2a step in a supported claim with no graph edge or PMID among its citations
            "hif2_uncited": round((float(o["step_recovery"][HIF2]) - float(o["step_recovery_kg"][HIF2])) * n)}


def llm_only_mentions(folder: Path = LLM_ONLY) -> tuple[int, int, int]:
    """(lowest, highest, n): how many no-tools E3 drafts name VHL, HIF and VEGF, each counted separately."""
    files = sorted(f for f in Path(folder).glob("*.json") if not f.name.startswith("_"))
    M._need(bool(files), f"{folder}: no LLM-only E3 traces")
    texts = [json.loads(f.read_text(encoding="utf-8")).get("draft") or "" for f in files]
    counts = [sum(bool(re.search(p, t, re.I)) for t in texts) for p in MEMORY_TERMS.values()]
    return min(counts), max(counts), len(texts)


def hif_genes_without_penalty(path: Path = DIAG_HUB0) -> tuple[int, int, list[str]]:
    """(patients whose re-run agent subgraph holds a HIF-family gene, patients, which genes), at hub penalty 0."""
    M._need(path.exists(), f"{path} missing: run python -m eval.e4_retrieval_diag --hub 0")
    d = json.loads(path.read_text(encoding="utf-8"))
    rows = [v["arms"].get("agent_rerun", {}) for v in d["per_patient"].values()]
    genes = sorted({g for r in rows for g in r.get("2_hif", [])})
    return sum(bool(r.get("2_hif")) for r in rows), len(rows), genes


def chart(hidden: dict, full: dict, out: Path, notes: dict) -> Path:
    sub = (f"E4 · Mechanism in the graph: each step cited for {min(full['steps']):.0%}–"
           f"{max(full['steps']):.0%} of patients. Hidden: at most {max(hidden['steps']):.0%}.")
    fig = M.figure("Answers follow the graph’s evidence, not the model’s memory", sub, False)
    ax = fig.add_axes([0.25, 0.5, 0.69, 0.28])
    y = np.arange(len(LABELS))[::-1].astype(float)
    h = 0.36
    # as in make_charts.chart_e1: the emphasised series in violet (bar and value), the reference in lilac / grey ink
    series = [(full, "Mechanism visible (full graph)", M.BASE, M.INK2, +h / 2 + 0.02),
              (hidden, "Mechanism hidden (the test)", M.BLUE, M.BLUE, -h / 2 - 0.02)]
    for d, label, color, tcolor, dy in series:
        ax.barh(y + dy, d["steps"], height=h, color=color, label=label)
        for yi, v in zip(y + dy, d["steps"]):
            ax.text(v + 0.012, yi, f"{v:.0%}", va="center", fontsize=17, family=M.SERIF, color=tcolor)
    ax.set_yticks(y, list(LABELS.values()))
    ax.tick_params(axis="y", length=0, labelsize=15)
    ax.set_xlim(0, 1.1)
    ax.set_xticks([0, 0.25, 0.5, 0.75, 1.0])
    ax.xaxis.set_major_formatter(M.matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    ax.xaxis.grid(True, color=M.GRID, lw=0.8)
    ax.set_axisbelow(True)
    fig.legend(loc="center", bbox_to_anchor=(0.595, 0.41), ncol=2, frameon=False, fontsize=13)
    fig.text(0.04, 0.335, f"Standard ccRCC drugs named (of sunitinib, pazopanib, axitinib, cabozantinib): "
                          f"{full['drugs']:.1f} visible, {hidden['drugs']:.1f} hidden",
             fontsize=15, color=M.INK, family=M.SERIF)
    lo, hi, n_llm = notes["llm_only"]
    n_hif, n_pat, genes = notes["hub0"]
    gene_txt = "/".join(genes) + (" (not HIF-2α)" if genes and "EPAS1" not in genes else "")
    k = hidden["hif2_uncited"]
    M.foot(fig,
           f"Without tools the same LLM names VHL, HIF and VEGF in {lo}–{hi} of its {n_llm} E3 answers. With the "
           f"mechanism hidden, {k} of {hidden['n']} answers still state{'s' if k == 1 else ''} "
           "HIF stabilization without citing a graph edge or PMID.",
           "Why the gap: retrieval down-weights genes linked to almost everything, to keep evidence patient-specific; "
           f"relaxing that brings {gene_txt} in for {n_hif} of {n_pat} patients.",
           "A step counts when a verified claim names it and cites a retrieved edge or PMID; hidden-mechanism counts can "
           f"include coincidental keyword matches. {hidden['n']} patients per condition, fixed before any run; "
           f"{hidden['leaky']} leaks.")
    return M.save(fig, out, "e4_cohort")


def main(argv=None) -> int:
    try:
        notes = {"llm_only": llm_only_mentions(), "hub0": hif_genes_without_penalty()}
        path = chart(load(HIDDEN), load(FULL), ROOT / "eval" / "figures", notes)
    except M.ValidationError as e:
        print(f"e4_cohort chart: {e}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
