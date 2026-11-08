/* Persistent network engine for the tour. One canvas, one simulation; chapters change the scene, never the stage.
   Nodes keep their positions across scenes, so the audience watches the same graph transform. */
(function () {
  const TYPE_COLOR = {
    gene: "#8b7cf6", mechanism: "#e0558a", disease: "#c46a8e", phenotype: "#b9b2d8", drug: "#d9a441",
    biological_process: "#6f86b8", molecular_function: "#5f9fb0", cellular_component: "#7c8aa3",
    pathway: "#4fb3a2", anatomy: "#8e8aa0", exposure: "#a08e72", cell: "#9a87b5", treatment: "#d9a441",
    patient: "#f4efe6", unknown: "#7d7896",
  };
  const ORIGIN_EDGE = { dismech: "rgba(224,85,138,", primekg: "rgba(185,178,216,", patient: "rgba(244,239,230,", run: "rgba(139,124,246," };
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const G = {
    nodes: new Map(), edges: new Map(), sim: null, canvas: null, ctx: null, w: 0, h: 0, dpr: 1,
    transform: d3.zoomIdentity, hover: null, selected: null, pulses: [], listeners: {},
    rings: new Map(), S: {},
  };

  G.color = (t) => TYPE_COLOR[t] || TYPE_COLOR.unknown;
  G.on = (ev, fn) => { (G.listeners[ev] = G.listeners[ev] || []).push(fn); };
  const emit = (ev, x) => (G.listeners[ev] || []).forEach((f) => f(x));

  G.init = function (canvas) {
    G.canvas = canvas; G.ctx = canvas.getContext("2d");
    const resize = () => {
      G.dpr = Math.min(window.devicePixelRatio || 1, 2);
      G.w = canvas.clientWidth; G.h = canvas.clientHeight;
      canvas.width = G.w * G.dpr; canvas.height = G.h * G.dpr;
      if (G.sim) applyForces(0.15);
    };
    window.addEventListener("resize", resize); resize();
    G.zoom = d3.zoom().scaleExtent([0.25, 6]).on("zoom", (e) => { G.transform = e.transform; });
    d3.select(canvas).call(G.zoom).on("dblclick.zoom", null);
    canvas.addEventListener("mousemove", (e) => {
      const n = pick(e.offsetX, e.offsetY);
      if (n !== G.hover) { G.hover = n; canvas.style.cursor = n ? "pointer" : "grab"; emit("hover", n); }
    });
    canvas.addEventListener("mouseleave", () => { G.hover = null; emit("hover", null); });
    canvas.addEventListener("click", (e) => {
      const n = pick(e.offsetX, e.offsetY);
      if (n) { G.selected = n.id; emit("select", n); }
      else {
        const ed = pickEdge(e.offsetX, e.offsetY);
        if (ed) emit("selectEdge", ed); else { G.selected = null; emit("select", null); }
      }
    });
    G.sim = d3.forceSimulation([]).alphaDecay(0.035).velocityDecay(0.38).on("tick", () => {});
    requestAnimationFrame(frame);
  };

  /* ---------------------------------------------------------------- data */
  G.merge = function (nodes, edges, seedAt) {
    for (const n of nodes) {
      const id = n.curie || n.id;
      if (!G.nodes.has(id)) {
        const s = seedAt && G.nodes.get(seedAt);
        G.nodes.set(id, {
          id, name: n.name || id, type: n.type || "unknown", data: n,
          x: s ? s.x + (Math.random() - 0.5) * 30 : (Math.random() - 0.5) * 600,
          y: s ? s.y + (Math.random() - 0.5) * 30 : (Math.random() - 0.5) * 400,
          a: 0, ta: 0, r: 0, tr: 3, label: false, ring: null,
        });
      } else {
        const g = G.nodes.get(id);
        g.data = Object.assign({}, g.data, n);
        if (n.name && g.name === id) g.name = n.name;
        if (n.type && g.type === "unknown") g.type = n.type;
      }
    }
    for (const e of edges) {
      const id = e.edge_id || e.row_ref || e.id || `${e.src}>${e.rel}>${e.dst}`;
      if (!G.nodes.has(e.src) || !G.nodes.has(e.dst)) continue;
      if (!G.edges.has(id)) G.edges.set(id, { id, s: e.src, t: e.dst, data: e, origin: e.origin || "primekg", a: 0, ta: 0, dash: !!e.dash, hl: 0 });
      else Object.assign(G.edges.get(id).data, e);
    }
  };

  G.neighbours = function (id, visibleOnly = true) {
    const out = new Set();
    for (const e of G.edges.values()) {
      if (visibleOnly && e.ta <= 0) continue;
      if (e.s === id) out.add(e.t); else if (e.t === id) out.add(e.s);
    }
    return out;
  };

  /* ---------------------------------------------------------------- scenes
     spec: { nodes: Set|null (null = all), edges: fn(edge)->bool, dim: Set, emphasis: Set, labels: Set,
             anchor: {x, y} as fractions of the viewport, spread: number, fixed: {id: {fx, fy}}, rings: {id: color} } */
  G.setScene = function (spec) {
    G.S = Object.assign({ anchor: { x: 0.62, y: 0.5 }, spread: 1 }, spec);
    const S = G.S;
    const vis = [];
    for (const n of G.nodes.values()) {
      const on = !S.nodes || S.nodes.has(n.id);
      const dim = S.dim && S.dim.has(n.id);
      const emph = S.emphasis && S.emphasis.has(n.id);
      n.ta = on ? (dim ? 0.16 : (S.baseAlpha ?? 0.92)) : 0;
      if (emph) n.ta = 1;
      n.tr = on ? (emph ? (S.emphR || 7) : (S.r ? S.r(n) : radius(n))) : 0;
      n.label = !!(S.labels && S.labels.has(n.id));
      n.ring = (S.rings && S.rings[n.id]) || null;
      n.fx = S.fixed && S.fixed[n.id] ? S.fixed[n.id].fx : null;
      n.fy = S.fixed && S.fixed[n.id] ? S.fixed[n.id].fy : null;
      if (on) vis.push(n);
    }
    const links = [];
    for (const e of G.edges.values()) {
      const s = G.nodes.get(e.s), t = G.nodes.get(e.t);
      const on = s.ta > 0 && t.ta > 0 && (!S.edges || S.edges(e));
      const dim = (S.dim && (S.dim.has(e.s) || S.dim.has(e.t)));
      e.ta = on ? (dim ? 0.05 : (S.edgeAlpha ? S.edgeAlpha(e) : 0.32)) : 0;
      e.hl = 0;
      if (on) links.push({ source: s, target: t, e });
    }
    G.sim.nodes(vis);
    G.links = links;
    applyForces(S.reheat ?? 0.5);
  };

  function radius(n) {
    if (n.type === "patient") return 13;
    if (n.type === "mechanism") return 4.2;
    if (n.type === "gene") return 3.6;
    if (n.type === "drug" || n.type === "disease") return 3.6;
    return 2.6;
  }

  function applyForces(alpha) {
    const S = G.S || {};
    const cx = (S.anchor?.x ?? 0.62) * G.w - G.w / 2, cy = (S.anchor?.y ?? 0.5) * G.h - G.h / 2;
    const k = S.spread || 1;
    G.sim
      .force("link", d3.forceLink(G.links || []).distance((l) => (l.e.dash ? 120 * k : (l.e.origin === "dismech" ? 26 : 34) * k)).strength((l) => (l.e.dash ? 0.25 : 0.55)))
      .force("charge", d3.forceManyBody().strength((n) => (n.type === "patient" ? -900 : -26 * k)).distanceMax(320 * k))
      .force("collide", d3.forceCollide((n) => n.tr + 1.5))
      .force("x", d3.forceX(cx).strength(0.045))
      .force("y", d3.forceY(cy).strength(0.06));
    G.sim.alpha(reduced ? 0 : alpha).restart();
    if (reduced) for (let i = 0; i < 160; i++) G.sim.tick();
  }

  /* ---------------------------------------------------------------- camera */
  G.reset = function (dur = 900) {
    d3.select(G.canvas).transition().duration(reduced ? 0 : dur).call(G.zoom.transform, d3.zoomIdentity);
  };
  G.focus = function (ids, dur = 900, scale = null) {
    const pts = [...ids].map((i) => G.nodes.get(i)).filter(Boolean);
    if (!pts.length) return;
    const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
    const [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
    const S = G.S || {};
    const k = scale || Math.max(0.6, Math.min(pts.length < 8 ? 1.5 : 2.4, 0.55 * Math.min(G.w / (x1 - x0 + 160), G.h / (y1 - y0 + 160))));
    const tx = (S.anchor?.x ?? 0.62) * G.w - k * ((x0 + x1) / 2 + G.w / 2);
    const ty = 0.5 * G.h - k * ((y0 + y1) / 2 + G.h / 2);
    d3.select(G.canvas).transition().duration(reduced ? 0 : dur).call(G.zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(k));
  };

  /* ---------------------------------------------------------------- pulses */
  G.pulse = function (fromId, toId, opt = {}) {
    if (reduced) return;
    const now = performance.now();
    G.pulses.push({ a: fromId, b: toId, t0: now + (opt.delay || 0), dur: opt.dur || 900, color: opt.color || "#f4efe6", size: opt.size || 2.4 });
  };
  G.highlightEdges = function (ids, on = true) {
    const set = new Set(ids);
    for (const e of G.edges.values()) e.hl = on && set.has(e.id) ? 1 : 0;
  };
  G.ringPulse = function (id, color = "#f4efe6") { G.rings.set(id, { t0: performance.now(), color }); };

  /* ---------------------------------------------------------------- picking */
  function toWorld(px, py) {
    const [x, y] = G.transform.invert([px, py]);
    return [x - G.w / 2, y - G.h / 2];
  }
  function pick(px, py) {
    const [x, y] = toWorld(px, py);
    let best = null, bd = Infinity;
    for (const n of G.nodes.values()) {
      if (n.a < 0.12 || n.r <= 0) continue;
      const d = Math.hypot(n.x - x, n.y - y);
      const lim = Math.max(n.r + 4, 7 / G.transform.k);
      if (d < lim && d < bd) { bd = d; best = n; }
    }
    return best;
  }
  function pickEdge(px, py) {
    const [x, y] = toWorld(px, py);
    let best = null, bd = 5 / G.transform.k;
    for (const e of G.edges.values()) {
      if (e.a < 0.1) continue;
      const s = G.nodes.get(e.s), t = G.nodes.get(e.t);
      const dx = t.x - s.x, dy = t.y - s.y, L = dx * dx + dy * dy || 1;
      const u = Math.max(0, Math.min(1, ((x - s.x) * dx + (y - s.y) * dy) / L));
      const d = Math.hypot(s.x + u * dx - x, s.y + u * dy - y);
      if (d < bd) { bd = d; best = e; }
    }
    return best;
  }

  /* ---------------------------------------------------------------- render */
  function frame(now) {
    const c = G.ctx, T = G.transform;
    const ease = reduced ? 1 : 0.09;
    for (const n of G.nodes.values()) { n.a += (n.ta - n.a) * ease; n.r += (n.tr - n.r) * ease; }
    for (const e of G.edges.values()) e.a += (e.ta - e.a) * ease;

    c.setTransform(G.dpr, 0, 0, G.dpr, 0, 0);
    c.clearRect(0, 0, G.w, G.h);
    c.save();
    c.translate(T.x, T.y); c.scale(T.k, T.k); c.translate(G.w / 2, G.h / 2);

    // edges
    for (const e of G.edges.values()) {
      if (e.a < 0.01) continue;
      const s = G.nodes.get(e.s), t = G.nodes.get(e.t);
      const base = ORIGIN_EDGE[e.origin] || ORIGIN_EDGE.primekg;
      c.beginPath(); c.moveTo(s.x, s.y); c.lineTo(t.x, t.y);
      if (e.hl) { c.strokeStyle = "rgba(244,239,230,0.95)"; c.lineWidth = 2.2 / Math.sqrt(T.k); }
      else { c.strokeStyle = base + e.a.toFixed(3) + ")"; c.lineWidth = (e.origin === "dismech" ? 1.1 : 0.7) / Math.sqrt(T.k); }
      if (e.dash) c.setLineDash([5 / T.k, 4 / T.k]); else c.setLineDash([]);
      c.stroke();
    }
    c.setLineDash([]);

    // pulses
    G.pulses = G.pulses.filter((p) => now < p.t0 + p.dur);
    for (const p of G.pulses) {
      if (now < p.t0) continue;
      const s = G.nodes.get(p.a), t = G.nodes.get(p.b);
      if (!s || !t) continue;
      const u = d3.easeCubicInOut((now - p.t0) / p.dur);
      const x = s.x + (t.x - s.x) * u, y = s.y + (t.y - s.y) * u;
      const g = c.createRadialGradient(x, y, 0, x, y, p.size * 3);
      g.addColorStop(0, p.color); g.addColorStop(1, "rgba(0,0,0,0)");
      c.fillStyle = g; c.beginPath(); c.arc(x, y, p.size * 3, 0, Math.PI * 2); c.fill();
    }

    // nodes
    for (const n of G.nodes.values()) {
      if (n.a < 0.01 || n.r < 0.2) continue;
      c.globalAlpha = n.a;
      if (n.type === "patient") {
        const g = c.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 3.2);
        g.addColorStop(0, "rgba(244,239,230,0.35)"); g.addColorStop(1, "rgba(244,239,230,0)");
        c.fillStyle = g; c.beginPath(); c.arc(n.x, n.y, n.r * 3.2, 0, Math.PI * 2); c.fill();
      }
      c.fillStyle = G.color(n.type);
      c.beginPath(); c.arc(n.x, n.y, n.r, 0, Math.PI * 2); c.fill();
      if (n.ring) { c.strokeStyle = n.ring; c.lineWidth = 1.6 / Math.sqrt(T.k); c.beginPath(); c.arc(n.x, n.y, n.r + 3, 0, Math.PI * 2); c.stroke(); }
      if (n.id === G.selected || n === G.hover) {
        c.strokeStyle = "#f4efe6"; c.lineWidth = 1.5 / Math.sqrt(T.k);
        c.beginPath(); c.arc(n.x, n.y, n.r + 5, 0, Math.PI * 2); c.stroke();
      }
    }
    c.globalAlpha = 1;

    // ring pulses
    for (const [id, r] of G.rings) {
      const n = G.nodes.get(id), u = (now - r.t0) / 1400;
      if (!n || u > 1) { G.rings.delete(id); continue; }
      c.strokeStyle = r.color; c.globalAlpha = 1 - u; c.lineWidth = 2 / T.k;
      c.beginPath(); c.arc(n.x, n.y, n.r + 4 + u * 30, 0, Math.PI * 2); c.stroke();
    }
    c.globalAlpha = 1;

    // labels, with collision avoidance in screen space (priority: selected/hover, patient, emphasised, larger nodes)
    const fs = 11.5 / T.k;
    c.font = `500 ${fs}px "Public Sans", system-ui, sans-serif`;
    c.textBaseline = "middle";
    const cand = [];
    for (const n of G.nodes.values()) {
      const forced = n === G.hover || n.id === G.selected;
      const show = n.a > 0.25 && (forced || n.label || (T.k > 2.2 && n.a > 0.6 && n.type !== "phenotype"));
      if (show) cand.push([forced ? 3 : n.type === "patient" ? 2 : n.label ? 1 : 0, n]);
    }
    cand.sort((a, b) => b[0] - a[0] || b[1].r - a[1].r);
    const boxes = [];
    for (const [pri, n] of cand) {
      const txt = n.name.length > 34 ? n.name.slice(0, 33) + "…" : n.name;
      const x = n.x + n.r + 4 / T.k, y = n.y;
      const sx = T.applyX(x + G.w / 2), sy = T.applyY(y + G.h / 2);
      const wpx = (n._tw && n._tw[0] === txt ? n._tw[1] : (n._tw = [txt, c.measureText(txt).width * T.k])[1]);
      const box = [sx - 2, sy - 8, sx + wpx + 2, sy + 8];
      if (pri < 3 && boxes.some((b) => box[0] < b[2] && box[2] > b[0] && box[1] < b[3] && box[3] > b[1])) continue;
      boxes.push(box);
      c.globalAlpha = Math.min(1, n.a + 0.1);
      c.lineWidth = 3.5 / T.k; c.strokeStyle = "rgba(21,18,43,0.9)"; c.strokeText(txt, x, y);
      c.fillStyle = n.type === "patient" ? "#f4efe6" : "#e9e5f7"; c.fillText(txt, x, y);
    }
    c.globalAlpha = 1;
    c.restore();
    requestAnimationFrame(frame);
  }

  window.Graph = G;
})();
