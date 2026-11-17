/* Positioning: dealer gamma exposure and unusual options activity (Charts tab). */
"use strict";

(function () {
  const { api } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v == null ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const money = (v) => {
    if (v == null) return "–";
    const a = Math.abs(v), s = v < 0 ? "-" : "";
    return a >= 1e9 ? `${s}$${(a / 1e9).toFixed(2)}B` : a >= 1e6 ? `${s}$${(a / 1e6).toFixed(1)}M` : a >= 1e3 ? `${s}$${(a / 1e3).toFixed(0)}K` : `${s}$${a.toFixed(0)}`;
  };
  const kv = (label, v, cls = "") => `<div class="kv"><small>${label}</small><b class="${cls}">${v}</b></div>`;

  async function loadGex() {
    if (!App.chain) { App.showBanner("Load a live option chain first."); return; }
    const el = $("#ch-gex");
    $("#gex-summary").innerHTML = '<span class="muted">Loading live chains…</span>';
    let d;
    try {
      d = await api("/api/analytics/gamma?" + new URLSearchParams({ symbol: App.chain.symbol, expiry: App.chain.expiry, source: App.chain.source, expiries: $("#gex-exp").value }));
    } catch (e) {
      App.chartMessage(el, `<p class="muted">Gamma exposure unavailable: ${esc(e.message)}</p>`);
      $("#gex-summary").innerHTML = "";
      return;
    }
    $("#gex-summary").innerHTML = [
      kv("Total GEX", money(d.total_gex) + " / 1%", d.total_gex >= 0 ? "pos" : "neg"),
      kv("Regime", d.regime === "positive" ? "Long gamma (damping)" : "Short gamma (amplifying)", d.total_gex >= 0 ? "pos" : "neg"),
      kv("Zero gamma", d.zero_gamma ? fmt(d.zero_gamma) : "outside ±12%"),
      kv("Call wall", fmt(d.call_wall, d.call_wall % 1 ? 1 : 0)), kv("Put wall", fmt(d.put_wall, d.put_wall % 1 ? 1 : 0)),
      kv("Expiries", d.expiries.length), kv("Contracts", d.contracts),
    ].join("") + `<p class="muted" style="flex-basis:100%;margin:0">${esc(d.explanation)}</p>`;

    const c = App.colors();
    const lo = d.spot * 0.88, hi = d.spot * 1.12;
    const st = d.strikes.filter((s) => s.strike >= lo && s.strike <= hi);
    const shapes = [{ type: "line", x0: d.spot, x1: d.spot, yref: "paper", y0: 0, y1: 1, line: { color: c.text, dash: "dot" } }];
    const notes = [{ x: d.spot, yref: "paper", y: 1, text: `Spot ${fmt(d.spot)}`, showarrow: false, yanchor: "bottom", font: { size: 11, color: c.text } }];
    if (d.zero_gamma) {
      shapes.push({ type: "line", x0: d.zero_gamma, x1: d.zero_gamma, yref: "paper", y0: 0, y1: 1, line: { color: c.accent, dash: "dash" } });
      notes.push({ x: d.zero_gamma, yref: "paper", y: 0.92, text: `Zero gamma ${fmt(d.zero_gamma)}`, showarrow: false, xanchor: "left", font: { size: 11, color: c.accent } });
    }
    App.plot(el, [
      { type: "bar", x: st.map((s) => s.strike), y: st.map((s) => s.call), name: "Calls (dealers long)", marker: { color: c.profit }, hovertemplate: "%{x}: %{y:$,.0f}<extra>calls</extra>" },
      { type: "bar", x: st.map((s) => s.strike), y: st.map((s) => s.put), name: "Puts (dealers short)", marker: { color: c.loss }, hovertemplate: "%{x}: %{y:$,.0f}<extra>puts</extra>" },
      { type: "scatter", mode: "lines", x: d.profile.map((p) => p.spot), y: d.profile.map((p) => p.gex), name: "Total GEX if spot moved here", yaxis: "y2", line: { color: c.accent, width: 2 } },
    ], App.plotLayout({
      barmode: "relative", hovermode: "closest", shapes, annotations: notes,
      xaxis: { title: "Strike / spot", range: [lo, hi] },
      yaxis: { title: "GEX by strike ($ per 1% move)" },
      yaxis2: { title: "Total GEX", overlaying: "y", side: "right", showgrid: false, zeroline: true },
      margin: { l: 70, r: 70, t: 20, b: 45 },
    }));
  }

  async function loadFlow() {
    if (!App.chain) { App.showBanner("Load a live option chain first."); return; }
    $("#flow-summary").textContent = "Scanning…";
    let d;
    try {
      d = await api("/api/analytics/flow?" + new URLSearchParams({ symbol: App.chain.symbol, expiry: App.chain.expiry, source: App.chain.source }));
    } catch (e) {
      $("#flow-summary").textContent = `Scan failed: ${e.message}`;
      return;
    }
    const f = d.filters;
    $("#flow-summary").innerHTML = `Scanned ${d.scanned} traded contracts in ${d.expiries.length} expiries. Premium traded: calls ${money(d.call_premium)}, puts ${money(d.put_premium)}` +
      (d.premium_pcr != null ? ` (put/call ${d.premium_pcr.toFixed(2)})` : "") +
      `. Showing contracts with ≥${f.min_volume} volume, volume ≥ ${f.min_vol_oi}× open interest and ≥ ${money(f.min_premium)} premium.`;
    const sideTxt = { ask: "at ask", "near ask": "near ask", bid: "at bid", "near bid": "near bid", mid: "mid", unknown: "–" };
    $("#flow-table tbody").innerHTML = d.hits.map((h) => `<tr>
        <td>${esc(h.expiry)}</td><td>${fmt(h.strike, h.strike % 1 ? 1 : 0)}</td><td>${h.type === "CE" ? "Call" : "Put"}${h.otm ? ' <span class="muted">OTM</span>' : ""}</td>
        <td>${fmt(h.volume, 0)}</td><td>${fmt(h.oi, 0)}</td><td>${h.vol_oi == null ? "new" : h.vol_oi.toFixed(1) + "×"}</td>
        <td>$${fmt(h.price)}</td><td><b>${money(h.premium)}</b></td><td>${sideTxt[h.side] || h.side}</td>
        <td><span class="pill ${h.sentiment}">${esc(h.sentiment)}</span></td></tr>`).join("")
      || '<tr><td colspan="10" class="muted">Nothing unusual right now: no contract passes all three filters.</td></tr>';
  }

  $("#load-gex").addEventListener("click", loadGex);
  $("#gex-exp").addEventListener("change", loadGex);
  $("#load-flow").addEventListener("click", loadFlow);
  App.onChain((ch) => {
    // A different symbol invalidates the panels; same-symbol refreshes keep them until reloaded.
    const key = ch && ch.symbol;
    if (key !== App._posSym) {
      App._posSym = key;
      $("#gex-summary").innerHTML = "";
      $("#flow-table tbody").innerHTML = "";
    }
  });
})();
