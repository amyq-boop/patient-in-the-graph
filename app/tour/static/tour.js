/* Patient-in-the-Graph guided tour. Six chapters over one persistent network, then a patient workspace.
   Data: GET /api/bootstrap (real graph rows, committed results, verified public case). Runs: POST /api/investigate. */
(function () {
  const $ = (s, r = document) => r.querySelector(s);
  const esc = Charts.esc;
  const fmt = (n) => (n == null ? "n/a" : Number(n).toLocaleString("en-US"));
  const f3 = (n) => (n == null ? "n/a" : Number(n).toFixed(3));
  const PATIENT = "PATIENT:youngcrc";
  const state = { ch: -1, boot: null, typesOff: new Set(), kindsOn: new Set(["ihc", "clinical_report", "variant", "proteomics"]),
    mapSel: null, marker: null, run: null, local: null, busy: false, wsTab: "molecular", notes: false, mpTimer: null };

  /* ---------------------------------------------------------------- helpers */
  const api = async (path, body) => {
    const r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
    return r.json();
  };
  const toast = (msg) => { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => (t.hidden = true), 3200); };
  const typeLabel = (t) => ({ biological_process: "biological process", molecular_function: "molecular function", cellular_component: "cellular component" }[t] || t);
  const kindLabel = { ihc: "IHC marker", clinical_report: "clinical report", variant: "research locus", proteomics: "proteomics panel" };
  const relLabel = (r) => String(r || "").replace(/^dm_/, "").replace(/_rev$/, " (reverse)").replace(/_/g, " ");

  const inWs = () => document.body.classList.contains("is-ws");
  const AX = () => ({ x: inWs() ? 0.3 : 0.68, y: !inWs() && state.ch === 5 ? 0.3 : 0.5 });
  const PF = (x, y) => (inWs() ? patientFixed(0.12, 0.5) : state.ch === 5 ? patientFixed(0.5, 0.3) : patientFixed(x, y));
  const hv = (v) => String(v ?? "").replace(/_/g, " ");
  function stageIds() { return new Set(state.boot.stage.nodes.map((n) => n.curie)); }
  function mappedCuries() { return new Set(state.boot.mapping.filter((m) => m.curie && state.kindsOn.has(m.kind)).map((m) => m.curie)); }

  /* ---------------------------------------------------------------- boot */
  async function boot() {
    Graph.init($("#stage"));
    $("#intro-foot").textContent = "Loading the knowledge graph and the public case data ...";
    let b;
    try { b = await api("/api/bootstrap"); }
    catch (e) { $("#intro-foot").textContent = "Could not reach the tour server. Start it with: python -m app.tour"; return; }
    if (b.ok === false) { $("#intro-foot").textContent = "The tour server could not load its data: " + b.error; return; }
    state.boot = b;
    Graph.merge(b.stage.nodes, b.stage.edges);
    const gi = b.graph_index, p = b.patient;
    $("#intro-foot").innerHTML = `Showing ${fmt(gi.n_nodes)} real entities and ${fmt(gi.n_edges)} real relationships from ${gi.source === "full_graph" ? "the full merged graph" : "this project's executed graph retrievals and PrimeKG extracts"}. ` +
      `Patient data: Alexander Young, Open Cancer Data release ${esc(p.release)}, ${p.data_source === "live" ? "loaded live from youngcrc.com" : "hash-verified offline copy"}.`;
    buildRail();
    introScene();
    wire();
    route();
    window.addEventListener("hashchange", route);
  }

  function route() {
    const h = location.hash;
    if (h === "#workspace") { if (!inWs()) openWorkspace(); }
    else if (/^#ch=\d/.test(h)) { const i = Math.min(5, Math.max(0, +h.slice(4) - 1)); if (i !== state.ch || inWs()) go(i); }
  }

  function introScene() {
    Graph.setScene({ anchor: { x: 0.7, y: 0.52 }, baseAlpha: 0.55, edgeAlpha: (e) => (e.origin === "dismech" ? 0.45 : 0.14), reheat: 0.9 });
  }

  /* ---------------------------------------------------------------- chapters */
  const CH = [
    { title: "Knowledge graph", enter: ch1, notes: [
      "Start broad: this is biology before any patient. Every dot is a real entity, every line a real relationship.",
      "Pink lines are DisMech: curated disease mechanisms, each tied to a paper (PMID) and a supporting quote.",
      "Click a pink edge or a mechanism step to show the citation. Toggle entity types to show the mix."] },
    { title: "Our GNN", enter: ch2, notes: [
      "Click Watch message passing. Two layers: each node mixes in its neighbours, per relation type.",
      "Lead with what works: link prediction MRR, retrieval recall vs four baselines, verifier-supported claims vs LLM alone.",
      "Say the honest part out loud: the GNN does not beat a proteomics baseline on stage, and with ccRCC mechanisms masked recovery is low. Judges reward this.",
      "All of this is CPTAC kidney cancer. That is why we never reuse the kidney stage model on Alexander."] },
    { title: "The patient", enter: ch3, notes: [
      "Alexander Young published his own records: reports, sequencing, proteomics. Release number and hashes are checked on load.",
      "Click a specimen diamond on the timeline: findings are tied to the tissue and date they came from.",
      "Most molecular data is from 2024 tissue. His disease has progressed since. The tool keeps that visible."] },
    { title: "Into the graph", enter: ch4, notes: [
      "The cover-slip node is Alexander. Dashed lines are his measurements, solid lines are knowledge.",
      "Filter by kind, click CEACAM5 or MET: the measurement keeps its specimen, date and source.",
      "Proteomics: show the rank strip. It is a rank against reference cohorts, not a fold change."] },
    { title: "Investigation", enter: ch5, notes: [
      "Pick a marker and run: GNN-B retrieves a subgraph on the full 8.5M-edge graph, then synthesis, then the strict verifier.",
      "Click a citation chip: the exact edge lights up. That is the trust story.",
      "If the graph assets are not on this laptop, say so and use the source-only verifier. Never pretend."] },
    { title: "Clinical insight", enter: ch6, notes: [
      "Three columns: observed, hypotheses, not concluded. The verifier removed the planted false claim.",
      "Generate the case review: a source-linked document a molecular tumour board could read.",
      "Close: scattered reports in one place, every claim traceable, unsupported claims removed before a clinician sees them."] },
  ];

  function buildRail() {
    $("#rail").innerHTML = CH.map((c, i) => `<li><button data-ch="${i}"><span class="n">${i + 1}</span><span class="lbl">${c.title}</span></button></li>`).join("");
  }

  function go(i) {
    if (!state.boot || i < 0 || i >= CH.length) return;
    closeWorkspace(true);
    document.body.classList.remove("is-intro");
    stopMP();
    state.ch = i;
    history.replaceState(null, "", `#ch=${i + 1}`);
    document.querySelectorAll(".rail button").forEach((b, j) => { b.classList.toggle("on", j === i); b.classList.toggle("done", j < i); });
    $("#progbar").style.width = `${((i + 1) / CH.length) * 100}%`;
    $("#prev").disabled = i === 0;
    $("#next").textContent = i === CH.length - 1 ? "Open the workspace" : "Next";
    hideDetail(); $("#band").hidden = true; $("#band").innerHTML = ""; document.body.classList.remove("has-band");
    document.querySelectorAll(".board,.caption,.legend").forEach((x) => x.remove());
    Graph.highlightEdges([], false);
    CH[i].enter();
    $("#narr").scrollTop = 0;
    renderNotes();
  }

  function narr(html) { $("#narr").innerHTML = `<div class="enter">${html}</div>`; }
  function step(i) { return `<p class="step">Chapter ${i} of 6</p>`; }
  function legend(items) {
    const d = document.createElement("div"); d.className = "legend";
    d.innerHTML = items.map(([c, l, cls]) => `<span><i class="${cls || ""}" style="${cls ? `border-color:${c}` : `background:${c}`}"></i>${esc(l)}</span>`).join("");
    document.body.appendChild(d);
  }

  /* ---------------------------------------------------------------- chapter 1: knowledge graph */
  function ch1() {
    const b = state.boot, g = b.results.graph || {}, gi = b.graph_index;
    const counts = {};
    b.stage.nodes.forEach((n) => (counts[n.type] = (counts[n.type] || 0) + 1));
    const types = Object.keys(counts).sort((a, c) => counts[c] - counts[a]);
    const chain = (b.mechanisms || []).find((m) => /renal/.test(m.disease_name || "") && m.steps.some((s) => s.pmids.length)) || (b.mechanisms || [])[0];
    const ov = g.dismech_vs_primekg_overlap || {};
    const newPairs = Object.values(ov).reduce((a, v) => a + (v.new_from_dismech || 0), 0);
    narr(`${step(1)}<h2>Biology, already connected</h2>
      <p class="big">Before we look at any patient, we start with what medicine already knows, written as a graph.</p>
      <p><b>PrimeKG</b> (Harvard, Zitnik lab) joins twenty biomedical resources into one network of genes, proteins, diseases, phenotypes, drugs and pathways. <b>DisMech</b> adds curated disease mechanisms: cause-and-effect steps, each backed by a published paper and a supporting quote.</p>
      <div class="stats">
        <div><b>${fmt(g.n_nodes)}</b>entities in our merged graph</div>
        <div><b>${fmt(g.edges_directed_total)}</b>directed relationships</div>
        <div class="hema"><b>${fmt(g.edge_types)}</b>relationship types</div>
        <div class="eosin"><b>${fmt(newPairs)}</b>disease links DisMech adds that PrimeKG lacks</div>
      </div>
      <p class="src">Counts from <code>${esc(g.source)}</code>.</p>
      <h3>A curated mechanism, with its evidence</h3>
      ${chain ? `<p class="note">${esc(chain.disease_name)}, DisMech chain as returned by the agent's get_mechanism tool:</p><ol class="chain">${chain.steps.map((s) => `<li>${esc(s.label)} ${s.edge_id ? `<span class="id">${esc(s.edge_id)}</span>` : ""} ${s.pmids.map((p) => `<a href="https://pubmed.ncbi.nlm.nih.gov/${p.replace("PMID:", "")}/" target="_blank" rel="noopener">${esc(p)}</a>`).join(" ")}${s.quote ? `<span class="q">${esc(cleanQuote(s.quote))}</span>` : ""}</li>`).join("")}</ol>` : `<p class="note">No mechanism chain in the saved runs.</p>`}
      <h3>Explore what is on screen</h3>
      <p>${fmt(b.stage.nodes.length)} entities and ${fmt(b.stage.edges.length)} relationships, each a real row returned by this project's graph tools. Click a node or a line to see its identifier and evidence. Toggle types:</p>
      <div class="chips" id="typechips">${types.map((t) => `<button class="chip ${state.typesOff.has(t) ? "off" : "on"}" data-type="${t}"><i style="background:${Graph.color(t)}"></i>${esc(typeLabel(t))} ${counts[t]}</button>`).join("")}</div>
      <p class="note" style="margin-top:1rem">${esc(gi.description)}</p>`);
    $("#typechips").onclick = (e) => {
      const btn = e.target.closest("[data-type]"); if (!btn) return;
      const t = btn.dataset.type; state.typesOff.has(t) ? state.typesOff.delete(t) : state.typesOff.add(t);
      btn.classList.toggle("off"); btn.classList.toggle("on"); kgScene();
    };
    kgScene(true);
    legend([["#8b7cf6", "gene"], ["#e0558a", "mechanism"], ["#b9b2d8", "phenotype"], ["#d9a441", "drug"], ["#6f86b8", "GO term"], ["#4fb3a2", "pathway"], ["#e0558a", "DisMech edge", "ln"], ["#b9b2d8", "PrimeKG edge", "ln"]]);
  }

  function hubLabels(ids, n = 10) {
    const deg = new Map();
    for (const e of Graph.edges.values()) if (e.ta > 0 || true) { if (ids.has(e.s)) deg.set(e.s, (deg.get(e.s) || 0) + 1); if (ids.has(e.t)) deg.set(e.t, (deg.get(e.t) || 0) + 1); }
    return new Set([...deg.entries()].sort((a, b) => b[1] - a[1]).slice(0, n).map((x) => x[0]));
  }

  function kgScene(first) {
    const ids = new Set([...stageIds()].filter((c) => !state.typesOff.has((Graph.nodes.get(c) || {}).type)));
    Graph.setScene({ nodes: ids, labels: hubLabels(ids, 12), anchor: { x: 0.66, y: 0.5 },
      edgeAlpha: (e) => (e.origin === "dismech" ? 0.6 : 0.2), reheat: first ? 0.6 : 0.25 });
    if (first) Graph.reset();
  }

  function cleanQuote(q) { return String(q).replace(/\[PMID:\d+\]\s*\[[A-Z]+\]\s*/g, "").split(" --- ")[0].slice(0, 220); }

  /* ---------------------------------------------------------------- chapter 2: GNN */
  function ch2() {
    const r = state.boot.results, m = r.gnn, a = m?.architecture || {};
    const e1 = r.e1, e2 = r.e2, e3 = r.e3, e4 = r.e4;
    const e3a = e3?.by_mode?.agent, e3l = e3?.by_mode?.llm_only;
    const frac = (s) => { const [x, y] = String(s || "0/1").split("/").map(Number); return y ? x / y : 0; };
    const e4m = e4?.masked, e4c = e4?.ceiling;
    narr(`${step(2)}<h2>A model that learns the graph's shape</h2>
      <p class="big">GNN-A reads the whole graph and gives every entity a 128-number fingerprint that reflects its neighbourhood.</p>
      <p>Each entity starts from a text embedding of its name. In each of ${a.encoder ? "two" : "its"} layers, an entity averages its neighbours' vectors separately for each of the ${fmt(a.edge_types)} relation types, then mixes in its own. A DistMult decoder then scores whether any two entities should be linked.</p>
      <div class="btnrow"><button class="primary" id="mp">Watch message passing</button><span class="note" id="mp-on"></span></div>
      <div class="stats">
        <div class="hema"><b>${f3(m?.test_linkpred?.mrr)}</b>test MRR, held-out links</div>
        <div><b>${f3(m?.test_linkpred?.hits10)}</b>Hits@10 against 50 negatives</div>
        <div><b>${fmt(a.parameters)}</b>parameters</div>
        <div><b>${fmt(a.message_edges)}</b>edges in message passing</div>
      </div>
      <h3>Validation MRR while training</h3>${m ? Charts.curve(m.train_curve) : ""}
      <p class="src">Without DisMech the same model reaches test MRR ${f3(m?.no_dismech?.test_linkpred?.mrr)}. Source <code>${esc(m?.source)}</code>.</p>
      <div class="scope"><b>Scope.</b> ${esc(r.scope)}</div>
      <h3>Does it find the right biology? Retrieval (E1)</h3>
      <p>${esc(e1?.task)} ${fmt(e1?.n_test_diseases)} held-out diseases, ${e1?.budget}-node budget. Recall of hidden genes and GO terms, mean with standard error:</p>
      ${e1 ? Charts.bars(e1.methods.map((x) => ({ label: x.label.replace(" (ours)", ""), value: x.recall_pooled, err: x.sem, ours: x.id === "gnn_pcst" })), { labelW: 190 }) : ""}
      <p class="src">${esc(e1?.note)} Source <code>${esc(e1?.source)}</code>.</p>
      <h3>Does grounding make answers checkable? (E3)</h3>
      ${e3a ? Charts.bars([{ label: "Graph agent, claims supported", value: frac(e3a.claims_supported), ours: true }, { label: "Same LLM, no tools", value: frac(e3l?.claims_supported) },
        { label: "Graph agent, gold recall", value: e3a.gold_recall, ours: true }, { label: "Same LLM, gold recall", value: e3l?.gold_recall }], { max: 1, labelW: 190, fmt: (v) => v.toFixed(2) }) : ""}
      <p class="src">Claims supported: ${esc(e3a?.claims_supported)} vs ${esc(e3l?.claims_supported)}. ${esc(e3?.task)} Source <code>${esc(e3?.source)}</code>.</p>
      <h3>Where it falls short</h3>
      <p>Stage III to IV prediction on ${fmt(e2?.n)} CPTAC kidney tumours (out-of-fold AUROC, 95% CI):</p>
      ${e2 ? Charts.ci(e2.models.filter((x) => ["clinical_lr", "prot_pca_lr", "gnn", "gnn_mut"].includes(x.id)).map((x) => ({ label: x.label, value: x.pooled_oof_auroc, lo: x.pooled_ci95[0], hi: x.pooled_ci95[1], ours: x.id.startsWith("gnn") })), { labelW: 170 }) : ""}
      <p>${esc(e2?.honest_readout)}</p>
      ${e4m ? `<p>Mechanism recovery (E4, ${e4m.n} pre-registered patients): with ccRCC's own mechanism edges masked, the agent recovers ${e4m.mean_steps_recovered} of 5 DisMech steps on average; with them visible, ${e4c?.mean_steps_recovered} of 5. The graph carries the answer; inferring it when hidden remains hard.</p>` : ""}
      <p class="src">Sources <code>${esc(e2?.source)}</code>, <code>${esc(e4m?.source)}</code>, <code>${esc(e4c?.source)}</code>.</p>`);
    $("#mp").onclick = () => messagePassing(state.mpNode || pickHub());
    kgScene(false);
    state.mpNode = pickHub();
    $("#mp-on").textContent = "Click any node first to choose where to watch.";
    legend([["#f4efe6", "message (illustration)"], ["#8b7cf6", "gene"], ["#e0558a", "DisMech edge", "ln"]]);
  }

  function pickHub() {
    const pref = ["NCBIGene:4233", "NCBIGene:2064", "NCBIGene:7157", "NCBIGene:7428"];
    for (const p of pref) if (Graph.nodes.has(p) && Graph.nodes.get(p).ta > 0 && Graph.neighbours(p).size > 3) return p;
    let best = null, bd = 0;
    for (const n of Graph.nodes.values()) { const d = Graph.neighbours(n.id).size; if (n.ta > 0 && d > bd) { bd = d; best = n.id; } }
    return best;
  }

  function stopMP() { clearTimeout(state.mpTimer); document.querySelectorAll(".caption").forEach((x) => x.remove()); }

  function messagePassing(id) {
    stopMP();
    const t = Graph.nodes.get(id); if (!t) return;
    const h1 = [...Graph.neighbours(id)];
    const h2 = new Map();
    h1.forEach((n) => Graph.neighbours(n).forEach((m) => { if (m !== id && !h1.includes(m)) h2.set(m, n); }));
    const ring = new Set([id, ...h1, ...h2.keys()]);
    const dim = new Set([...stageIds()].filter((c) => !ring.has(c)));
    Graph.setScene({ nodes: Graph.S.nodes, dim, emphasis: new Set([id]), labels: new Set([id, ...h1.slice(0, 8)]), anchor: Graph.S.anchor, edgeAlpha: Graph.S.edgeAlpha, reheat: 0.05 });
    Graph.focus(ring, 900);
    const cap = document.createElement("div"); cap.className = "caption"; document.body.appendChild(cap);
    const say = (main, small) => (cap.innerHTML = `${esc(main)}<small>${esc(small)}</small>`);
    say(`Watching ${t.name}`, `${h1.length} direct neighbours, ${h2.size} at two hops, all real edges. An illustration of the computation's flow, not the trained model's activations.`);
    state.mpTimer = setTimeout(() => {
      say("Layer 1: every entity averages its neighbours", "Two-hop neighbours send to one-hop neighbours, and one-hop neighbours send to the target, separately per relation type.");
      [...h2.entries()].slice(0, 80).forEach(([m, n], i) => Graph.pulse(m, n, { delay: i * 12, dur: 1100, color: "#b3a8ff" }));
      h1.forEach((n, i) => Graph.pulse(n, id, { delay: 300 + i * 20, dur: 1100, color: "#b3a8ff" }));
      state.mpTimer = setTimeout(() => {
        say("Layer 2: the target hears from two hops away", "One-hop neighbours, now carrying what they heard in layer 1, send again. The target's vector now reflects its two-hop neighbourhood.");
        h1.forEach((n, i) => Graph.pulse(n, id, { delay: i * 25, dur: 1300, color: "#f4efe6", size: 3 }));
        state.mpTimer = setTimeout(() => {
          Graph.ringPulse(id, "#f4efe6");
          say(`${t.name} now has a 128-number embedding`, "DistMult then scores candidate links from it. GNN-B uses those scores as prizes when it retrieves a subgraph for a question.");
        }, 1500 + h1.length * 25);
      }, 1700 + Math.min(80, h2.size) * 12);
    }, 1400);
  }

  /* ---------------------------------------------------------------- chapter 3: the patient */
  function ch3() {
    const p = state.boot.patient, q = p.proteomics;
    const ev = p.timeline;
    const key = [
      ev.find((e) => e.category === "pathology"),
      ev.find((e) => e.category === "surgery"),
      ev.find((e) => e.category === "systemic_therapy"),
      [...ev].reverse().find((e) => e.category === "imaging"),
      [...ev].reverse().find((e) => e.category === "biopsy"),
    ].filter(Boolean);
    const bySpec = {};
    p.findings.forEach((f) => (bySpec[f.sample_id] = bySpec[f.sample_id] || []).push(f));
    narr(`${step(3)}<h2>Alexander Young</h2>
      <p class="big">A real person with metastatic rectal cancer, who published his own records so that others could learn from them.</p>
      <p>His public dataset holds clinical reports, imaging summaries, sequencing results and tumour proteomics, each linked to the tissue specimen and the document it came from. We use release ${esc(p.release)}; every file is checked against the release manifest's SHA-256 before it is shown.</p>
      <div class="stats">
        <div><b>${ev.length}</b>dated clinical events, ${esc(ev[0]?.date)} to ${esc(ev[ev.length - 1]?.date)}</div>
        <div><b>${p.findings.length}</b>source-linked molecular findings</div>
        <div><b>${p.specimens.length}</b>specimens with roles and dates</div>
        <div><b>${fmt(q.qc.tumour_detected_groups)}</b>protein groups detected in one tumour block</div>
      </div>
      <h3>Key moments, as recorded</h3>
      <ol class="chain">${key.map((e) => `<li><b>${esc(e.date)}</b> ${esc(e.title)}<span class="q">${esc(e.summary)}</span></li>`).join("")}</ol>
      <h3>Findings by specimen</h3>
      <div class="list" id="spec-list">${p.specimens.filter((s) => s.n_findings || s.proteomics).map((s) => `<button class="item" data-sp="${esc(s.sample_id)}"><span class="t">${esc(s.label)}</span><span class="v">${esc(s.date || "undated")}</span><span class="s">${s.proteomics ? "Proteomics block B7_43. " : ""}${(bySpec[s.sample_id] || []).map((f) => `${esc(f.feature)}: ${esc(hv(f.value))}${f.unit ? " " + esc(f.unit) : ""}`).join("; ")}</span></button>`).join("")}</div>
      <div class="scope"><b>Specimen dates matter.</b> Most molecular results come from tissue collected in 2024. His disease has progressed since then. Each finding carries its specimen and date, and the release marks current-node status as not assessed.</div>
      <p class="src">Alexander Young, Open Cancer Data, <a href="${esc(p.base_url)}" target="_blank" rel="noopener">youngcrc.com</a>, release ${esc(p.release)} (${p.data_source === "live" ? "live" : "verified offline copy"}). Curated data CC BY 4.0. ${Object.keys(p.sha256).length} files hash-checked.</p>`);
    $("#spec-list").onclick = (e) => { const b = e.target.closest("[data-sp]"); if (b) selectSpecimen(b.dataset.sp); };
    showTimeline();
    Graph.setScene({ nodes: stageIds(), dim: stageIds(), anchor: { x: 0.72, y: 0.36 }, reheat: 0.2 });
    Graph.reset();
  }

  function showTimeline() {
    const p = state.boot.patient, band = $("#band");
    band.hidden = false; document.body.classList.add("has-band");
    band.innerHTML = `<h3>Clinical timeline <span>Filled dots: documented. Hollow: patient-reported or planned. Diamonds: specimens (teal is the proteomics block). Click to inspect.</span></h3>${Charts.timeline(p.timeline, p.specimens)}`;
    band.onclick = (e) => {
      const ev = e.target.closest("[data-ev]"), sp = e.target.closest("[data-sp]");
      if (ev) selectEvent(ev.dataset.ev); else if (sp) selectSpecimen(sp.dataset.sp);
    };
    band.onkeydown = (e) => { if (e.key === "Enter") band.onclick(e); };
  }

  function selectEvent(id) {
    const e = state.boot.patient.timeline.find((x) => x.id === id); if (!e) return;
    document.querySelectorAll(".tl .ev").forEach((g) => g.classList.toggle("on", g.dataset.ev === id));
    showDetail(`<h4>${esc(e.title)}</h4><div class="meta">${esc(e.date)}${e.end_date ? " to " + esc(e.end_date) : ""}, ${esc(e.category.replace(/_/g, " "))}</div>
      <p>${esc(e.summary)}</p><p class="note">Evidence status: ${esc(String(e.status).replace(/_/g, " "))}</p>
      ${e.limitations.length ? `<h3>Limitations recorded</h3><ul class="edges">${e.limitations.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : ""}
      <h3>Sources</h3>${sourcesHtml(e.sources)}`);
  }

  function sourcesHtml(sources) {
    if (!sources?.length) return `<p class="note">No source document attached in the release.</p>`;
    return `<ul class="edges">${sources.map((s) => `<li><span class="id">${esc(s.id)}</span> ${s.url ? `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a>` : esc(s.label)}${s.date ? ` <span class="rel">(${esc(s.date)})</span>` : ""}${s.resolved === false ? ` <span class="tag miss">unresolved</span>` : ""}</li>`).join("")}</ul>`;
  }

  function selectSpecimen(id) {
    const p = state.boot.patient, s = p.specimens.find((x) => x.sample_id === id); if (!s) return;
    document.querySelectorAll("#spec-list .item").forEach((b) => b.classList.toggle("on", b.dataset.sp === id));
    document.querySelectorAll(".tl .sp").forEach((g) => g.classList.toggle("on", g.dataset.sp === id));
    const fs = p.findings.filter((f) => f.sample_id === id);
    showDetail(`<h4>${esc(s.label)}</h4><div class="meta">${esc(s.sample_id)}, ${esc(s.site || "site not stated")}, collected ${esc(s.date || "date not stated")}</div>
      <p class="note">Role: ${esc(String(s.role || "not stated").replace(/_/g, " "))}. Preservation: ${esc(String(s.preservation || "not stated").replace(/_/g, " "))}.</p>
      ${s.proteomics ? `<p>Source of the B7_43 quantitative proteomics: ${fmt(p.proteomics.qc.tumour_detected_groups)} protein groups detected, ${fmt(p.proteomics.qc.high_support_groups)} with two or more unique peptides.</p>` : ""}
      ${fs.length ? `<h3>Findings from this specimen</h3><ul class="edges">${fs.map((f) => `<li><b>${esc(f.feature)}</b>: ${esc(hv(f.value))}${f.unit ? " " + esc(f.unit) : ""}<br><span class="rel">${esc(f.assay_label)}, ${esc(String(f.evidence_class).replace(/_/g, " "))}. Current node status: ${esc(String(f.current_node_status).replace(/_/g, " "))}.</span>${sourcesHtml(f.sources)}</li>`).join("")}</ul>` : ""}`);
  }

  /* ---------------------------------------------------------------- chapter 4: integration */
  function ensurePatientNode() {
    const b = state.boot;
    if (!Graph.nodes.has(PATIENT)) Graph.merge([{ curie: PATIENT, name: "Alexander Young", type: "patient" }], []);
    const edges = b.mapping.filter((m) => m.curie && Graph.nodes.has(m.curie)).map((m) => ({
      id: `map:${m.measurement_id}:${m.curie}`, src: PATIENT, dst: m.curie, rel: kindLabel[m.kind], origin: "patient", dash: true, mapping: m }));
    Graph.merge([], edges.map((e) => ({ ...e, edge_id: null, row_ref: e.id })));
  }

  function patientFixed(xFrac = 0.47, yFrac = 0.5) { return { [PATIENT]: { fx: (xFrac - 0.5) * Graph.w, fy: (yFrac - 0.5) * Graph.h } }; }

  function integrationScene(reheat = 0.6) {
    ensurePatientNode();
    const mapped = mappedCuries();
    const near = new Set();
    mapped.forEach((c) => Graph.neighbours(c, false).forEach((n) => { if (stageIds().has(n)) near.add(n); }));
    const show = new Set([...stageIds(), PATIENT, ...mapped]);
    const dim = new Set([...show].filter((c) => c !== PATIENT && !mapped.has(c) && !near.has(c)));
    const sel = state.mapSel?.curie;
    Graph.setScene({ nodes: show, dim, emphasis: new Set([...mapped, PATIENT]), emphR: 5.5, labels: new Set([PATIENT, ...mapped]),
      rings: Object.fromEntries([...mapped].map((c) => [c, c === sel ? "#f4efe6" : null]).filter((x) => x[1])),
      edges: (e) => e.origin !== "patient" || (e.data.mapping && state.kindsOn.has(e.data.mapping.kind)),
      edgeAlpha: (e) => (e.origin === "patient" ? 0.75 : e.origin === "dismech" ? 0.4 : 0.22),
      anchor: AX(), fixed: PF(0.47, 0.5), reheat });
  }

  function ch4() {
    const b = state.boot, M = b.mapping;
    const kinds = ["ihc", "clinical_report", "variant", "proteomics"];
    const byKind = (k) => M.filter((m) => m.kind === k);
    const resolved = M.filter((m) => m.curie).length, withEdges = M.filter((m) => m.n_graph_edges > 0).length;
    narr(`${step(4)}<h2>Placing his measurements in the graph</h2>
      <p class="big">Each measurement that names a gene becomes a dashed line from Alexander to that gene's node in the knowledge graph.</p>
      <p>Solid lines are general knowledge. Dashed lines are his data, and each one carries the specimen, date and source it came from. Nothing is inferred at this step; we only resolve names to graph identifiers.</p>
      <div class="stats">
        <div><b>${M.length}</b>gene-level measurements</div>
        <div class="hema"><b>${resolved}</b>resolved to a graph entity</div>
        <div><b>${withEdges}</b>with relationships in the ${b.graph_index.source === "full_graph" ? "full graph" : "local index"}</div>
        <div><b>${fmt(b.patient.proteomics.n_genes_ranked)}</b>genes ranked by proteomics</div>
      </div>
      <div class="chips" id="kindchips">${kinds.map((k) => `<button class="chip ${state.kindsOn.has(k) ? "on" : "off"}" data-kind="${k}"><span class="tag ${k}">${byKind(k).length}</span>${kindLabel[k]}</button>`).join("")}</div>
      <h3>Measurements</h3>
      <div class="list" id="maplist"></div>
      <p class="note">Unresolved or unconnected entries stay listed and are marked, rather than dropped.</p>`);
    $("#kindchips").onclick = (e) => {
      const btn = e.target.closest("[data-kind]"); if (!btn) return;
      const k = btn.dataset.kind; state.kindsOn.has(k) ? state.kindsOn.delete(k) : state.kindsOn.add(k);
      btn.classList.toggle("on"); btn.classList.toggle("off"); renderMapList(); integrationScene(0.3);
    };
    renderMapList();
    const fromPrev = !Graph.nodes.has(PATIENT);
    integrationScene(0.8);
    Graph.reset();
    Graph.ringPulse(PATIENT, "#f4efe6");
    if (fromPrev) setTimeout(() => {
      [...mappedCuries()].forEach((c, i) => Graph.pulse(PATIENT, c, { delay: i * 35, dur: 1000, color: "#f4efe6" }));
    }, 700);
    legend([["#f4efe6", "Alexander Young"], ["#f4efe6", "his measurement", "ln dash"], ["#8b7cf6", "gene"], ["#e0558a", "DisMech edge", "ln"], ["#b9b2d8", "PrimeKG edge", "ln"]]);
  }

  function renderMapList() {
    const M = state.boot.mapping.filter((m) => state.kindsOn.has(m.kind));
    const order = { ihc: 0, clinical_report: 1, variant: 2, proteomics: 3 };
    M.sort((a, b) => order[a.kind] - order[b.kind] || (b.n_graph_edges - a.n_graph_edges));
    $("#maplist").innerHTML = M.map((m, i) => {
      const r = m.ranks?.[state.boot.patient.proteomics.primary_reference];
      const v = m.kind === "proteomics" ? (r ? `rank ${r.query_rank_pct.toFixed(0)}` : "not ranked") : hv(m.value ?? "");
      const miss = !m.curie ? `<span class="tag miss">no graph entity</span>` : !m.n_graph_edges ? `<span class="tag miss">no edges in index</span>` : "";
      return `<button class="item ${state.mapSel === m ? "on" : ""}" data-i="${i}"><span class="t">${esc(m.symbol)}</span><span class="v">${esc(v)}</span>
        <span class="s"><span class="tag ${m.kind}">${kindLabel[m.kind]}</span>${esc(m.label)}${m.specimen ? `, ${esc(m.specimen.label)} ${esc(m.specimen.date || "")}` : ""} ${miss}</span></button>`;
    }).join("");
    $("#maplist").onclick = (e) => { const b = e.target.closest("[data-i]"); if (b) selectMapping(M[+b.dataset.i]); };
  }

  async function selectMapping(m) {
    state.mapSel = m;
    document.querySelectorAll("#maplist .item").forEach((b) => b.classList.remove("on"));
    const ref = state.boot.patient.proteomics.primary_reference;
    let html = `<h4>${esc(m.symbol)}</h4><div class="meta">${esc(m.curie || "not resolved to a graph identifier")}</div>
      <p><span class="tag ${m.kind}">${kindLabel[m.kind]}</span> ${esc(m.label)}${m.value != null && m.kind !== "proteomics" ? `: <b>${esc(hv(m.value))}</b>` : ""}</p>
      <p class="note">${esc(m.note)}${m.specimen ? ` Specimen: ${esc(m.specimen.label)}, ${esc(m.specimen.date || "date not stated")}.` : ""} Evidence class: ${esc(String(m.evidence_class || "").replace(/_/g, " "))}.</p>`;
    if (m.ranks) html += `<h3>Proteomics rank, B7_43 block vs reference cohorts</h3>${Charts.rankStrip(m.ranks)}<p class="note">${esc(state.boot.patient.proteomics.caveat)}</p>`;
    else if (m.kind === "proteomics" || m.kind === "ihc") html += `<p class="note">Not present in the B7_43 rank comparison table.</p>`;
    if (m.sources?.length) html += `<h3>Sources</h3>${sourcesHtml(m.sources)}`;
    html += `<h3>Graph relationships</h3><div id="nb"><span class="spinner"></span>Loading</div>`;
    showDetail(html);
    if (!m.curie) { $("#nb").textContent = "Not in the graph, so nothing to connect."; return; }
    const r = await api(`/api/neighbours?curie=${encodeURIComponent(m.curie)}&limit=24`);
    Graph.merge(r.nodes, r.edges, m.curie);
    $("#nb").innerHTML = edgesHtml(r.edges, m.curie);
    bindEdgeList($("#nb"));
    if (state.ch === 3 || document.body.classList.contains("is-ws")) {
      integrationScene(0.25);
      const extra = new Set([...Graph.S.nodes, ...r.nodes.map((n) => n.curie)]);
      Graph.S.nodes = extra;
      const em = new Set([...Graph.S.emphasis, m.curie]);
      Graph.setScene({ ...Graph.S, nodes: extra, emphasis: em, labels: new Set([...Graph.S.labels, ...r.nodes.slice(0, 10).map((n) => n.curie)]), rings: { [m.curie]: "#f4efe6" }, reheat: 0.3 });
      Graph.focus(new Set([m.curie, PATIENT, ...r.nodes.map((n) => n.curie)]), 900);
      Graph.pulse(PATIENT, m.curie, { dur: 900 });
      r.edges.forEach((e, i) => Graph.pulse(m.curie, e.src === m.curie ? e.dst : e.src, { delay: 700 + i * 30, dur: 800, color: "#b3a8ff" }));
    }
  }

  function edgesHtml(edges, from) {
    if (!edges.length) return `<p class="note">No relationships for this entity in the ${state.boot.graph_index.source === "full_graph" ? "full graph" : "local index"}.</p>`;
    return `<ul class="edges">${edges.map((e) => {
      const other = e.src === from ? e.dst : e.src, n = Graph.nodes.get(other);
      return `<li><span class="rel">${esc(relLabel(e.rel))}</span> <button data-go="${esc(other)}">${esc(n?.name || other)}</button> ${e.edge_id ? `<span class="id">${esc(e.edge_id)}</span>` : `<span class="tag">kg.csv row</span>`} ${e.origin === "dismech" ? `<span class="tag" style="color:#f4a9c5">DisMech</span>` : ""}
        ${e.pmids?.length ? `<span class="pm">${e.pmids.map((p) => `<a href="https://pubmed.ncbi.nlm.nih.gov/${p.replace("PMID:", "")}/" target="_blank" rel="noopener">${esc(p)}</a>`).join(" ")}${e.quote ? ` ${esc(cleanQuote(e.quote))}` : ""}</span>` : ""}</li>`;
    }).join("")}</ul>`;
  }
  function bindEdgeList(root) {
    root.onclick = (e) => { const b = e.target.closest("[data-go]"); if (b) { const n = Graph.nodes.get(b.dataset.go); if (n) { Graph.selected = n.id; nodeDetail(n); Graph.focus(new Set([n.id]), 700, 2.2); } } };
  }

  /* ---------------------------------------------------------------- chapter 5: investigation */
  function ch5() {
    const b = state.boot, caps = b.capabilities;
    state.marker = state.marker || bestMarker();
    const saved = b.saved_traces.filter((t) => t.real_gnn);
    narr(`${step(5)}<h2>Asking the graph a question</h2>
      <p class="big">We seed the graph with one of his measured tumour markers and let GNN-B retrieve the part of biology most relevant to it.</p>
      <p>GNN-B scores candidate entities with personalised PageRank plus GNN-A's learned link scores, then keeps a small connected subgraph (a prize-collecting Steiner tree). A writer drafts statements that must cite edge IDs from that subgraph, and a strict verifier deletes any statement whose citations were not actually returned.</p>
      <h3>1. Choose a measured marker</h3>
      <div class="list" id="markers">${b.markers.map((m) => `<button class="item ${state.marker?.finding_id === m.finding_id ? "on" : ""}" data-f="${esc(m.finding_id)}"><span class="t">${esc(m.symbol)}</span><span class="v">${esc(m.value)}</span><span class="s">${esc(m.feature)}, ${esc(m.sample_label)}, ${esc(m.sample_date)}</span></button>`).join("")}</div>
      <h3>2. Ask</h3>
      <textarea id="q" aria-label="Research question">${esc(caps.default_question)}</textarea>
      <label class="tog"><input type="checkbox" id="llm" ${caps.llm_ready ? "" : "disabled"}> Write the draft with NVIDIA NIM (${esc(caps.llm_model || "LLM")})${caps.llm_ready ? "" : ", no API key on this machine"}</label>
      <label class="tog"><input type="checkbox" id="inject" checked> Plant one false claim to show the verifier catching it</label>
      <div class="btnrow">
        <button class="primary" id="run" ${caps.gnn_ready ? "" : "disabled"}>Run GNN-B and the verifier</button>
        <button class="ghost" id="srcchk">Verify the source observation only</button>
      </div>
      ${saved.length ? `<div class="btnrow">${saved.slice(0, 4).map((t) => `<button class="ghost" data-replay="${esc(t.name)}">Replay saved run, ${esc(t.finding_id)} ${esc((t.generated_utc || "").slice(0, 16).replace("T", " "))} UTC</button>`).join("")}</div>` : ""}
      <div id="capbox">${capsHtml(caps)}</div>
      <div id="result"></div>`);
    $("#markers").onclick = (e) => { const x = e.target.closest("[data-f]"); if (!x) return; state.marker = b.markers.find((m) => m.finding_id === x.dataset.f); document.querySelectorAll("#markers .item").forEach((i) => i.classList.toggle("on", i === x)); seedScene(); };
    $("#run").onclick = () => doRun("investigate");
    $("#srcchk").onclick = () => doRun("source_check");
    document.querySelectorAll("[data-replay]").forEach((x) => (x.onclick = () => replay(x.dataset.replay)));
    if (state.run && state.run.trace.finding_id === state.marker.finding_id) showRun(state.run, false); else seedScene();
    legend([["#f4efe6", "Alexander Young"], ["#8b7cf6", "retrieved gene"], ["#e0558a", "DisMech edge", "ln"], ["#f4efe6", "cited by a supported claim", "ln"]]);
  }

  function bestMarker() {
    const b = state.boot;
    if (b.capabilities.gnn_ready) return b.markers[0];
    const deg = (m) => Math.max(0, ...b.mapping.filter((x) => x.curie === m.curie).map((x) => x.n_graph_edges || 0));
    return [...b.markers].sort((a, c) => deg(c) - deg(a))[0];
  }

  function capsHtml(c) {
    if (c.gnn_ready) return `<div class="status good">Live GNN-B is ready on the full graph${c.llm_ready ? " and NVIDIA NIM is configured" : ""}. Runs are saved to <code>cache/demo/youngcrc/</code> for replay.</div>`;
    return `<div class="status warn"><b>Live GNN-B is not available on this machine</b>, so nothing is run or faked. Use the source-only verifier${state.boot.saved_traces.some((t) => t.real_gnn) ? " or replay a saved real run" : ""}.
      <details><summary>What is missing</summary>${c.missing_files.length ? `<p>Graph files: ${c.missing_files.map((f) => esc(f.split(/[\\/]/).pop())).join(", ")}.</p>` : ""}${c.missing_packages.length ? `<p>Packages: ${esc(c.missing_packages.join(", "))}.</p>` : ""}<p>${esc(c.how_to_enable)}</p></details></div>`;
  }

  async function seedScene() {
    const m = state.marker; if (!m) return;
    ensurePatientNode();
    const r = await api(`/api/neighbours?curie=${encodeURIComponent(m.curie)}&limit=30`);
    Graph.merge(r.nodes, r.edges, m.curie);
    state.local = r;
    const ids = new Set([PATIENT, m.curie, ...r.nodes.map((n) => n.curie)]);
    Graph.setScene({ nodes: new Set([...stageIds(), ...ids]), dim: new Set([...stageIds()].filter((c) => !ids.has(c))), emphasis: new Set([m.curie, PATIENT]), emphR: 7,
      labels: new Set([PATIENT, m.curie, ...r.nodes.slice(0, 12).map((n) => n.curie)]), rings: { [m.curie]: "#f4efe6" },
      edges: (e) => e.origin !== "patient" || e.t === m.curie, edgeAlpha: (e) => (e.origin === "patient" ? 0.85 : ids.has(e.s) && ids.has(e.t) ? 0.5 : 0.1),
      anchor: AX(), fixed: PF(0.5, 0.55), reheat: 0.5 });
    Graph.focus(ids, 900);
    Graph.pulse(PATIENT, m.curie, { dur: 900 });
    if (!state.run) $("#result").innerHTML = `<p class="note">On screen: ${esc(m.symbol)} and its ${r.edges.length} relationships in the ${r.index === "full_graph" ? "full graph" : "local index"}. This is a lookup, not a retrieval run.</p>`;
  }

  async function doRun(kind) {
    if (state.busy || !state.marker) return;
    state.busy = true;
    const box = $("#result");
    box.innerHTML = pipelineHtml(kind, "running");
    const body = { finding_id: state.marker.finding_id, question: $("#q")?.value, use_llm: $("#llm")?.checked, inject: $("#inject")?.checked ?? true };
    try {
      const r = await api(`/api/${kind}`, body);
      if (!r.ok) { box.innerHTML = r.reason === "gnn_unavailable" ? capsHtml(r.capabilities) : `<div class="status warn">The run did not complete: ${esc(r.error || r.reason)}</div>`; return; }
      state.run = r;
      if (r.trace?.saved_as) state.boot.saved_traces = await api("/api/traces");
      showRun(r, true);
    } catch (e) { box.innerHTML = `<div class="status warn">Request failed: ${esc(e.message)}</div>`; }
    finally { state.busy = false; }
  }

  async function replay(name) {
    const r = await api(`/api/trace?name=${encodeURIComponent(name)}`);
    if (!r.ok) { toast("That saved run could not be opened."); return; }
    state.run = r; state.marker = state.boot.markers.find((m) => m.finding_id === r.trace.finding_id) || state.marker;
    showRun(r, true);
  }

  function pipelineHtml(kind, phase, t) {
    const gnn = kind !== "source_check" && (!t || t.graph_backend === "gnn_pcst");
    const steps = [
      ["Source observation", t ? `${esc(t.observation)}` : "Reading the verified release"],
      ["GNN-B retrieval", gnn ? (t ? `${t.n_graph_nodes} nodes, ${t.n_graph_edges} edges, gnn_pcst on the ${esc(t.graph_variant)} graph` : "Scoring candidates and solving the Steiner tree") : "Not run in this mode"],
      ["Draft", t ? esc(t.mode) : "Writing statements that must cite IDs"],
      ["Strict verifier", t ? `${t.verifier.n_supported} supported, ${t.verifier.n_hypothesis || 0} hypotheses, ${t.verifier.n_removed} removed` : "Checking every citation against this run's evidence"],
    ];
    return `<ol class="chain">${steps.map(([a, b], i) => `<li><b>${a}</b><span class="q">${phase === "running" && i === 1 ? `<span class="spinner"></span>` : ""}${b}</span></li>`).join("")}</ol>`;
  }

  function showRun(r, animate) {
    const t = r.trace, v = r;
    const gnn = t.graph_backend === "gnn_pcst";
    const m = state.boot.markers.find((x) => x.finding_id === t.finding_id);
    let html = `${r.replayed ? `<div class="status">Replayed from a saved real run, generated ${esc(t.generated_utc)} (${esc(r.saved_as || "")}). Not executed just now.</div>` : ""}
      ${!gnn ? `<div class="status warn">Source-only mode: no graph retrieval or LLM ran. The graph on screen is a lookup of ${esc(m?.symbol)}'s relationships, shown for context only.</div>` : ""}
      ${pipelineHtml(gnn ? "investigate" : "source_check", "done", t)}
      <h3>Statements, as the verifier judged them</h3>${claimsHtml(v.claims)}`;
    if (gnn && t.selected_edges?.length) {
      const path = (v.graph.nodes || []).filter((n) => ["pathway", "biological_process"].includes(n.type)).slice(0, 14);
      html += `<h3>Traceable connections</h3><ul class="edges">${t.selected_edges.slice(0, 14).map((e) => `<li><button class="cite" data-edge="${esc(e.edge_id)}">${esc(e.edge_id)}</button> ${esc(e.source)} <span class="rel">${esc(relLabel(e.relation))}</span> ${esc(e.target)} ${e.origin === "dismech" ? `<span class="tag" style="color:#f4a9c5">DisMech</span>` : ""}</li>`).join("")}</ul>`;
      if (path.length) html += `<h3>Pathways and processes in the subgraph</h3><div class="chips">${path.map((n) => `<button class="chip" data-node="${esc(n.curie)}"><i style="background:${Graph.color(n.type)}"></i>${esc(n.name)}</button>`).join("")}</div>`;
    }
    html += `<details style="margin-top:1rem"><summary class="note">Full draft before verification</summary><pre style="white-space:pre-wrap;font-size:.8rem;color:var(--soft)">${esc(t.draft)}</pre></details>`;
    $("#result").innerHTML = html;
    $("#result").onclick = (e) => {
      const c = e.target.closest("[data-edge]"), n = e.target.closest("[data-node]");
      if (c) citeEdge(c.dataset.edge);
      if (n) { const g = Graph.nodes.get(n.dataset.node); if (g) { Graph.selected = g.id; nodeDetail(g); Graph.focus(new Set([g.id]), 700, 2.4); } }
    };
    if (gnn) runScene(v, animate); else seedScene();
  }

  function claimsHtml(claims) {
    if (!claims?.length) return `<p class="note">No statements.</p>`;
    return `<ul class="claims">${claims.map((c) => `<li class="${esc(c.kind)}"><span class="txt">${esc(c.text)}</span> ${c.cited_ids.map((id) => /^E\d+$/.test(id) ? `<button class="cite" data-edge="${esc(id)}">${esc(id)}</button>` : `<span class="cite">${esc(id)}</span>`).join("")}
      ${c.kind === "removed" ? `<span class="why">Removed: ${c.cited_ids.length ? "a cited ID was not returned in this run or names a different entity" : "no supporting edge, PMID or source ID"}.</span>` : c.kind === "hypothesis" ? `<span class="why" style="color:var(--soft)">Kept as a labelled hypothesis.</span>` : ""}</li>`).join("")}</ul>`;
  }

  function runScene(v, animate) {
    const seed = v.seed?.evidence_id ? state.boot.markers.find((m) => m.finding_id === v.trace.finding_id)?.curie : null;
    Graph.merge(v.graph.nodes, v.graph.edges.map((e) => ({ ...e, origin: e.origin })), seed);
    ensurePatientNode();
    const ids = new Set([PATIENT, ...v.graph.nodes.map((n) => n.curie)]);
    const runEdges = new Set(v.graph.edges.map((e) => e.edge_id));
    const cited = new Set(v.claims.filter((c) => c.kind === "supported").flatMap((c) => c.cited_ids));
    Graph.setScene({ nodes: new Set([...stageIds(), ...ids]), dim: new Set([...stageIds()].filter((c) => !ids.has(c))),
      emphasis: new Set([seed, PATIENT].filter(Boolean)), emphR: 7, labels: new Set([PATIENT, ...v.graph.nodes.filter((n) => n.is_seed || ["gene", "pathway", "drug", "disease"].includes(n.type)).slice(0, 22).map((n) => n.curie)]),
      edges: (e) => e.origin !== "patient" || e.t === seed, edgeAlpha: (e) => (e.origin === "patient" ? 0.85 : runEdges.has(e.id) ? 0.7 : 0.05),
      anchor: AX(), fixed: PF(0.5, 0.55), reheat: 0.7 });
    Graph.highlightEdges([...cited], true);
    Graph.focus(ids, 1000);
    if (animate && seed) {
      Graph.pulse(PATIENT, seed, { dur: 800 });
      const adj = new Map(); v.graph.edges.forEach((e) => { (adj.get(e.src) || adj.set(e.src, []).get(e.src)).push(e.dst); (adj.get(e.dst) || adj.set(e.dst, []).get(e.dst)).push(e.src); });
      const seen = new Set([seed]); let frontier = [seed], d = 0;
      while (frontier.length && d < 6) { const nxt = []; frontier.forEach((a) => (adj.get(a) || []).forEach((b) => { if (!seen.has(b)) { seen.add(b); nxt.push(b); Graph.pulse(a, b, { delay: 800 + d * 420, dur: 700, color: "#b3a8ff" }); } })); frontier = nxt; d++; }
    }
  }

  function citeEdge(id) {
    const e = Graph.edges.get(id);
    Graph.highlightEdges([id], true);
    if (!e) { toast(`${id} is not on screen`); return; }
    Graph.focus(new Set([e.s, e.t]), 700, 2.4);
    Graph.pulse(e.s, e.t, { dur: 900, size: 3.5 });
    edgeDetail(e);
  }

  /* ---------------------------------------------------------------- chapter 6: insights */
  function ch6() {
    const r = state.run, p = state.boot.patient;
    const t = r?.trace;
    const m = t ? state.boot.markers.find((x) => x.finding_id === t.finding_id) : state.marker;
    const f = m ? p.findings.find((x) => x.finding_id === m.finding_id) : null;
    const cl = r?.claims || [];
    const n = (k) => cl.filter((c) => c.kind === k).length;
    narr(`${step(6)}<h2>What we can say, and what we cannot</h2>
      <p class="big">The output a clinician sees separates what was measured, what the graph suggests, and what nobody should conclude.</p>
      ${r ? `<div class="verdict"><div class="s"><b>${n("supported")}</b>supported, every ID returned in this run</div><div><b>${n("hypothesis")}</b>labelled hypotheses</div><div class="r"><b>${n("removed")}</b>removed before anyone reads them</div></div>
        <p class="note">From the ${r.replayed ? "replayed" : "latest"} ${t.graph_backend === "gnn_pcst" ? "GNN-B" : "source-only"} run on ${esc(m?.symbol)}${r.replayed ? ` (generated ${esc(t.generated_utc)})` : ""}.</p>
        <div class="btnrow"><button class="primary" id="review">Generate the case review</button></div>`
      : `<div class="status">No run yet in this session. Go back to chapter 5 and run the investigation, or verify the source observation, to fill this page with real output.</div><div class="btnrow"><button class="ghost" id="back5">Back to chapter 5</button></div>`}
      <h3>Why a clinician would open this</h3>
      <ol class="chain">
        <li>One view of reports that are usually scattered across providers, each value tied to its specimen, date and source document.</li>
        <li>Every statement points to a graph edge, a PubMed ID or a released source record, and the link is checked by code, not by trust.</li>
        <li>Statements without that support are removed before a reader sees them, including fluent ones from a large language model.</li>
      </ol>
      <div class="scope"><b>What it is not.</b> Not a treatment recommendation, not a response or survival predictor for Alexander, and not a substitute for review by his care team. The kidney-cancer stage model was never run on his data.</div>
      <div class="btnrow"><button class="primary" id="ws-open">Open the patient workspace</button></div>`);
    $("#review") && ($("#review").onclick = () => openReview(r));
    $("#back5") && ($("#back5").onclick = () => go(4));
    $("#ws-open").onclick = openWorkspace;
    const board = document.createElement("div"); board.className = "board";
    const supported = cl.filter((c) => c.kind === "supported"), hyp = cl.filter((c) => c.kind === "hypothesis"), rem = cl.filter((c) => c.kind === "removed");
    const graphFacts = (t?.selected_edges || []).filter((e) => e.touches_marker).slice(0, 5);
    board.innerHTML = `<section class="obs"><h4>Observed</h4>${f ? `<p><b>${esc(f.feature)} = ${esc(hv(f.value))}${f.unit ? " " + esc(f.unit) : ""}</b>, ${esc(f.sample_label)}, collected ${esc(f.sample_date)}. ${esc(f.assay_label)}.</p>${sourcesHtml(f.sources)}` : "<p>No marker selected.</p>"}
        ${supported.filter((c) => !c.cited_ids.some((i) => /^E\d+/.test(i))).map((c) => `<p class="note">${esc(c.text)}</p>`).join("")}</section>
      <section class="hyp"><h4>Graph-derived, to investigate</h4>${graphFacts.length ? `<ul>${graphFacts.map((e) => `<li>The graph records ${esc(e.source)} ${esc(relLabel(e.relation))} ${esc(e.target)} <span class="cite">${esc(e.edge_id)}</span></li>`).join("")}</ul>` : `<p>No graph retrieval in this run, so no graph associations are offered.</p>`}
        ${hyp.map((c) => `<p>${esc(c.text)}</p>`).join("")}<p class="note">Associations in the graph are general knowledge. None was tested in this patient.</p></section>
      <section class="no"><h4>Not concluded</h4><ul>${rem.map((c) => `<li><del>${esc(c.text)}</del></li>`).join("")}${(t?.limitations || []).map((l) => `<li>${esc(l)}</li>`).join("")}${f ? `<li>Current node status: ${esc(String(f.current_node_status).replace(/_/g, " "))}; the specimen is from ${esc(f.sample_date)}.</li>` : ""}</ul></section>`;
    document.body.appendChild(board);
    if (r && r.trace.graph_backend === "gnn_pcst") runScene(r, false); else if (state.marker) seedScene();
  }

  async function openReview(r) {
    const body = r.saved_as || r.trace.saved_as ? { name: r.saved_as || r.trace.saved_as } : { trace: { ...r.trace, tool_calls: r.trace.tool_calls || [] } };
    const res = await api("/api/review", body.name ? body : { trace: r.trace });
    if (!res.ok) { toast("Could not build the review: " + (res.error || "")); return; }
    $("#modal-body").innerHTML = md(res.markdown);
    $("#modal").hidden = false; $("#modal-x").focus();
    $("#modal-dl").onclick = () => {
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([res.markdown], { type: "text/markdown" }));
      a.download = `case_review_${r.trace.finding_id}_${(r.trace.generated_utc || "").slice(0, 10)}.md`; a.click();
    };
  }

  function md(s) {
    const inline = (x) => esc(x).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/~~(.+?)~~/g, "<del>$1</del>").replace(/\*(.+?)\*/g, "<i>$1</i>").replace(/`(.+?)`/g, "<code>$1</code>")
      .replace(/\((https?:\/\/[^)\s]+)\)/g, '(<a href="$1" target="_blank" rel="noopener">$1</a>)');
    let out = "", inList = false;
    for (const line of s.split("\n")) {
      if (/^- /.test(line)) { if (!inList) { out += "<ul>"; inList = true; } out += `<li>${inline(line.slice(2))}</li>`; continue; }
      if (inList) { out += "</ul>"; inList = false; }
      if (/^# /.test(line)) out += `<h1>${inline(line.slice(2))}</h1>`;
      else if (/^## /.test(line)) out += `<h2>${inline(line.slice(3))}</h2>`;
      else if (line.trim()) out += `<p>${inline(line)}</p>`;
    }
    return out + (inList ? "</ul>" : "");
  }

  /* ---------------------------------------------------------------- detail drawer */
  function showDetail(html) { const d = $("#detail"); d.hidden = false; d.innerHTML = `<button class="x" aria-label="Close details">×</button>${html}`; d.querySelector(".x").onclick = hideDetail; }
  function hideDetail() { $("#detail").hidden = true; }

  async function nodeDetail(n) {
    if (n.id === PATIENT) {
      showDetail(`<h4>Alexander Young</h4><div class="meta">Public case, release ${esc(state.boot.patient.release)}</div><p>Patients are not graph nodes. His measurements attach to genes as dashed lines, each carrying its specimen and source.</p>`);
      return;
    }
    const mapped = state.boot.mapping.filter((m) => m.curie === n.id);
    showDetail(`<h4>${esc(n.name)}</h4><div class="meta">${esc(typeLabel(n.type))}, <span class="id">${esc(n.id)}</span></div>
      ${mapped.length ? `<p>Alexander's data names this entity: ${mapped.map((m) => `<span class="tag ${m.kind}">${kindLabel[m.kind]}</span>${esc(m.label)}`).join(", ")}</p>` : ""}
      <h3>Relationships</h3><div id="nb"><span class="spinner"></span>Loading</div>`);
    const r = await api(`/api/neighbours?curie=${encodeURIComponent(n.id)}&limit=24`);
    Graph.merge(r.nodes, r.edges, n.id);
    const box = $("#nb"); if (!box) return;
    box.innerHTML = edgesHtml(r.edges, n.id) + `<p class="src">From the ${r.index === "full_graph" ? "full merged graph" : "local index of executed retrievals and PrimeKG extracts"}.</p>`;
    bindEdgeList(box);
  }

  function edgeDetail(e) {
    const s = Graph.nodes.get(e.s), t = Graph.nodes.get(e.t), d = e.data || {};
    if (e.origin === "patient") {
      const m = d.mapping;
      showDetail(`<h4>Measurement</h4><div class="meta">${esc(m.label)}</div><p><span class="tag ${m.kind}">${kindLabel[m.kind]}</span>${esc(m.value ?? "")}</p><p class="note">${esc(m.note)}${m.specimen ? ` Specimen ${esc(m.specimen.label)}, ${esc(m.specimen.date || "")}.` : ""}</p>${sourcesHtml(m.sources)}`);
      return;
    }
    showDetail(`<h4>${esc(s?.name)} <span class="rel" style="font-size:1rem;color:var(--mute)">${esc(relLabel(d.rel))}</span> ${esc(t?.name)}</h4>
      <div class="meta">${d.edge_id ? `<span class="id">${esc(d.edge_id)}</span>` : `<span class="tag">kg.csv row</span>`} ${esc(d.origin === "dismech" ? "DisMech curated edge" : "PrimeKG edge")}</div>
      ${d.pmids?.length ? `<p>${d.pmids.map((p) => `<a href="https://pubmed.ncbi.nlm.nih.gov/${p.replace("PMID:", "")}/" target="_blank" rel="noopener">${esc(p)}</a>`).join(" ")}</p>` : ""}
      ${d.quote ? `<p style="font-family:var(--serif);font-size:1.02rem;line-height:1.5">${esc(cleanQuote(d.quote))}</p>` : ""}
      <p class="src">${esc(d.provenance || "")}</p>`);
  }

  /* ---------------------------------------------------------------- workspace */
  function openWorkspace() {
    if (!state.boot) return;
    stopMP();
    document.body.classList.remove("is-intro"); document.body.classList.add("is-ws");
    document.querySelectorAll(".board,.caption,.legend").forEach((x) => x.remove());
    $("#band").hidden = true; hideDetail();
    $("#ws").hidden = false; $("#to-tour").hidden = false; $("#skip").hidden = true;
    history.replaceState(null, "", "#workspace");
    document.querySelectorAll(".rail button").forEach((b) => b.classList.remove("on"));
    renderWs();
    integrationScene(0.5);
    Graph.S.anchor = { x: 0.3, y: 0.5 };
    Graph.setScene({ ...Graph.S, anchor: { x: 0.3, y: 0.5 }, fixed: patientFixed(0.14, 0.5), reheat: 0.6 });
    Graph.reset();
    legend([["#f4efe6", "Alexander Young"], ["#f4efe6", "his measurement", "ln dash"], ["#8b7cf6", "gene"], ["#e0558a", "DisMech edge", "ln"]]);
    document.querySelector(".legend").style.right = "calc(min(620px, 44vw) + 1.4rem)";
  }
  function closeWorkspace(silent) {
    document.body.classList.remove("is-ws"); $("#ws").hidden = true; $("#to-tour").hidden = true; $("#skip").hidden = false;
  }

  function renderWs() {
    const p = state.boot.patient, tabs = [["molecular", "Molecular"], ["timeline", "Timeline"], ["graph", "Graph"], ["agent", "Agent"], ["verifier", "Verifier"], ["sources", "Sources"]];
    $("#ws").innerHTML = `<div class="ws-head"><h2>Alexander Young</h2><p>Metastatic rectal adenocarcinoma, public patient-led dataset, release ${esc(p.release)} (${p.data_source === "live" ? "live" : "verified offline copy"}). Research use only.</p></div>
      <div class="tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" data-tab="${k}" class="${state.wsTab === k ? "on" : ""}">${l}</button>`).join("")}</div><div class="ws-body" id="wsb"></div>`;
    $("#ws .tabs").onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b) { state.wsTab = b.dataset.tab; renderWs(); } };
    const W = $("#wsb");
    const T = state.wsTab;
    if (T === "molecular") {
      W.innerHTML = `<h3>Source-linked findings</h3><table class="t"><thead><tr><th>Finding</th><th>Value</th><th>Specimen</th><th>Date</th></tr></thead><tbody>${p.findings.map((f, i) => `<tr class="row" data-f="${i}"><td>${esc(f.feature)}</td><td>${esc(hv(f.value))}${f.unit ? " " + esc(f.unit) : ""}</td><td>${esc(f.sample_label)}</td><td>${esc(f.sample_date || "")}</td></tr>`).join("")}</tbody></table>
        <h3>Proteomics panels vs ${esc(p.proteomics.primary_reference.replace("_", " "))}</h3><table class="t"><thead><tr><th>Panel</th><th>Markers</th><th>Median rank shift</th></tr></thead><tbody>${p.proteomics.panels.filter((x) => x.reference === p.proteomics.primary_reference).map((x) => `<tr><td>${esc(x.panel.replace(/_/g, " "))}</td><td>${esc(x.markers.join(", "))}</td><td class="n">${x.median_rank_shift_points?.toFixed(1)}</td></tr>`).join("")}</tbody></table>
        <p class="note">${esc(p.proteomics.caveat)}</p>
        <h3>Research-audit loci</h3><table class="t"><thead><tr><th>Locus</th><th>Annotation</th><th>In graph</th></tr></thead><tbody>${p.variants.map((v) => { const m = state.boot.mapping.find((x) => x.measurement_id === v.variant_id); return `<tr class="row" data-v="${esc(v.variant_id)}"><td>${esc(v.gene_label)}</td><td>${esc(v.protein_annotation || "")}</td><td>${m ? (m.n_graph_edges ? `${m.n_graph_edges} edges` : "entity only") : "no symbol"}</td></tr>`; }).join("")}</tbody></table>
        <p class="note">These loci come from a research audit; the release attaches no specimen and makes no clinical call.</p>`;
      W.onclick = (e) => {
        const r = e.target.closest("[data-f]"), v = e.target.closest("[data-v]");
        if (r) { const f = p.findings[+r.dataset.f]; const m = state.boot.mapping.find((x) => x.measurement_id === f.finding_id); if (m) selectMapping(m); else selectSpecimen(f.sample_id); }
        if (v) { const m = state.boot.mapping.find((x) => x.measurement_id === v.dataset.v); if (m) selectMapping(m); }
      };
    } else if (T === "timeline") {
      W.innerHTML = `<div style="overflow-x:auto">${Charts.timeline(p.timeline, p.specimens, { w: 900, h: 190 })}</div>
        <table class="t"><thead><tr><th>Date</th><th>Event</th><th>Status</th></tr></thead><tbody>${p.timeline.map((e) => `<tr class="row" data-ev="${esc(e.id)}"><td>${esc(e.date)}</td><td>${esc(e.title)}</td><td>${esc(String(e.status).replace(/_/g, " "))}</td></tr>`).join("")}</tbody></table>`;
      W.onclick = (e) => { const r = e.target.closest("[data-ev]"), s = e.target.closest("[data-sp]"); if (r) selectEvent(r.dataset.ev); if (s) selectSpecimen(s.dataset.sp); };
    } else if (T === "graph") {
      W.innerHTML = `<p>Every measurement that names a gene, and where it lands in the graph. Click one to expand its relationships on the canvas.</p><div class="chips" id="kindchips">${["ihc", "clinical_report", "variant", "proteomics"].map((k) => `<button class="chip ${state.kindsOn.has(k) ? "on" : "off"}" data-kind="${k}">${kindLabel[k]}</button>`).join("")}</div><div class="list" id="maplist"></div>
        <p class="note">${esc(state.boot.graph_index.description)}</p>`;
      $("#kindchips").onclick = (e) => { const b = e.target.closest("[data-kind]"); if (!b) return; const k = b.dataset.kind; state.kindsOn.has(k) ? state.kindsOn.delete(k) : state.kindsOn.add(k); renderWs(); integrationScene(0.3); Graph.setScene({ ...Graph.S, anchor: { x: 0.3, y: 0.5 }, fixed: patientFixed(0.14, 0.5) }); };
      renderMapList();
    } else if (T === "agent") {
      const caps = state.boot.capabilities;
      state.marker = state.marker || bestMarker();
      W.innerHTML = `<p>Seed GNN-B with a measured marker, draft cited statements, and run the strict verifier. Saved runs replay with their timestamp.</p>
        <div class="list" id="markers">${state.boot.markers.map((m) => `<button class="item ${state.marker?.finding_id === m.finding_id ? "on" : ""}" data-f="${esc(m.finding_id)}"><span class="t">${esc(m.symbol)}</span><span class="v">${esc(m.value)}</span><span class="s">${esc(m.feature)}, ${esc(m.sample_label)} ${esc(m.sample_date)}</span></button>`).join("")}</div>
        <textarea id="q">${esc(caps.default_question)}</textarea>
        <label class="tog"><input type="checkbox" id="llm" ${caps.llm_ready ? "" : "disabled"}> Draft with NVIDIA NIM${caps.llm_ready ? "" : " (no API key)"}</label>
        <label class="tog"><input type="checkbox" id="inject" checked> Plant one false claim for the verifier</label>
        <div class="btnrow"><button class="primary" id="run" ${caps.gnn_ready ? "" : "disabled"}>Run GNN-B and the verifier</button><button class="ghost" id="srcchk">Verify the source only</button></div>
        ${state.boot.saved_traces.filter((t) => t.real_gnn).slice(0, 6).map((t) => `<button class="ghost" style="margin:.2rem .2rem 0 0" data-replay="${esc(t.name)}">Replay ${esc(t.finding_id)} ${esc((t.generated_utc || "").slice(0, 16).replace("T", " "))}</button>`).join("")}
        <div id="capbox">${capsHtml(caps)}</div><div id="result"></div>`;
      $("#markers").onclick = (e) => { const x = e.target.closest("[data-f]"); if (!x) return; state.marker = state.boot.markers.find((m) => m.finding_id === x.dataset.f); renderWs(); };
      $("#run").onclick = () => doRun("investigate"); $("#srcchk").onclick = () => doRun("source_check");
      W.querySelectorAll("[data-replay]").forEach((x) => (x.onclick = () => replay(x.dataset.replay)));
      if (state.run) showRun(state.run, false);
    } else if (T === "verifier") {
      const r = state.run;
      W.innerHTML = r ? `<p>${esc(r.trace.mode)}, ${esc(r.trace.generated_utc)}. Supported means every cited ID was returned in this run and names an entity in the sentence; it is not a statement of biological truth.</p>${claimsHtml(r.claims)}<div class="btnrow"><button class="primary" id="review">Generate the case review</button></div>`
        : `<div class="status">No run yet. Use the Agent tab.</div>`;
      $("#review") && ($("#review").onclick = () => openReview(r));
      W.onclick = (e) => { const c = e.target.closest("[data-edge]"); if (c) citeEdge(c.dataset.edge); };
    } else if (T === "sources") {
      const gi = state.boot.graph_index;
      W.innerHTML = `<h3>Patient data</h3><p>Alexander Young, Open Cancer Data, <a href="${esc(p.base_url)}" target="_blank" rel="noopener">youngcrc.com</a>, release ${esc(p.release)}, loaded ${p.data_source === "live" ? "live" : "from the hash-verified offline copy in app/tour/snapshot"}. Curated data CC BY 4.0; third-party reports keep their own rights.</p>
        ${p.load_errors.length ? `<p class="note">Live load: ${esc(p.load_errors.join("; "))}</p>` : ""}
        <table class="t"><thead><tr><th>File</th><th>SHA-256 (checked)</th></tr></thead><tbody>${Object.entries(p.sha256).map(([f, h]) => `<tr><td>${esc(f)}</td><td><span class="id">${esc(h.slice(0, 20))}...</span></td></tr>`).join("")}</tbody></table>
        <h3>Knowledge graph</h3><p>${esc(gi.description)} Backend: ${esc(gi.source)}, ${fmt(gi.n_nodes)} nodes, ${fmt(gi.n_edges)} edges.</p>
        <h3>Models</h3><p>GNN-A and GNN-B results are from CPTAC clear-cell renal carcinoma and graph link prediction (<code>models/results</code>, <code>eval/results</code>). They are not colorectal evaluations. The kidney-cancer stage model is never run on this case.</p>
        <h3>Other team views</h3><p>CPTAC kidney-cancer workspace: <code>streamlit run app/streamlit_app.py</code>. Original Alexander Young page: <code>streamlit run app/pages/1_Alexander_Young.py</code>.</p>`;
    }
  }

  /* ---------------------------------------------------------------- notes */
  function renderNotes() {
    const n = $("#notes");
    if (!state.notes || state.ch < 0) { n.hidden = true; return; }
    n.hidden = false;
    n.innerHTML = `<h5>${state.ch + 1}. ${esc(CH[state.ch].title)}</h5><ul>${CH[state.ch].notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul><p class="t">Press N to hide.</p>`;
  }

  /* ---------------------------------------------------------------- wiring */
  function wire() {
    $("#begin").onclick = () => go(0);
    $("#skip").onclick = openWorkspace; $("#skip2").onclick = openWorkspace;
    $("#to-tour").onclick = () => go(Math.max(0, state.ch));
    $("#brand").onclick = () => { closeWorkspace(); hideDetail(); stopMP(); document.body.classList.add("is-intro"); $("#band").hidden = true; document.querySelectorAll(".board,.legend").forEach((x) => x.remove()); state.ch = -1; history.replaceState(null, "", "#"); introScene(); Graph.reset(); renderNotes(); };
    $("#prev").onclick = () => go(state.ch - 1);
    $("#next").onclick = () => (state.ch === CH.length - 1 ? openWorkspace() : go(state.ch + 1));
    $("#rail").onclick = (e) => { const b = e.target.closest("[data-ch]"); if (b) go(+b.dataset.ch); };
    $("#modal-x").onclick = () => ($("#modal").hidden = true);
    $("#modal").onclick = (e) => { if (e.target.id === "modal") $("#modal").hidden = true; };
    Graph.on("select", (n) => {
      if (!n) { hideDetail(); return; }
      if (state.ch === 1) { state.mpNode = n.id; $("#mp-on") && ($("#mp-on").textContent = `Selected: ${n.name}`); }
      const m = state.boot.mapping.find((x) => x.curie === n.id);
      if (m && (state.ch === 3 || document.body.classList.contains("is-ws"))) selectMapping(m); else nodeDetail(n);
    });
    Graph.on("selectEdge", (e) => { Graph.highlightEdges([e.id], true); edgeDetail(e); });
    document.addEventListener("keydown", (e) => {
      if (e.target.closest("textarea,input,select")) return;
      if (e.key === "Escape") { if (!$("#modal").hidden) $("#modal").hidden = true; else hideDetail(); }
      if (document.body.classList.contains("is-ws") || !$("#modal").hidden) return;
      if (e.key === "ArrowRight" || e.key === "PageDown") { state.ch < 0 ? go(0) : state.ch === CH.length - 1 ? openWorkspace() : go(state.ch + 1); }
      if (e.key === "ArrowLeft" || e.key === "PageUp") go(state.ch - 1);
      if (e.key.toLowerCase() === "n") { state.notes = !state.notes; renderNotes(); }
    });
  }

  boot();
})();
