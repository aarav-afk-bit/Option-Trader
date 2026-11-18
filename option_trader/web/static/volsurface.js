/* Volatility surface, 25-delta skew and IV rank (Charts tab). */
"use strict";

(function () {
  const { api } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const pc = (v, d = 1) => (v == null ? "–" : (v * 100).toFixed(d) + "%");
  const pts = (v) => (v == null ? "–" : (v >= 0 ? "+" : "") + (v * 100).toFixed(2));
  const kv = (label, v, cls = "", title = "") => `<div class="kv" title="${esc(title)}"><small>${label}</small><b class="${cls}">${v}</b></div>`;

  /** Blend two hex colours (t = 0..1) for a short-to-long expiry gradient. */
  function mix(a, b, t) {
    const p = (h) => [1, 3, 5].map((i) => parseInt(h.replace("#", "").padEnd(6, "0").slice(i - 1, i + 1), 16));
    const [x, y] = [p(a), p(b)];
    return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * t).toString(16).padStart(2, "0")).join("");
  }

  async function load() {
    if (!App.chain) { App.showBanner("Load a live option chain first."); return; }
    $("#vs-summary").innerHTML = '<span class="muted">Loading live chains for every expiry…</span>';
    let d;
    try {
      d = await api("/api/analytics/surface?" + new URLSearchParams({ symbol: App.chain.symbol, source: App.chain.source, expiries: 8 }));
    } catch (e) {
      $("#vs-summary").innerHTML = "";
      App.chartMessage("ch-surface", `<p class="muted">Volatility surface unavailable: ${esc(e.message)}</p>`);
      return;
    }
    const r = d.iv_rank || {};
    const ratio = d.iv_hv_ratio;
    const front = d.expiries[0] || {};
    $("#vs-summary").innerHTML = [
      kv("IV30", pc(d.iv30_current), "", d.iv30_source || ""),
      kv("IV rank", r.iv_rank == null ? `building (${r.days || 0}/${r.min_days || 20} days)` : r.iv_rank.toFixed(0),
        r.iv_rank == null ? "" : r.iv_rank >= 60 ? "neg" : r.iv_rank <= 25 ? "pos" : "",
        r.low != null ? `1-year range recorded: ${pc(r.low)} – ${pc(r.high)}` : "Recorded once a day while the app runs"),
      kv("IV percentile", r.iv_percentile == null ? "–" : r.iv_percentile.toFixed(0) + "%"),
      kv("HV 20d", pc(d.hv20), "", "Actual volatility of daily closes over the last 20 sessions"),
      kv("IV / HV", ratio == null ? "–" : ratio.toFixed(2), ratio == null ? "" : ratio > 1.2 ? "neg" : ratio < 0.9 ? "pos" : ""),
      kv("Front RR 25Δ", pts(front.rr25) + " pts", front.rr25 == null ? "" : front.rr25 < 0 ? "neg" : "pos"),
      kv("Front BF 25Δ", pts(front.bf25) + " pts"),
    ].join("");

    const c = App.colors();
    const n = d.expiries.length;
    const traces = d.expiries.filter((e) => e.points.length).map((e, i) => ({
      type: "scatter", mode: "lines+markers", name: `${e.expiry} (${Math.round(e.days)}d)`,
      x: e.points.map((p) => p.moneyness * 100), y: e.points.map((p) => p.iv * 100),
      text: e.points.map((p) => `strike ${p.strike}`), hovertemplate: "%{text}<br>%{x:.1f}% from fwd: %{y:.2f}%<extra>%{fullData.name}</extra>",
      line: { color: mix(c.s1, c.s2, n > 1 ? i / (n - 1) : 0), width: 2 }, marker: { size: 4 },
    }));
    App.plot("ch-surface", traces, App.plotLayout({
      hovermode: "closest", xaxis: { title: "Strike vs forward (% , ln K/F)", zeroline: true }, yaxis: { title: "Implied volatility %" },
      legend: { orientation: "h", y: -0.25, font: { size: 10, color: c.text } }, margin: { l: 55, r: 15, t: 10, b: 70 },
    }));

    $("#vs-table tbody").innerHTML = d.expiries.map((e) => `<tr><td>${esc(e.expiry)}</td><td>${e.days.toFixed(1)}</td><td>${pc(e.atm_iv, 2)}</td>
      <td class="${e.rr25 == null ? "" : e.rr25 < 0 ? "neg" : "pos"}">${pts(e.rr25)}</td><td>${pts(e.bf25)}</td>
      <td>${pc(e.put25_iv, 2)}</td><td>${pc(e.call25_iv, 2)}</td></tr>`).join("")
      || '<tr><td colspan="7" class="muted">No expiry had enough priced strikes.</td></tr>';

    const h = (r.history || []);
    if (h.length > 1) {
      App.plot("vs-hist", [{ type: "scatter", mode: "lines", x: h.map((p) => p.date), y: h.map((p) => p.iv * 100), line: { color: c.accent, width: 2 }, name: "IV30" }],
        App.plotLayout({ margin: { l: 45, r: 10, t: 10, b: 30 }, yaxis: { title: "IV30 %" }, showlegend: false }));
    } else {
      App.chartMessage("vs-hist", `<p class="muted">IV30 history: ${h.length} day${h.length === 1 ? "" : "s"} recorded so far. The chart and IV rank fill in as the app sees ${esc(d.symbol)} on more trading days.</p>`);
    }
  }

  $("#load-surface").addEventListener("click", load);
})();
