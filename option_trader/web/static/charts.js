/* Charts tab: OI by strike, change in OI, IV smile, IV term structure, intraday tracker. */
"use strict";

(function () {
  const { api, qs } = window.App;
  const $ = (s) => document.querySelector(s);
  const CALL = "#f05252", PUT = "#22c55e";
  const cfg = { responsive: true, displaylogo: false };
  let termData = null;

  function spotLine(ch) {
    return { type: "line", x0: ch.spot, x1: ch.spot, yref: "paper", y0: 0, y1: 1, line: { color: "#94a3b8", dash: "dot", width: 1 } };
  }

  function drawChain(ch) {
    const ks = ch.rows.map((r) => r.strike);
    const series = (side, key, scale = 1) => ch.rows.map((r) => (r[side] && r[side][key] != null ? r[side][key] * scale : null));
    Plotly.react("ch-oi", [
      { x: ks, y: series("call", "oi"), name: "Call OI", type: "bar", marker: { color: CALL } },
      { x: ks, y: series("put", "oi"), name: "Put OI", type: "bar", marker: { color: PUT } },
    ], App.plotLayout({ barmode: "group", shapes: [spotLine(ch)], xaxis: { title: "Strike" } }), cfg);
    Plotly.react("ch-oichg", [
      { x: ks, y: series("call", "oi_change"), name: "Call ΔOI", type: "bar", marker: { color: CALL } },
      { x: ks, y: series("put", "oi_change"), name: "Put ΔOI", type: "bar", marker: { color: PUT } },
    ], App.plotLayout({ barmode: "group", shapes: [spotLine(ch)], xaxis: { title: "Strike" } }), cfg);
    Plotly.react("ch-smile", [
      { x: ks, y: series("call", "iv", 100), name: "Call IV %", mode: "lines+markers", line: { color: CALL }, connectgaps: false },
      { x: ks, y: series("put", "iv", 100), name: "Put IV %", mode: "lines+markers", line: { color: PUT }, connectgaps: false },
    ], App.plotLayout({ shapes: [spotLine(ch)], xaxis: { title: "Strike" }, yaxis: { title: "IV %" } }), cfg);
  }

  function drawTerm(d) {
    const x = d.points.map((p) => `${p.expiry} (${Math.round(p.days)}d)`);
    Plotly.react("ch-term", [
      { x, y: d.points.map((p) => (p.atm_iv != null ? p.atm_iv * 100 : null)), name: "ATM IV %", mode: "lines+markers", line: { color: "#3b82f6" } },
      { x, y: d.points.map((p) => p.one_sd_move), name: "1σ move", mode: "lines+markers", yaxis: "y2", line: { color: "#f59e0b", dash: "dash" } },
    ], App.plotLayout({ yaxis: { title: "ATM IV %" }, yaxis2: { title: "1σ move", overlaying: "y", side: "right", showgrid: false },
      margin: { l: 60, r: 60, t: 10, b: 60 } }), cfg);
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
    Plotly.react("ch-intraday", [
      { x: t, y: d.points.map((p) => p.spot), name: "Spot", mode: "lines+markers", line: { color: "#3b82f6" } },
      { x: t, y: d.points.map((p) => p.pcr_oi), name: "PCR (OI)", mode: "lines+markers", yaxis: "y2", line: { color: "#f59e0b" } },
      { x: t, y: d.points.map((p) => (p.atm_iv != null ? p.atm_iv * 100 : null)), name: "ATM IV %", mode: "lines", yaxis: "y3", line: { color: "#a855f7", dash: "dot" } },
    ], App.plotLayout({
      xaxis: { type: "date", domain: [0, 0.88] },
      yaxis: { title: "Spot" },
      yaxis2: { title: "PCR", overlaying: "y", side: "right", showgrid: false },
      yaxis3: { title: "IV %", overlaying: "y", side: "right", position: 0.96, anchor: "free", showgrid: false },
      margin: { l: 60, r: 70, t: 10, b: 45 },
    }), cfg);
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
