/* Watchlist: live snapshot of several underlyings. */
"use strict";

(function () {
  const { api, qs } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const KEY = "ot-watchlist";
  let symbols = load();
  let timer = null;
  let busy = false;

  function load() {
    try {
      const v = JSON.parse(localStorage.getItem(KEY) || "null");
      if (Array.isArray(v) && v.length) return v;
    } catch (_) { /* ignore */ }
    return ["SPY", "QQQ", "SPX", "AAPL", "NVDA", "TSLA"];
  }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(symbols)); } catch (_) { /* ignore */ } }

  function render(rows) {
    $("#wl-body").innerHTML = rows.map((r) => r.error
      ? `<tr><td><a data-sym="${esc(r.symbol)}">${esc(r.symbol)}</a></td><td colspan="10" class="err">${esc(r.error)}</td>
         <td><button data-del="${esc(r.symbol)}" title="Remove">✕</button></td></tr>`
      : `<tr>
        <td><a data-sym="${esc(r.symbol)}">${esc(r.symbol)}</a></td>
        <td>${fmt(r.spot)}</td><td>${esc(r.expiry)}</td><td>${r.pcr_oi ?? "–"}</td>
        <td>${r.atm_iv == null ? "–" : (r.atm_iv * 100).toFixed(1) + "%"}</td><td>${fmt(r.straddle)}</td>
        <td>${fmt(r.max_pain, 0)}</td><td>${fmt(r.support, 0)}</td><td>${fmt(r.resistance, 0)}</td>
        <td><span class="pill ${r.bias}">${r.bias}</span> <span class="muted">${r.options_score.toFixed(2)}</span></td>
        <td>${esc(r.source.toUpperCase())} <span class="muted">${new Date(r.fetched_at).toLocaleTimeString()}</span></td>
        <td><button data-del="${esc(r.symbol)}" title="Remove">✕</button></td>
      </tr>`).join("") || `<tr><td colspan="12" class="muted">Add symbols above.</td></tr>`;
  }

  async function refresh() {
    if (busy || !symbols.length) { if (!symbols.length) render([]); return; }
    busy = true;
    $("#wl-status").textContent = `Fetching ${symbols.length} live chains…`;
    try {
      const d = await api("/api/analytics/watchlist?" + qs({ symbols: symbols.join(","), source: $("#source").value }));
      render(d.rows);
      $("#wl-status").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    } catch (e) {
      $("#wl-status").textContent = `Watchlist unavailable: ${e.message}`;
    } finally {
      busy = false;
    }
  }

  function schedule() {
    clearInterval(timer);
    // Refresh every 2 minutes while the tab is open (sources are rate-limited).
    timer = setInterval(() => { if (!document.hidden && App.activeTab() === "watchlist") refresh(); }, 120000);
  }

  $("#wl-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const sym = $("#wl-add").value.trim().toUpperCase();
    if (sym && !symbols.includes(sym)) { symbols.push(sym); save(); refresh(); }
    $("#wl-add").value = "";
  });
  $("#wl-refresh").addEventListener("click", refresh);
  $("#wl-body").addEventListener("click", (ev) => {
    const del = ev.target.closest("button[data-del]");
    if (del) {
      symbols = symbols.filter((s) => s !== del.dataset.del); save();
      del.closest("tr").remove();
      return;
    }
    const a = ev.target.closest("a[data-sym]");
    if (a) {
      $("#symbol").value = a.dataset.sym;
      App.switchTab("chain");
      $("#controls").requestSubmit();
    }
  });

  const prev = App.onTabShown;
  App.onTabShown = (name) => {
    if (prev) prev(name);
    if (name === "watchlist") { refresh(); schedule(); }
  };
  render(symbols.map((s) => ({ symbol: s, error: "Not loaded yet: open this tab or press Refresh" })));
})();
