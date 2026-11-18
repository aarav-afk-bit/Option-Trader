/* Valuation lab: one live contract priced with every model, next to the market. */
"use strict";

(function () {
  const { api } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const usd = (v, d = 2) => (v == null ? "–" : (v < 0 ? "-$" : "$") + fmt(Math.abs(v), d));
  let lastKey = null;

  function fillStrikes() {
    const ch = App.chain;
    const sel = $("#vl-strike");
    if (!ch) { sel.innerHTML = ""; return; }
    const key = `${ch.symbol}|${ch.expiry}`;
    if (key === lastKey && sel.options.length) return;
    const keep = Number(sel.value);
    lastKey = key;
    sel.innerHTML = ch.rows.map((r) => `<option value="${r.strike}">${fmt(r.strike, r.strike % 1 ? 1 : 0)}</option>`).join("");
    const strikes = ch.rows.map((r) => r.strike);
    sel.value = strikes.includes(keep) ? keep : ch.atm_strike;
  }
  App.onChain(fillStrikes);

  /** Open the lab on a given contract (used from other panels). */
  App.openLab = (strike, kind) => {
    App.switchTab("valuation");
    fillStrikes();
    if (strike != null) $("#vl-strike").value = strike;
    if (kind) $("#vl-kind").value = kind;
    $("#vl-form").requestSubmit();
  };

  function params() {
    const ch = App.chain;
    const p = { symbol: ch.symbol, expiry: ch.expiry, strike: $("#vl-strike").value, kind: $("#vl-kind").value, source: ch.source };
    const s = $("#vl-sigma").value;
    if (s !== "") p.sigma = Number(s) / 100;
    return p;
  }

  $("#vl-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!App.chain) { App.showBanner("Load a live option chain first."); return; }
    const p = params();
    $("#vl-status").textContent = "Pricing with 11 models…";
    let d;
    try {
      d = await api("/api/valuation/contract?" + new URLSearchParams({ ...p, paths: $("#vl-paths").value }));
    } catch (e) {
      $("#vl-status").textContent = `Valuation failed: ${e.message}`;
      return;
    }
    const total = d.models.reduce((a, m) => a + m.ms, 0);
    $("#vl-status").textContent = `${d.symbol} ${d.expiry} ${fmt(d.strike, d.strike % 1 ? 1 : 0)} ${d.kind === "CE" ? "call" : "put"} · ${d.source.toUpperCase()} data from ${new Date(d.fetched_at).toLocaleTimeString()} · ${Math.round(total)} ms`;
    renderInputs(d);
    renderTable(d);
    renderBars(d);
    loadSmile(p);
    loadConvergence(p);
  });

  function renderInputs(d) {
    const i = d.inputs, m = d.market;
    const kv = (label, v, title = "") => `<div class="kv" title="${esc(title)}"><small>${label}</small><b>${v}</b></div>`;
    $("#vl-inputs").innerHTML = [
      kv("Spot", usd(i.spot)), kv("Forward", usd(i.forward), "From put-call parity on the live quotes"),
      kv("Days left", fmt(i.days, 1)), kv("Rate", (i.rate * 100).toFixed(2) + "%", i.rate_source || "configured rate"),
      kv("Carry / dividend", (i.carry * 100).toFixed(2) + "%", "Implied by the forward"),
      kv("Volatility", (i.sigma * 100).toFixed(2) + "%", i.sigma_source),
      kv("Bid / Ask", `${usd(m.bid)} / ${usd(m.ask)}`), kv("Mid", usd(m.mid)),
      kv("Exercise", d.exercise === "american" ? "American" : "European", d.exercise === "american" ? "Equity/ETF options can be exercised any day" : "Index options exercise only at expiry"),
      kv("Early-exercise value", usd(d.early_exercise_premium, 3), "American (tree) minus European (Black-Scholes) at the same volatility"),
      d.american_iv ? kv("American IV", (d.american_iv * 100).toFixed(2) + "%", "Volatility that makes Barone-Adesi-Whaley match the market price") : "",
    ].join("");
  }

  function renderTable(d) {
    const rows = d.models.map((m) => {
      const fam = m.american ? `${esc(m.family)} · American` : esc(m.family);
      const vs = m.vs_mid == null ? "–" : `<span class="${Math.abs(m.vs_mid) < 0.005 ? "" : m.vs_mid > 0 ? "pos" : "neg"}">${m.vs_mid > 0 ? "+" : ""}${fmt(m.vs_mid, 3)}</span>`;
      const inside = m.in_spread == null ? "–" : m.in_spread ? '<span class="pill bullish">yes</span>' : '<span class="pill bearish">no</span>';
      const ci = m.ci95 ? `${fmt(m.ci95[0], 3)} – ${fmt(m.ci95[1], 3)}` : "";
      const note = [m.note, m.applies ? "" : "European contract: shown for reference"].filter(Boolean).join(" · ");
      return `<tr class="${m.applies ? "" : "na"}" title="${esc(m.description)}"><td>${esc(m.name)}</td><td class="muted">${fam}</td>
        <td class="num">${m.price == null ? "–" : usd(m.price, 3)}</td><td class="num">${vs}</td><td>${inside}</td>
        <td class="num muted">${ci}</td><td class="num muted">${m.ms < 1 ? "<1" : Math.round(m.ms)} ms</td><td class="muted">${esc(note)}</td></tr>`;
    });
    $("#vl-table tbody").innerHTML = rows.join("");
    const priced = d.models.filter((m) => m.price != null && m.applies);
    const inSpread = priced.filter((m) => m.in_spread).length;
    $("#vl-explain").textContent = `${inSpread} of ${priced.length} applicable models land inside the live bid/ask. ` +
      (d.exercise === "american"
        ? "This is an American contract: trees, Barone-Adesi-Whaley, Bjerksund-Stensland and Longstaff-Schwartz include the right to exercise early; Black-Scholes does not. "
        : "This is a European contract: American models are greyed out because early exercise isn't allowed. ") +
      "Merton keeps the same total variance but moves part of it into sudden jumps, so it is cheaper at the money and dearer far out of the money. " +
      "Heston is fitted to the whole smile, so it shows where this strike sits versus the rest of the expiry. Hover a row for what the model assumes.";
  }

  function renderBars(d) {
    const c = App.colors();
    const ms = d.models.filter((m) => m.price != null);
    const y = ms.map((m) => m.name);
    const traces = [{
      type: "bar", orientation: "h", y, x: ms.map((m) => m.price), name: "Model value",
      marker: { color: ms.map((m) => (m.applies ? c.s1 : App.alpha(c.s1, 0.35))) },
      error_x: { type: "data", array: ms.map((m) => (m.ci95 ? m.ci95[1] - m.price : 0)), visible: true, color: c.muted },
      hovertemplate: "%{y}: $%{x:.3f}<extra></extra>",
    }];
    const shapes = [];
    const m = d.market;
    if (m.bid != null && m.ask != null) {
      shapes.push({ type: "rect", xref: "x", yref: "paper", x0: m.bid, x1: m.ask, y0: 0, y1: 1, fillcolor: App.alpha(c.profit, 0.15), line: { width: 0 } });
    }
    if (m.mid != null) shapes.push({ type: "line", xref: "x", yref: "paper", x0: m.mid, x1: m.mid, y0: 0, y1: 1, line: { color: c.profit, dash: "dot", width: 2 } });
    const xs = ms.map((x) => x.price).concat([m.bid, m.ask].filter((v) => v != null));
    const lo = Math.min(...xs), hi = Math.max(...xs), pad = Math.max((hi - lo) * 0.15, 0.02);
    Plotly.react("vl-bars", traces, App.plotLayout({
      margin: { l: 225, r: 20, t: 10, b: 40 }, hovermode: "closest", showlegend: false, shapes,
      xaxis: { title: "Value per share ($) · shaded = live bid/ask", range: [Math.max(lo - pad, 0), hi + pad] },
      yaxis: { autorange: "reversed" },
    }), { displayModeBar: false, responsive: true });
  }

  async function loadSmile(p) {
    const el = $("#vl-smile");
    try {
      const d = await api("/api/valuation/smile?" + new URLSearchParams({ symbol: p.symbol, expiry: p.expiry, source: p.source }));
      const c = App.colors();
      const pts = d.points;
      App.plot(el, [
        { x: pts.map((q) => q.strike), y: pts.map((q) => q.iv * 100), mode: "markers", name: "Market IV", marker: { color: c.s2, size: 7 } },
        { x: pts.map((q) => q.strike), y: pts.map((q) => (q.heston_iv == null ? null : q.heston_iv * 100)), mode: "lines", name: "Heston fit", line: { color: c.s1, width: 2 } },
      ], App.plotLayout({
        xaxis: { title: "Strike" }, yaxis: { title: "Implied volatility %" },
        shapes: [{ type: "line", x0: d.spot, x1: d.spot, yref: "paper", y0: 0, y1: 1, line: { color: c.muted, dash: "dot" } }],
      }));
      const h = d.heston;
      const se = h.stderr || {}, ok = h.identified || {};
      // Show only what the quotes pin down as a fitted value; the rest are labelled as assumptions.
      const val = (k, txt, sdTxt) => (ok[k] === false ? `<span class="muted" title="Not pinned down by one expiry's quotes">${txt} (uncertain${sdTxt ? ", ±" + sdTxt : ""})</span>` : `${txt}${sdTxt ? " ±" + sdTxt : ""}`);
      const volPct = (v) => (Math.sqrt(Math.max(v, 0)) * 100).toFixed(1) + "%";
      const v0sd = se.v0 != null ? ((se.v0 / (2 * Math.sqrt(h.v0))) * 100).toFixed(2) + " pts" : "";
      const parts = [
        `Current vol ${val("v0", volPct(h.v0), v0sd)}`,
        `vol of vol ${val("xi", h.xi.toFixed(2), se.xi != null && se.xi < 9 ? se.xi.toFixed(2) : "")}`,
        `spot–vol correlation ${val("rho", h.rho.toFixed(2), se.rho != null && se.rho < 9 ? se.rho.toFixed(2) : "")}`,
      ];
      const assumed = ["kappa", "theta"].every((k) => ok[k] === false)
        ? ` Long-run vol ${volPct(h.theta)} and mean reversion ${h.kappa.toFixed(1)}/yr are assumptions: a single expiry can't measure them.`
        : ` Long-run vol ${volPct(h.theta)}, mean reversion ${h.kappa.toFixed(2)}/yr.`;
      $("#vl-heston").innerHTML = `Fitted on ${d.fitted_on} out-of-the-money strikes, error ${(d.rmse_vol * 100).toFixed(2)} vol pts. ` +
        parts.join(", ") + "." + assumed +
        (ok.rho !== false && h.rho < -0.3 ? " Prices and volatility move opposite ways: puts carry a skew premium." : "") +
        " Parameters can be uncertain while prices stay reliable: the fitted smile is checked against held-out strikes.";
    } catch (e) {
      App.chartMessage(el, `<p class="muted">Heston fit unavailable: ${esc(e.message)}</p>`);
      $("#vl-heston").textContent = "";
    }
  }

  async function loadConvergence(p) {
    const el = $("#vl-conv");
    try {
      const d = await api("/api/valuation/convergence?" + new URLSearchParams(p));
      const c = App.colors();
      const mc = d.mc;
      App.plot(el, [
        { x: d.tree.map((t) => t.steps), y: d.tree.map((t) => t.price), name: d.american ? "Binomial tree (American)" : "Binomial tree", mode: "lines+markers", line: { color: c.s1 }, xaxis: "x", yaxis: "y" },
        { x: mc.map((m) => m.paths), y: mc.map((m) => m.hi), mode: "lines", line: { width: 0 }, showlegend: false, hoverinfo: "skip", xaxis: "x2", yaxis: "y" },
        { x: mc.map((m) => m.paths), y: mc.map((m) => m.lo), mode: "lines", line: { width: 0 }, fill: "tonexty", fillcolor: App.alpha(c.s2, 0.2), name: "MC 95% band", hoverinfo: "skip", xaxis: "x2", yaxis: "y" },
        { x: mc.map((m) => m.paths), y: mc.map((m) => m.price), name: "Monte Carlo", mode: "lines+markers", line: { color: c.s2 }, xaxis: "x2", yaxis: "y" },
      ], App.plotLayout({
        hovermode: "closest",
        xaxis: { title: "Tree steps", type: "log", domain: [0, 0.47] },
        xaxis2: { title: "Monte Carlo paths", type: "log", domain: [0.53, 1], anchor: "y" },
        yaxis: { title: "Value ($)" },
        shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: d.bsm, y1: d.bsm, line: { color: c.muted, dash: "dash" } }],
        annotations: [{ xref: "paper", x: 1, y: d.bsm, text: `Black-Scholes ${usd(d.bsm, 3)}`, showarrow: false, yanchor: "bottom", xanchor: "right", font: { color: c.muted, size: 11 } }],
      }));
    } catch (e) {
      App.chartMessage(el, `<p class="muted">Convergence unavailable: ${esc(e.message)}</p>`);
    }
  }

  const prev = App.onTabShown;
  App.onTabShown = (name) => { if (prev) prev(name); if (name === "valuation") fillStrikes(); };
})();
