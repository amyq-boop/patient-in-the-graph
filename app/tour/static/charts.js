/* Small SVG charts. Values come straight from the bootstrap payload; nothing is smoothed or rounded upstream. */
(function () {
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const C = {};

  // Horizontal bars with optional +-err whiskers. rows: {label, value, err, ours}
  C.bars = function (rows, { max = null, fmt = (v) => v.toFixed(3), w = 380, row = 30, labelW = 150 } = {}) {
    const m = max ?? Math.max(...rows.map((r) => r.value + (r.err || 0))) * 1.08;
    const h = rows.length * row + 6, bw = w - labelW - 54;
    let s = `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img">`;
    rows.forEach((r, i) => {
      const y = i * row + 4, x = labelW, L = (r.value / m) * bw;
      s += `<text class="lab ${r.ours ? "ours" : ""}" x="0" y="${y + 14}">${esc(r.label)}</text>`;
      s += `<rect x="${x}" y="${y + 5}" width="${bw}" height="12" rx="3" fill="rgba(236,232,248,.06)"/>`;
      s += `<rect x="${x}" y="${y + 5}" width="${Math.max(1, L)}" height="12" rx="3" fill="${r.ours ? "#8b7cf6" : "#6c6789"}"/>`;
      if (r.err) {
        const a = x + ((r.value - r.err) / m) * bw, b = x + ((r.value + r.err) / m) * bw;
        s += `<line x1="${a}" x2="${b}" y1="${y + 11}" y2="${y + 11}" stroke="#f4efe6" stroke-width="1.2"/>`;
      }
      s += `<text class="val" x="${x + bw + 8}" y="${y + 15}">${esc(fmt(r.value))}</text>`;
    });
    return s + "</svg>";
  };

  // Dots with 95% CI. rows: {label, value, lo, hi, ours}
  C.ci = function (rows, { min = 0.35, max = 0.85, ref = 0.5, w = 380, row = 28, labelW = 160 } = {}) {
    const h = rows.length * row + 26, bw = w - labelW - 46;
    const X = (v) => labelW + ((v - min) / (max - min)) * bw;
    let s = `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img">`;
    s += `<line x1="${X(ref)}" x2="${X(ref)}" y1="0" y2="${h - 20}" stroke="rgba(236,232,248,.25)" stroke-dasharray="3 3"/>`;
    s += `<text x="${X(ref) - 8}" y="${h - 6}">${ref}</text><text x="${X(max) - 14}" y="${h - 6}">${max}</text><text x="${X(min)}" y="${h - 6}">${min}</text>`;
    rows.forEach((r, i) => {
      const y = i * row + 12;
      s += `<text class="lab ${r.ours ? "ours" : ""}" x="0" y="${y + 4}">${esc(r.label)}</text>`;
      s += `<line x1="${X(r.lo)}" x2="${X(r.hi)}" y1="${y}" y2="${y}" stroke="${r.ours ? "#b3a8ff" : "#8a84a8"}" stroke-width="2"/>`;
      s += `<circle cx="${X(r.value)}" cy="${y}" r="4.5" fill="${r.ours ? "#8b7cf6" : "#b6b0d2"}"/>`;
      s += `<text class="val" x="${X(max) + 6}" y="${y + 4}">${r.value.toFixed(3)}</text>`;
    });
    return s + "</svg>";
  };

  // Training curve (validation MRR by step)
  C.curve = function (pts, { w = 380, h = 90, key = "val_mrr" } = {}) {
    const xs = pts.map((p) => p.step), ys = pts.map((p) => p[key]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = 0, y1 = Math.max(...ys) * 1.1;
    const X = (v) => 30 + ((v - x0) / (x1 - x0)) * (w - 70), Y = (v) => h - 18 - ((v - y0) / (y1 - y0)) * (h - 30);
    const d = pts.map((p, i) => `${i ? "L" : "M"}${X(p.step).toFixed(1)},${Y(p[key]).toFixed(1)}`).join("");
    const last = pts[pts.length - 1];
    return `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img"><path d="${d}" fill="none" stroke="#8b7cf6" stroke-width="2"/>` +
      `<circle cx="${X(last.step)}" cy="${Y(last[key])}" r="3.5" fill="#f4efe6"/>` +
      `<text class="val" x="${X(last.step) + 6}" y="${Y(last[key]) + 4}">${last[key].toFixed(3)}</text>` +
      `<text x="30" y="${h - 3}">step ${x0}</text><text x="${w - 90}" y="${h - 3}">step ${x1}</text>` +
      `<text x="0" y="${Y(y1 * 0.9)}">MRR</text></svg>`;
  };

  // Proteomics rank strip: one track per reference cohort, reference IQR band, reference median, the patient's rank.
  C.rankStrip = function (byCohort, { w = 380, row = 30, labelW = 128 } = {}) {
    const cs = Object.keys(byCohort || {});
    if (!cs.length) return `<p class="note">Not quantified in the B7_43 proteomics rank comparison.</p>`;
    const h = cs.length * row + 22, bw = w - labelW - 10, X = (v) => labelW + (v / 100) * bw;
    let s = `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img">`;
    cs.forEach((c, i) => {
      const r = byCohort[c], y = i * row + 12;
      s += `<text class="lab" x="0" y="${y + 4}">${esc(c.replace(/_/g, " "))}</text>`;
      s += `<line x1="${X(0)}" x2="${X(100)}" y1="${y}" y2="${y}" stroke="rgba(236,232,248,.12)" stroke-width="6" stroke-linecap="round"/>`;
      if (r.reference_q25 != null) s += `<rect x="${X(r.reference_q25)}" y="${y - 5}" width="${Math.max(2, X(r.reference_q75) - X(r.reference_q25))}" height="10" rx="2" fill="rgba(79,179,162,.45)"/>`;
      if (r.reference_median_rank_pct != null) s += `<line x1="${X(r.reference_median_rank_pct)}" x2="${X(r.reference_median_rank_pct)}" y1="${y - 7}" y2="${y + 7}" stroke="#a8e3d8" stroke-width="2"/>`;
      if (r.query_rank_pct != null) s += `<circle cx="${X(r.query_rank_pct)}" cy="${y}" r="5" fill="#f4efe6" stroke="#15122b" stroke-width="1.5"><title>Patient rank ${r.query_rank_pct.toFixed(1)} vs reference median ${r.reference_median_rank_pct?.toFixed(1)} (n=${r.n_reference_detected})</title></circle>`;
    });
    s += `<text x="${X(0)}" y="${h - 4}">lower abundance rank</text><text x="${X(100) - 112}" y="${h - 4}">higher abundance rank</text>`;
    return s + "</svg>";
  };

  // Clinical timeline with specimen markers. Returns svg string; elements carry data-ev / data-sp for delegation.
  C.timeline = function (events, specimens, { w = 1100, h = 170 } = {}) {
    const lanes = [["imaging", "Imaging"], ["pathology", "Pathology and biopsy"], ["surgery", "Surgery"],
      ["systemic_therapy", "Systemic therapy"], ["radiotherapy", "Radiotherapy"], ["other", "Biomarkers and other"]];
    const laneOf = (c) => (c === "biopsy" ? "pathology" : lanes.find((l) => l[0] === c) ? c : "other");
    const parse = (d) => (d ? new Date(d.length === 7 ? d + "-15" : d) : null);
    const dates = events.map((e) => parse(e.date)).concat(specimens.map((s) => parse(s.date))).filter(Boolean);
    const t0 = d3.min(dates), t1 = new Date(Math.max(d3.max(dates), new Date("2026-11-01")));
    const left = 150, right = 16, top = 8, laneH = (h - 34) / lanes.length;
    const X = d3.scaleTime().domain([t0, t1]).range([left, w - right]);
    const col = { imaging: "#6f86b8", pathology: "#c46a8e", surgery: "#f4efe6", systemic_therapy: "#8b7cf6", radiotherapy: "#d9a441", other: "#4fb3a2" };
    let s = `<svg class="tl" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Clinical timeline">`;
    lanes.forEach(([k, lab], i) => {
      const y = top + i * laneH + laneH / 2;
      s += `<text class="lane" x="0" y="${y + 4}">${lab}</text><line x1="${left}" x2="${w - right}" y1="${y}" y2="${y}" stroke="rgba(236,232,248,.08)"/>`;
    });
    X.ticks(d3.timeMonth.every(3)).forEach((t) => {
      s += `<line x1="${X(t)}" x2="${X(t)}" y1="${top}" y2="${h - 24}" stroke="rgba(236,232,248,.05)"/>` +
        `<text x="${X(t) - 16}" y="${h - 10}">${d3.timeFormat("%b %Y")(t)}</text>`;
    });
    events.forEach((e) => {
      const d = parse(e.date); if (!d) return;
      const i = lanes.findIndex((l) => l[0] === laneOf(e.category)), y = top + i * laneH + laneH / 2;
      const end = parse(e.end_date);
      if (end) s += `<line x1="${X(d)}" x2="${X(end)}" y1="${y}" y2="${y}" stroke="${col[laneOf(e.category)]}" stroke-width="4" stroke-linecap="round" opacity=".55"/>`;
      const reported = /patient_reported|plan/.test(e.status || "");
      s += `<g class="ev" data-ev="${esc(e.id)}" tabindex="0"><circle cx="${X(d)}" cy="${y}" r="5" fill="${reported ? "none" : col[laneOf(e.category)]}" stroke="${col[laneOf(e.category)]}" stroke-width="1.5"><title>${esc(e.date)}: ${esc(e.title)} (${esc(e.status)})</title></circle></g>`;
    });
    specimens.filter((sp) => sp.date).forEach((sp) => {
      const x = X(parse(sp.date)), y = h - 28;
      s += `<g class="sp" data-sp="${esc(sp.sample_id)}" tabindex="0"><path d="M${x},${y - 6} L${x + 6},${y} L${x},${y + 6} L${x - 6},${y} Z" fill="${sp.proteomics ? "#4fb3a2" : sp.n_findings ? "#f4efe6" : "rgba(244,239,230,.35)"}" stroke="#15122b"><title>${esc(sp.date)}: ${esc(sp.label)} (${sp.n_findings} findings${sp.proteomics ? ", proteomics block" : ""})</title></path></g>`;
    });
    return s + "</svg>";
  };

  C.esc = esc;
  window.Charts = C;
})();
