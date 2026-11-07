"""Slide charts for E1-E4 (WS4-05 templates, WS4-08 finals).

    python -m eval.make_charts            # PNGs + status.json in eval/figures/

Inputs (CONTRACTS.md section 8), unchanged:
    eval/results/e1.csv   disease, kind, method, n_targets, recall50, recall500, mrr
    eval/results/e2.csv   model, fold, auroc            (+ optional e2_summary.json for the patient count)
    eval/results/e3.csv   mode, qid, n_claims, n_supported, n_hypothesis, n_removed
    eval/results/e4.json  patient_id, qid, recovered_steps, expected_steps, drugs_surfaced
                          (+ optional step_evidence: {step: [edge IDs / PMIDs]}; see chart_e4)
    eval/e4_answer_key.json   the fixed 5-step answer key

Rules:
  * Missing file   -> deterministic FAKE data in the same shape; chart stamped PLACEHOLDER DATA.
  * Present file   -> validated. Invalid -> no PNG for that chart (any stale one is deleted), actionable
                      error, non-zero exit. Real files are never silently replaced by fake numbers.
  * Fake markers in a present file (fake_ diseases, MOCK patients, ...) -> still stamped PLACEHOLDER.
  * Titles are neutral; the subtitle states what the numbers show, computed from the data.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from common.contracts import ROOT, P  # noqa: E402

# ------------------------------------------------------------------ fixed vocab (explicit, never mutated)
E1_METHODS = {  # contract code -> plain-language label; order = display order (ours last = bottom)
    "llm_only": "LLM alone (no graph)",
    "bfs": "Simple graph search",
    "text_pcst": "Graph search, text only",
    "gnn_pcst": "Graph search + GNN (ours)",
}
E1_REQUIRED = {"bfs", "text_pcst", "gnn_pcst"}  # llm_only comes later from the agent workstream
E1_KINDS = {"gene": "Hidden genes", "go": "Hidden gene-function terms"}
E2_MODELS = {
    "clinical_lr": "Clinical only",
    "prot_pca_lr": "Proteomics only",
    "concat_lr": "Clinical + proteomics",
    "gnn_no_dismech": "Patient-in-graph, no DisMech",
    "gnn": "Patient-in-graph (ours)",
    "gnn_mut": "Patient-in-graph + mutations (ours)",
}
E2_REQUIRED = {"clinical_lr", "prot_pca_lr", "concat_lr", "gnn"}
E2_FOLDS = 5
E3_MODES = {"llm_only": "Same LLM, no tools", "agent": "Our agent"}
OURS = {"gnn_pcst", "gnn", "gnn_mut", "agent"}
FAKE_MARKERS = ("fake_", "MOCK", "FAKE")

# Palette (dataviz reference instance, light): ours = blue, baselines = neutral; composition = 3 validated slots.
BLUE, BASE = "#2a78d6", "#c3c1b8"
SUPPORTED, HYPOTHESIS, UNSUPPORTED = "#2a78d6", "#1baf7a", "#eb6834"
INK, INK2, MUTED, GRID, STAMP = "#0b0b0b", "#52514e", "#6f6d68", "#e1e0d9", "#c4451c"
FOOT = 13  # minimum footnote size (pt) at 16:9 slide size

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 14, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "axes.spines.left": False, "figure.facecolor": "white", "axes.facecolor": "white",
})


class ValidationError(ValueError):
    pass


@dataclass
class Loaded:
    data: object
    fake: bool
    notes: list = field(default_factory=list)


def _need(cond, msg):
    if not cond:
        raise ValidationError(msg)


# ------------------------------------------------------------------ fake data (contract shapes, deterministic)
def fake_e1():
    rng = np.random.default_rng(0)
    mean = {"bfs": 0.18, "text_pcst": 0.26, "llm_only": 0.15, "gnn_pcst": 0.38}
    return pd.DataFrame([{"disease": f"fake_disease_{d}", "kind": k, "method": m, "n_targets": 5,
                          "recall50": float(np.clip(rng.normal(mu - (0.05 if k == "go" else 0), 0.08), 0, 1)),
                          "recall500": np.nan, "mrr": np.nan}
                         for d in range(177) for k in E1_KINDS for m, mu in mean.items()])


def fake_e2():
    rng = np.random.default_rng(1)
    mean = {"clinical_lr": 0.55, "prot_pca_lr": 0.68, "concat_lr": 0.67, "gnn_no_dismech": 0.64, "gnn": 0.66,
            "gnn_mut": 0.69}
    return pd.DataFrame([{"model": m, "fold": f, "auroc": float(np.clip(rng.normal(mu, 0.06), 0, 1))}
                         for m, mu in mean.items() for f in range(E2_FOLDS)])


def fake_e3():
    rows = []
    for mode, (s, h, r) in {"agent": (6, 1, 1), "llm_only": (2, 0, 6)}.items():
        rows += [{"mode": mode, "qid": f"FAKE{i:02d}", "n_claims": s + h + r, "n_supported": s,
                  "n_hypothesis": h, "n_removed": r} for i in range(1, 21)]
    return pd.DataFrame(rows)


def fake_e4(key_steps):
    return {"patient_id": "C3L-MOCK1", "qid": "P01", "expected_steps": key_steps,
            "recovered_steps": key_steps[:2] + key_steps[2:3] + key_steps[4:],
            "step_evidence": {s: ["E900001"] for s in key_steps[:3] + key_steps[4:]},
            "drugs_surfaced": ["Sunitinib", "Axitinib"]}


# ------------------------------------------------------------------ loading + validation
def answer_key_steps(key_path: Path) -> list[str]:
    _need(key_path.exists(), f"{key_path} missing: E4 needs the fixed answer key (eval/e4_answer_key.json)")
    try:
        key = json.loads(key_path.read_text())
        steps = [s["label"] for s in key["core_chain"]] + [s["label"] for s in key["downstream_of_hif2"]]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise ValidationError(f"{key_path}: malformed answer key ({e})") from e
    _need(len(steps) == 5 and len(set(steps)) == 5, f"{key_path}: expected 5 unique steps, got {steps}")
    return steps


def _csv(path: Path, cols: list[str]) -> pd.DataFrame:
    try:
        df = pd.read_csv(path)
    except pd.errors.EmptyDataError as e:
        raise ValidationError(f"{path.name} is empty") from e
    missing = [c for c in cols if c not in df.columns]
    _need(not missing, f"{path.name}: missing columns {missing}")
    _need(len(df) > 0, f"{path.name}: no rows")
    return df


def _has_fake(values) -> bool:
    return any(any(m in str(v) for m in FAKE_MARKERS) for v in values)


def validate_e1(df: pd.DataFrame) -> list[str]:
    unknown = sorted(set(df.method) - set(E1_METHODS))
    _need(not unknown, f"e1.csv: unknown method(s) {unknown}; expected {list(E1_METHODS)}")
    _need(E1_REQUIRED <= set(df.method), f"e1.csv: missing required method(s) {sorted(E1_REQUIRED - set(df.method))}")
    _need(set(df.kind) == set(E1_KINDS), f"e1.csv: kinds must be {list(E1_KINDS)}, got {sorted(set(df.kind))}")
    dup = df.duplicated(["disease", "kind", "method"])
    _need(not dup.any(), f"e1.csv: {int(dup.sum())} duplicate (disease, kind, method) rows")
    r = pd.to_numeric(df.recall50, errors="coerce")
    _need(r.notna().all(), f"e1.csv: {int(r.isna().sum())} missing/non-numeric recall50 values")
    _need(r.between(0, 1).all(), "e1.csv: recall50 outside [0, 1]")
    notes = [f"{m} not in file yet (shown as pending)" for m in E1_METHODS if m not in set(df.method)]
    present = [m for m in E1_METHODS if m in set(df.method)]
    for k in E1_KINDS:
        sets = {m: set(df[(df.kind == k) & (df.method == m)].disease) for m in present}
        common = set.intersection(*sets.values())
        bad = {m: len(s - common) for m, s in sets.items() if s != common}
        _need(not bad, f"e1.csv: unequal disease coverage for kind={k}: extra diseases per method {bad} "
                       f"(common = {len(common)}). Every method must be scored on the same diseases.")
        notes.append(f"{k}: {len(common)} diseases")
    return notes


def validate_e2(df: pd.DataFrame) -> None:
    unknown = sorted(set(df.model) - set(E2_MODELS))
    _need(not unknown, f"e2.csv: unknown model(s) {unknown}; expected some of {list(E2_MODELS)}")
    _need(E2_REQUIRED <= set(df.model), f"e2.csv: missing required model(s) {sorted(E2_REQUIRED - set(df.model))}")
    dup = df.duplicated(["model", "fold"])
    _need(not dup.any(), f"e2.csv: {int(dup.sum())} duplicate (model, fold) rows")
    a = pd.to_numeric(df.auroc, errors="coerce")
    _need(a.notna().all(), f"e2.csv: {int(a.isna().sum())} missing/non-numeric AUROC values")
    _need(a.between(0, 1).all(), "e2.csv: AUROC outside [0, 1]")
    folds = df.groupby("model").fold.apply(lambda s: tuple(sorted(s)))
    _need(folds.nunique() == 1 and len(folds.iloc[0]) == E2_FOLDS,
          f"e2.csv: every model needs the same {E2_FOLDS} folds; got {folds.to_dict()}")


def validate_e3(df: pd.DataFrame) -> None:
    _need(set(df["mode"]) == set(E3_MODES), f"e3.csv: modes must be {list(E3_MODES)}, got {sorted(set(df['mode']))}")
    cols = ["n_claims", "n_supported", "n_hypothesis", "n_removed"]
    num = df[cols].apply(pd.to_numeric, errors="coerce")
    _need(num.notna().all().all(), "e3.csv: missing/non-numeric claim counts")
    _need((num >= 0).all().all() and (num == num.round()).all().all(), "e3.csv: counts must be non-negative integers")
    bad = num.n_supported + num.n_hypothesis + num.n_removed != num.n_claims
    _need(not bad.any(), f"e3.csv: n_supported + n_hypothesis + n_removed != n_claims for "
                         f"{df.loc[bad, ['mode', 'qid']].values.tolist()[:5]}")
    dup = df.duplicated(["mode", "qid"])
    _need(not dup.any(), f"e3.csv: duplicate (mode, qid) rows {df.loc[dup, ['mode', 'qid']].values.tolist()[:5]}")
    q = {m: set(df[df["mode"] == m].qid) for m in E3_MODES}
    _need(q["agent"] == q["llm_only"], f"e3.csv: modes answered different questions; only in agent: "
                                       f"{sorted(q['agent'] - q['llm_only'])}, only in llm_only: {sorted(q['llm_only'] - q['agent'])}")
    _need(num.groupby(df["mode"]).n_claims.sum().gt(0).all(), "e3.csv: a mode has zero claims in total")


def validate_e4(d: dict, key_steps: list[str]) -> list[str]:
    for k in ("patient_id", "qid", "recovered_steps", "expected_steps", "drugs_surfaced"):
        _need(k in d, f"e4.json: missing field {k!r}")
    _need(all(isinstance(d[k], list) for k in ("recovered_steps", "expected_steps", "drugs_surfaced")),
          "e4.json: recovered_steps, expected_steps and drugs_surfaced must be lists")
    _need(set(d["expected_steps"]) == set(key_steps) and len(d["expected_steps"]) == 5,
          f"e4.json: expected_steps must be the 5 answer-key steps {key_steps}, got {d['expected_steps']}")
    rec = d["recovered_steps"]
    _need(len(rec) == len(set(rec)), f"e4.json: duplicate recovered steps {rec}")
    unknown = sorted(set(rec) - set(key_steps))
    _need(not unknown, f"e4.json: recovered steps not in the answer key {unknown}")
    notes = []
    ev = d.get("step_evidence")
    if ev is None:
        notes.append("no step_evidence: recovered steps are shown as UNVERIFIED")
    else:
        _need(isinstance(ev, dict), "e4.json: step_evidence must map step -> [edge IDs / PMIDs]")
    return notes


def load(name: str, results: Path, key_path: Path) -> Loaded:
    """Returns validated data, or raises ValidationError. Missing file -> fake data."""
    if name == "e4":
        steps = answer_key_steps(key_path)
        f = results / "e4.json"
        if not f.exists():
            return Loaded(fake_e4(steps), True)
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError as e:
            raise ValidationError(f"e4.json is not valid JSON ({e})") from e
        _need(isinstance(d, dict), "e4.json must be a JSON object")
        notes = validate_e4(d, steps)
        return Loaded(d, _has_fake([d.get("patient_id")]), notes)
    f = results / f"{name}.csv"
    spec = {"e1": (["disease", "kind", "method", "recall50"], fake_e1, validate_e1, "disease"),
            "e2": (["model", "fold", "auroc"], fake_e2, validate_e2, None),
            "e3": (["mode", "qid", "n_claims", "n_supported", "n_hypothesis", "n_removed"], fake_e3, validate_e3, "qid")}
    cols, fake, check, marker_col = spec[name]
    if not f.exists():
        return Loaded(fake(), True)
    df = _csv(f, cols)
    notes = check(df) or []
    return Loaded(df, bool(marker_col) and _has_fake(df[marker_col]), notes)


# ------------------------------------------------------------------ drawing helpers
def figure(title, subtitle, fake, size=(12, 6.75)):
    fig = plt.figure(figsize=size)
    fig.text(0.04, 0.89, title, fontsize=24, weight="bold", color=INK)
    fig.text(0.04, 0.835, subtitle, fontsize=15, color=INK2)
    if fake:  # stamped twice so it survives cropping: banner + diagonal watermark
        fig.text(0.96, 0.955, "PLACEHOLDER DATA", fontsize=14, weight="bold", color="white", ha="right",
                 bbox={"boxstyle": "round,pad=0.35", "fc": STAMP, "ec": STAMP})
        fig.text(0.5, 0.45, "PLACEHOLDER", fontsize=90, weight="bold", color=STAMP, alpha=0.08, ha="center",
                 va="center", rotation=20, zorder=0)
    return fig


def foot(fig, *texts):
    """Footnotes, wrapped to the slide width and stacked upward from the bottom edge."""
    lines = [l for t in texts for l in _wrap(t, 112)]
    for i, line in enumerate(reversed(lines)):
        fig.text(0.04, 0.025 + i * 0.043, line, fontsize=FOOT, color=MUTED)


def save(fig, out: Path, name: str) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{name}.png"
    fig.savefig(path, dpi=200)
    plt.close(fig)
    return path


def style_ylabels(ax, ours_flags):
    ax.tick_params(axis="y", length=0)
    for t, o in zip(ax.get_yticklabels(), ours_flags):
        t.set_fontweight("bold" if o else "normal")


def whole_percents(shares) -> list[int]:
    """Largest-remainder rounding so the displayed percentages add up to exactly 100."""
    raw = np.asarray(shares, float) * 100
    base = np.floor(raw).astype(int)
    for i in np.argsort(-(raw - base))[: 100 - base.sum()]:
        base[i] += 1
    return base.tolist()


# ------------------------------------------------------------------ metrics (pure, testable)
def e1_means(df):
    return df.groupby(["kind", "method"]).recall50.mean().unstack("method").reindex(columns=list(E1_METHODS))


def e2_stats(df):
    g = df.groupby("model").auroc.agg(["mean", "std", "count"])
    g["std"] = g["std"].fillna(0.0)
    return g.reindex([m for m in E2_MODELS if m in g.index])


def e3_shares(df):
    tot = df.groupby("mode")[["n_claims", "n_supported", "n_hypothesis", "n_removed"]].sum()
    shares = tot[["n_supported", "n_hypothesis", "n_removed"]].div(tot.n_claims, axis=0)
    return tot.reindex(list(E3_MODES)), shares.reindex(list(E3_MODES))


# ------------------------------------------------------------------ charts
def chart_e1(L: Loaded, out: Path):
    df = L.data
    m = e1_means(df)
    m = m.dropna(axis=1, how="all")
    best = {k: m.loc[k].idxmax() for k in E1_KINDS}
    names = {k: E1_METHODS[best[k]].replace(" (ours)", "") for k in E1_KINDS}
    if len(set(names.values())) == 1:
        sub = f"{next(iter(names.values()))} finds the most, for both genes and gene-function terms"
    else:
        sub = "; ".join(f"{E1_KINDS[k].lower()}: {names[k]} finds the most" for k in E1_KINDS)
    fig = figure("Which method finds more hidden disease clues?", f"E1 · {sub}", L.fake)
    xmax = max(0.05, float(np.nanmax(m.values)) * 1.25)
    methods = [x for x in E1_METHODS if x in set(df.method)]
    for i, kind in enumerate(E1_KINDS):
        ax = fig.add_axes([0.27 + i * 0.37, 0.24, 0.29, 0.5])
        y = np.arange(len(methods))[::-1]
        vals = m.loc[kind, methods].values
        ours = [x in OURS for x in methods]
        ax.barh(y, vals, height=0.6, color=[BLUE if o else BASE for o in ours])
        for yi, v, o in zip(y, vals, ours):
            ax.text(v + xmax * 0.02, yi, f"{v:.0%}", va="center", fontsize=15, weight="bold" if o else "normal")
        ax.set_yticks(y, [E1_METHODS[x] for x in methods] if i == 0 else [""] * len(methods))
        style_ylabels(ax, ours)
        ax.set_xlim(0, xmax)  # shared scale across both panels
        ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
        ax.xaxis.grid(True, color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        ax.set_title(E1_KINDS[kind], loc="left", fontsize=16, color=INK)
    n = {k: df[df.kind == k].disease.nunique() for k in E1_KINDS}
    foot(fig, f"Share of each disease's hidden targets found in the top 50 retrieved nodes (Recall@50), averaged over "
              f"{n['gene']} diseases (genes) and {n['go']} (GO terms). Diseases' direct gene and GO links were hidden during "
              "testing. Retrieval performance, not proof of new biology.")
    return save(fig, out, "e1_retrieval")


def chart_e2(L: Loaded, out: Path, n_patients=None):
    g = e2_stats(L.data)
    top = g["mean"].idxmax()
    fig = figure("Can graph-based models predict cancer stage?",
                 f"E2 · Highest mean: {E2_MODELS[top]} ({g.loc[top, 'mean']:.2f}). Overlapping bars = no clear winner.",
                 L.fake)
    ax = fig.add_axes([0.38, 0.26, 0.56, 0.48])
    models = list(g.index)
    y = np.arange(len(models))[::-1]
    ours = [m in OURS for m in models]
    lo = float(min(0.45, (g["mean"] - g["std"]).min() - 0.03))
    hi = float(max(1.0, (g["mean"] + g["std"]).max() + 0.08))
    ax.errorbar(g["mean"], y, xerr=g["std"], fmt="none", ecolor=INK2, capsize=5, lw=1.6)
    ax.scatter(g["mean"], y, s=[180 if o else 120 for o in ours], color=[BLUE if o else MUTED for o in ours],
               marker="o", zorder=3, edgecolor="white", linewidth=2)
    for yi, (mname, row), o in zip(y, g.iterrows(), ours):
        ax.text(min(row["mean"] + row["std"] + 0.01, hi - 0.06), yi + 0.28, f"{row['mean']:.2f}", va="center",
                fontsize=15, weight="bold" if o else "normal")
    ax.axvline(0.5, color=MUTED, ls="--", lw=1)
    ax.text(0.505, y.max() + 0.55, "chance", color=MUTED, fontsize=FOOT)
    ax.set_yticks(y, [E2_MODELS[m] for m in models])
    style_ylabels(ax, ours)
    ax.set_xlim(lo, hi)
    ax.set_ylim(-0.7, len(models) - 0.2)
    ax.xaxis.grid(True, color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel("AUROC: ability to tell stage III-IV from I-II (0.5 = chance, 1 = perfect)")
    pts = f"{n_patients} patients" if n_patients else "patient count not reported"
    foot(fig, f"Dot = mean over {E2_FOLDS} fixed folds; bar = ±1 standard deviation (not a confidence interval). "
              f"{pts}; out-of-fold predictions. Classification performance on a small cohort: not a significance test, "
              "not clinical readiness.")
    return save(fig, out, "e2_patients")


def chart_e3(L: Loaded, out: Path):
    tot, share = e3_shares(L.data)
    agent = share.loc["agent", "n_supported"]
    fig = figure("How much of each answer is evidence-backed?",
                 f"E3 · Supported claims: {agent:.0%} for our agent vs "
                 f"{share.loc['llm_only', 'n_supported']:.0%} for the same LLM without tools", L.fake)
    ax = fig.add_axes([0.22, 0.33, 0.72, 0.4])
    y = np.arange(len(share))
    parts = [("n_supported", "Supported by an edge or PMID", SUPPORTED, None),
             ("n_hypothesis", "Labeled as hypothesis", HYPOTHESIS, "//"),
             ("n_removed", "Unsupported", UNSUPPORTED, "\\\\")]
    left = np.zeros(len(share))
    pct = {m: whole_percents(share.loc[m].values) for m in share.index}
    for j, (col, label, color, hatch) in enumerate(parts):
        v = share[col].values
        ax.barh(y, v, left=left, height=0.55, color=color, edgecolor="white", linewidth=2, hatch=hatch, label=label)
        for yi, (mode, l, w) in enumerate(zip(share.index, left, v)):
            if w >= 0.07:
                ax.text(l + w / 2, yi, f"{pct[mode][j]}%", ha="center", va="center", color="white", fontsize=16,
                        weight="bold", bbox={"boxstyle": "round,pad=0.15", "fc": color, "ec": "none"})
        left += v
    ax.set_yticks(y, [f"{E3_MODES[m]}\n({int(tot.loc[m, 'n_claims'])} claims)" for m in share.index])
    style_ylabels(ax, [m in OURS for m in share.index])
    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.1), ncol=3, frameon=False, fontsize=13)
    n_q = L.data.qid.nunique()
    foot(fig, f"All factual claims before verification, {n_q} questions per mode; denominator = n_claims. The agent's "
              "unsupported claims are removed by its verifier; the baseline's are found by the same audit. "
              "Supported = cites evidence the tools returned, not proof the biology is correct.")
    return save(fig, out, "e3_grounding")


def chart_e4(L: Loaded, out: Path, key_steps: list[str]):
    d = L.data
    ev = d.get("step_evidence")
    verified = {s for s in d["recovered_steps"] if ev and ev.get(s)}
    claimed_only = set(d["recovered_steps"]) - verified
    fig = figure("Can the agent reconstruct a hidden cancer pathway?",
                 f"E4 · DisMech's ccRCC chain hidden from the graph; agent explains patient {d['patient_id']}", L.fake)
    ax = fig.add_axes([0.03, 0.27, 0.94, 0.52])
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    core, down = key_steps[:2], key_steps[2:]

    def box(x, y, text, state):
        fc = {"ok": BLUE, "claimed": "white", "miss": "white"}[state]
        ec = {"ok": BLUE, "claimed": BLUE, "miss": MUTED}[state]
        ls = {"ok": "-", "claimed": "-", "miss": "--"}[state]
        ax.add_patch(matplotlib.patches.FancyBboxPatch((x - 0.13, y - 0.12), 0.26, 0.24,
                                                       boxstyle="round,pad=0.01,rounding_size=0.02",
                                                       fc=fc, ec=ec, lw=2.2, ls=ls))
        tc = "white" if state == "ok" else INK2
        ax.text(x, y + 0.03, "\n".join(_wrap(text, 24)), ha="center", va="center", fontsize=14, color=tc,
                weight="bold" if state == "ok" else "normal")
        status = {"ok": "✓ recovered, cited", "claimed": "? named, not citation-checked", "miss": "✗ not recovered"}[state]
        ax.text(x, y - 0.085, status, ha="center", va="center", fontsize=FOOT, color=tc)

    pos = {core[0]: (0.15, 0.5), core[1]: (0.45, 0.5)}
    pos.update({s: (0.82, yy) for s, yy in zip(down, np.linspace(0.86, 0.14, len(down)))})
    arrow = {"arrowstyle": "->", "color": INK2, "lw": 1.8}
    ax.annotate("", xy=(0.32, 0.5), xytext=(0.28, 0.5), arrowprops=arrow)
    for s in down:
        ax.annotate("", xy=(0.69, pos[s][1]), xytext=(0.58, 0.5), arrowprops=arrow)
    for s, (x, y) in pos.items():
        box(x, y, s, "ok" if s in verified else ("claimed" if s in claimed_only else "miss"))
    drugs = ", ".join(d["drugs_surfaced"]) or "none"
    head = f"Recovered with citations: {len(verified)} of 5 steps"
    if claimed_only:
        head += f"  (+{len(claimed_only)} named without checked evidence)"
    fig.text(0.04, 0.2, head, fontsize=16, color=INK, weight="bold")
    fig.text(0.04, 0.165, "\n".join(_wrap(f"Drugs mentioned: {drugs}", 120)[:2]), fontsize=FOOT, color=INK2, va="top")
    foot(fig, "Reconstruction under an ablation, not discovery of new biology: the LLM may already know this pathway. "
              "A drug mention is not a treatment recommendation.")
    return save(fig, out, "e4_case_study")


def _wrap(s, n):
    lines, cur = [], ""
    for w in s.split():
        if len(cur) + len(w) + 1 > n and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    return lines + [cur]


# ------------------------------------------------------------------ driver
def run(results: Path = P.results, out: Path = ROOT / "eval" / "figures",
        key_path: Path = ROOT / "eval" / "e4_answer_key.json") -> dict:
    status = {}
    names = {"e1": "e1_retrieval", "e2": "e2_patients", "e3": "e3_grounding", "e4": "e4_case_study"}
    for name, png in names.items():
        try:
            L = load(name, results, key_path)
            if name == "e1":
                chart_e1(L, out)
            elif name == "e2":
                summ = results / "e2_summary.json"
                n = None
                if summ.exists() and not L.fake:
                    try:
                        n = json.loads(summ.read_text()).get("y_stage", {}).get("n")
                    except json.JSONDecodeError:
                        pass
                chart_e2(L, out, n)
            elif name == "e3":
                chart_e3(L, out)
            else:
                chart_e4(L, out, answer_key_steps(key_path))
            status[name] = {"status": "PLACEHOLDER" if L.fake else "REAL", "notes": L.notes}
        except ValidationError as e:
            stale = out / f"{png}.png"
            if stale.exists():
                stale.unlink()
            status[name] = {"status": "ERROR", "notes": [str(e)]}
    out.mkdir(parents=True, exist_ok=True)
    (out / "status.json").write_text(json.dumps(status, indent=1))
    return status


if __name__ == "__main__":
    st = run()
    print("preflight:")
    for k, v in st.items():
        print(f"  {k}: {v['status']:<11} {'; '.join(v['notes'])}")
    sys.exit(1 if any(v["status"] == "ERROR" for v in st.values()) else 0)
