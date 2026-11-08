"""E4 cohort slide chart: share of patients whose answer recovered each ccRCC step, hidden vs visible mechanism.

    python -m eval.make_e4_cohort_chart        # -> eval/figures/e4_cohort.png

Inputs: eval/results/e4_cohort_rv.json (DisMech's ccRCC chain hidden, the pre-registered cohort) and
eval/results/e4_cohort_full_rv.json (full graph, the ceiling), both from eval/e4_cohort.py. Uses the strict column
(step_recovery_kg: the supported claim cites a graph edge or PMID). Same look as eval/make_charts.py, whose
helpers it imports; make_charts.py itself is unchanged.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

from common.contracts import ROOT, P
from eval import make_charts as M

HIDDEN, FULL = P.results / "e4_cohort_rv.json", P.results / "e4_cohort_full_rv.json"
LABELS = {  # answer-key label -> slide label (chain order, top to bottom)
    "VHL Tumor Suppressor Inactivation": "VHL inactivation",
    "HIF-2alpha Stabilization": "HIF-2α stabilization",
    "VEGF-Driven Angiogenesis": "VEGF-driven angiogenesis",
    "Growth Factor Pathway Activation": "Growth-factor signaling",
    "Metabolic Reprogramming and Aerobic Glycolysis": "Metabolic reprogramming",
}
TRADEOFF = ("Why the gap: retrieval down-weights genes linked to almost everything, HIF among them, to keep evidence "
            "patient-specific. Relaxing that brings a HIF gene in for 14 of 20 patients, at a small cost in grounding.")


def load(path: Path) -> dict:
    M._need(path.exists(), f"{path} missing: run python -m eval.e4_cohort --reverify (and --variant full)")
    d = json.loads(path.read_text(encoding="utf-8"))
    o = d.get("overall") or {}
    M._need(set(LABELS) <= set(o.get("step_recovery_kg", {})), f"{path}: step_recovery_kg lacks the 5 answer-key steps")
    M._need(d.get("n_valid", 0) > 0, f"{path}: no valid traces")
    return {"steps": [float(o["step_recovery_kg"][k]) for k in LABELS], "drugs": float(o["mean_drugs_surfaced"]),
            "n": int(d["n_valid"]), "n_traces": int(d["n_traces"]), "leaky": int(d.get("n_leaky", 0))}


def chart(hidden: dict, full: dict, out: Path) -> Path:
    sub = (f"E4 \u00b7 Mechanism in the graph: each step cited for {min(full['steps']):.0%}\u2013"
           f"{max(full['steps']):.0%} of patients. Hidden: at most {max(hidden['steps']):.0%}.")
    fig = M.figure("Knows the pathway, states only what it can cite", sub, False)
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
    M.foot(fig, "The same LLM without tools names VHL, HIF and VEGF in 15\u201316 of its 20 E3 answers; with the mechanism "
                "hidden, the agent stated no missing step as fact.", TRADEOFF,
           f"{hidden['n']} patients per condition, chosen by a fixed rule before any run; {hidden['leaky']} leaks. A step "
           "counts when a verified claim names it and cites a retrieved graph edge or PMID.")
    return M.save(fig, out, "e4_cohort")


def main(argv=None) -> int:
    try:
        path = chart(load(HIDDEN), load(FULL), ROOT / "eval" / "figures")
    except M.ValidationError as e:
        print(f"e4_cohort chart: {e}", file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
