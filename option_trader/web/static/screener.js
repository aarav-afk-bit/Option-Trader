/* Strategy screener: rank all strategies on the live chain. */
"use strict";

(function () {
  const { api } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  let last = null;

  const legText = (l) => `${l.action === "BUY" ? "B" : "S"} ${l.lots}× ${l.option_type === "FUT" ? "FUT" : fmt(l.strike, l.strike % 1 ? 2 : 0) + l.option_type}`;

  $("#sc-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (!App.chain) { App.showBanner("Load a live option chain first."); return; }
    $("#sc-status").textContent = "Screening…";
    const maxLoss = $("#sc-maxloss").value;
    try {
      last = await api("/api/strategy/screen", {
        method: "POST",
        body: JSON.stringify({
          ...App.params(), expiry: App.chain.expiry, view: $("#sc-view").value, lots: Number($("#sc-lots").value) || 1,
          max_loss: maxLoss === "" ? null : Number(maxLoss), min_pop: (Number($("#sc-pop").value) || 0) / 100,
          allow_unlimited_loss: $("#sc-unlimited").checked, sort: $("#sc-sort").value, top: 40,
        }),
      });
    } catch (e) {
      $("#sc-status").textContent = `Screen failed: ${e.message}`;
      return;
    }
    $("#sc-status").textContent = `${last.matched} matches on ${last.symbol} ${last.expiry} (spot ${fmt(last.spot)}, ${last.source.toUpperCase()}); ${last.skipped} didn't fit the listed strikes`;
    $("#sc-body").innerHTML = last.results.map((r, i) => `<tr>
      <td>${i + 1}</td><td>${esc(r.name)}${r.builtin ? "" : ' <span class="pill neutral">mine</span>'}</td><td>${r.width}</td>
      <td class="muted">${r.legs.map(legText).join(", ")}</td>
      <td class="${r.net_premium >= 0 ? "pos" : "neg"}">${fmt(r.net_premium)}</td>
      <td>${r.unlimited_profit ? "Unlimited" : fmt(r.max_profit)}</td>
      <td>${r.unlimited_loss ? "Unlimited" : fmt(r.max_loss)}</td>
      <td>${r.breakevens.map((b) => fmt(b, 0)).join(", ") || "–"}</td>
      <td>${r.pop == null ? "–" : (r.pop * 100).toFixed(0) + "%"}</td><td>${r.reward_risk ?? "–"}</td>
      <td>${r.score.toFixed(2)}</td>
      <td><button data-open="${i}">Open in builder</button></td></tr>`).join("")
      || `<tr><td colspan="12" class="muted">Nothing matched. Loosen the filters.</td></tr>`;
  });

  $("#sc-body").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-open]");
    if (!b || !last) return;
    const r = last.results[Number(b.dataset.open)];
    App.openStrategy(r.name, r.legs);
    App.switchTab("builder");
  });
})();
