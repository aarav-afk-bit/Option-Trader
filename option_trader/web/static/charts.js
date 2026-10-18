/* Charts tab: OI by strike, change in OI, IV smile, IV term structure, intraday tracker. */
"use strict";

(function () {
  const { api, qs } = window.App;
  const $ = (s) => document.querySelector(s);
  const cfg = { responsive: true, displaylogo: false };
  let termData = null;

  function spotLine(ch) {
    return { type: "line", x0: ch.spot, x1: ch.spot, yref: "paper", y0: 0, y1: 1, line: { color: App.colors().accent, dash: "dot", width: 1 } };
  }

  function drawChain(ch) {
    const c = App.colors();
    const ks = ch.rows.map((r) => r.strike);
    const series = (side, key, scale = 1) => ch.rows.map((r) => (r[side] && r[side][key] != null ? r[side][key] * scale : null));
    // Calls = series 1 (cyan), puts = series 2 (amber): a colour-blind-safe pair, always with a legend.
    Plotly.react("ch-oi", [
      { x: ks, y: series("call", "oi"), name: "Call OI", type: "bar", marker: { color: c.s1 } },
      { x: ks, y: series("put", "oi"), name: "Put OI", type: "bar", marker: { color: c.s2 } },
    ], App.plotLayout({ barmode: "group", shapes: [spotLine(ch)], xaxis: { title: "Strike" }, yaxis: { title: "Open interest" } }), cfg);
    Plotly.react("ch-oichg", [
      { x: ks, y: series("call", "oi_change"), name: "Call ΔOI", type: "bar", marker: { color: c.s1 } },
      { x: ks, y: series("put", "oi_change"), name: "Put ΔOI", type: "bar", marker: { color: c.s2 } },
    ], App.plotLayout({ barmode: "group", shapes: [spotLine(ch)], xaxis: { title: "Strike" }, yaxis: { title: "Change in OI" } }), cfg);
    Plotly.react("ch-smile", [
      { x: ks, y: series("call", "iv", 100), name: "Call IV %", mode: "lines+markers", line: { color: c.s1, width: 2 }, marker: { size: 8 }, connectgaps: false },
      { x: ks, y: series("put", "iv", 100), name: "Put IV %", mode: "lines+markers", line: { color: c.s2, width: 2 }, marker: { size: 8 }, connectgaps: false },
    ], App.plotLayout({ shapes: [spotLine(ch)], xaxis: { title: "Strike" }, yaxis: { title: "IV %" } }), cfg);
  }

  /** Stacked panels sharing one x-axis (never two y-scales on one plot). */
  function stacked(n, gap = 0.08) {
    const h = (1 - gap * (n - 1)) / n;
    const out = {};
    for (let i = 0; i < n; i++) {
      const top = 1 - i * (h + gap);
      out[i === 0 ? "yaxis" : `yaxis${i + 1}`] = { domain: [top - h, top] };
    }
    return out;
  }

  function drawTerm(d) {
    const c = App.colors();
    const x = d.points.map((p) => `${p.expiry} (${Math.round(p.days)}d)`);
    const ax = stacked(2);
    ax.yaxis.title = "ATM IV %"; ax.yaxis2.title = "1σ move";
    Plotly.react("ch-term", [
      { x, y: d.points.map((p) => (p.atm_iv != null ? p.atm_iv * 100 : null)), name: "ATM IV %", mode: "lines+markers", line: { color: c.s1, width: 2 }, marker: { size: 8 } },
      { x, y: d.points.map((p) => p.one_sd_move), name: "1σ move (₹)", mode: "lines+markers", xaxis: "x", yaxis: "y2", line: { color: c.s3, width: 2 }, marker: { size: 8 } },
    ], App.plotLayout({ ...ax, margin: { l: 60, r: 20, t: 10, b: 60 }, xaxis: { anchor: "y2" } }), cfg);
  }

  async function loadTerm() {
    const btn = $("#load-term");
    btn.disabled = true; btn.textContent = "Loading…";
    try {
      const p = App.params();
      termData = await api("/api/analytics/term-structure?" + qs({ symbol: p.symbol, source: p.source, count: 5 }));
      $("#ch-term").innerHTML = "";
      drawTerm(termData);
      if (termData.errors.length) App.showBanner("Some expiries could not be fetched: " + termData.errors.join(" | "));
    } catch (e) {
      $("#ch-term").innerHTML = `<p class="muted">Term structure unavailable: ${e.message}</p>`;
    } finally {
      btn.disabled = false; btn.textContent = "Reload";
    }
  }

  async function drawIntraday() {
    if (!App.chain) return;
    let d;
    try {
      d = await api("/api/analytics/intraday?" + qs({ symbol: App.chain.symbol, expiry: App.chain.expiry }));
    } catch (e) { return; }
    const t = d.points.map((p) => new Date(p.t)); // Date objects plot in local time
    const c = App.colors();
    const ax = stacked(3, 0.06);
    ax.yaxis.title = "Spot"; ax.yaxis2.title = "PCR"; ax.yaxis3.title = "ATM IV %";
    Plotly.react("ch-intraday", [
      { x: t, y: d.points.map((p) => p.spot), name: "Spot", mode: "lines+markers", line: { color: c.s1, width: 2 }, marker: { size: 6 } },
      { x: t, y: d.points.map((p) => p.pcr_oi), name: "PCR (OI)", mode: "lines+markers", yaxis: "y2", line: { color: c.s2, width: 2 }, marker: { size: 6 } },
      { x: t, y: d.points.map((p) => (p.atm_iv != null ? p.atm_iv * 100 : null)), name: "ATM IV %", mode: "lines+markers", yaxis: "y3", line: { color: c.s3, width: 2 }, marker: { size: 6 } },
    ], App.plotLayout({ ...ax, xaxis: { type: "date", anchor: "y3" }, margin: { l: 60, r: 20, t: 10, b: 45 } }), cfg);
  }

  function redrawAll() {
    if (App.activeTab() !== "charts" || !App.chain) return;
    drawChain(App.chain);
    drawIntraday();
    if (termData) drawTerm(termData);
  }

  $("#load-term").addEventListener("click", loadTerm);
  App.onChain(() => redrawAll());
  window.addEventListener("themechange", redrawAll);
  const prevTabShown = App.onTabShown;
  App.onTabShown = (name) => {
    if (prevTabShown) prevTabShown(name);
    if (name === "charts") requestAnimationFrame(redrawAll);
  };
  // A new symbol invalidates the loaded term structure.
  document.querySelector("#controls").addEventListener("submit", () => {
    termData = null;
    $("#ch-term").innerHTML = '<p class="muted">Fetches the next expiries\' live chains. Press Load.</p>';
  });
})();
