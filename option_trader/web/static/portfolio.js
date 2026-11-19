/* Paper portfolio: positions opened, marked and closed at live prices. */
"use strict";

(function () {
  const { api } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

  function legRow(pos, leg, i) {
    const mark = pos.status === "open" ? (pos.marks || [])[i] : leg.exit;
    const sign = leg.action === "BUY" ? 1 : -1;
    const pnl = mark == null ? null : sign * leg.lots * pos.lot_size * (mark - leg.entry) + 0; // +0 avoids "-0.00"
    return `<tr><td>${esc(leg.action)}</td><td>${leg.lots}</td><td>${leg.option_type === "FUT" ? "underlying" : fmt(leg.strike, leg.strike % 1 ? 2 : 0) + " " + esc(leg.option_type)}</td>
      <td>${esc(leg.expiry)}</td><td>${fmt(leg.entry)}</td><td>${fmt(mark)}</td><td class="${cls(pnl)}">${fmt(pnl)}</td></tr>`;
  }

  function card(pos) {
    const open = pos.status === "open";
    const pnl = pos.pnl;
    return `<div class="card">
      <h3>${esc(pos.name)} <span class="muted">${esc(pos.symbol)} · ${esc(pos.source.toUpperCase())}</span>
        <span class="pill ${open ? "neutral" : "bullish"}">${open ? "open" : "closed"}</span></h3>
      <div class="kv">
        <div>Opened</div><div>${new Date(pos.opened_at).toLocaleString()} @ spot ${fmt(pos.entry_spot)}</div>
        ${open ? `<div>Spot now</div><div>${fmt(pos.spot)}</div>` : `<div>Closed</div><div>${new Date(pos.closed_at).toLocaleString()}</div>`}
        <div>${open ? "Unrealised P&amp;L" : "Realised P&amp;L"} (gross)</div><div class="${cls(pnl)}"><b>${pnl == null ? "unavailable" : fmt(pnl)}</b></div>
        <div>Charges${open ? " (incl. est. exit)" : ""}</div><div>${pos.charges_total == null ? "–" : fmt(pos.charges_total)}</div>
        <div>Net P&amp;L</div><div class="${cls(pos.net_pnl)}"><b>${pos.net_pnl == null ? "–" : fmt(pos.net_pnl)}</b></div>
      </div>
      ${(pos.errors || []).map((e) => `<p class="neg">${esc(e)}</p>`).join("")}
      <div class="table-wrap"><table class="simple">
        <thead><tr><th>Side</th><th>Lots</th><th>Contract</th><th>Expiry</th><th>Entry</th><th>${open ? "Live mark" : "Exit"}</th><th>P&amp;L</th></tr></thead>
        <tbody>${pos.legs.map((l, i) => legRow(pos, l, i)).join("")}</tbody></table></div>
      <div class="row" style="margin-top:8px">
        ${open ? `<button class="primary" data-close="${esc(pos.id)}">Close at live prices</button>` : ""}
        <button class="danger" data-delete="${esc(pos.id)}">Delete</button>
      </div>
    </div>`;
  }

  async function refresh() {
    try {
      const d = await api("/api/portfolio");
      const ps = d.positions.slice().reverse();
      $("#pf-list").innerHTML = ps.length ? `<div class="cards">${ps.map(card).join("")}</div>`
        : `<p class="muted">No paper positions yet. Build a strategy and press “Paper trade this”.</p>`;
      const open = ps.filter((p) => p.status === "open").length;
      $("#pf-summary").innerHTML = `${open} open · unrealised <b class="${cls(d.open_pnl)}">${d.open_pnl == null ? "unavailable" : fmt(d.open_pnl)}</b>
        · realised <b class="${cls(d.realized_pnl)}">${fmt(d.realized_pnl)}</b> · ${new Date().toLocaleTimeString()}`;
    } catch (e) {
      $("#pf-summary").textContent = `Portfolio unavailable: ${e.message}`;
    }
  }

  $("#pf-refresh").addEventListener("click", refresh);
  $("#pf-list").addEventListener("click", async (ev) => {
    const c = ev.target.closest("button[data-close]");
    const d = ev.target.closest("button[data-delete]");
    try {
      if (c) {
        c.disabled = true;
        await api(`/api/portfolio/${c.dataset.close}/close`, { method: "POST", body: JSON.stringify({ fill: $("#pf-fill").value }) });
      } else if (d) {
        if (!confirm("Delete this paper position?")) return;
        await api(`/api/portfolio/${d.dataset.delete}`, { method: "DELETE" });
      } else return;
      refresh();
    } catch (e) {
      App.showBanner(e.message);
      if (c) c.disabled = false;
    }
  });

  $("#paper-btn").addEventListener("click", async () => {
    const { name, legs } = App.builderLegs();
    const msg = $("#save-msg");
    if (!legs.length || !App.chain) { msg.textContent = "Add legs on a live chain first."; return; }
    try {
      const pos = await api("/api/portfolio/open", {
        method: "POST",
        body: JSON.stringify({ name, ...App.params(), expiry: App.chain.expiry, fill: $("#tpl-fill").value,
          legs: legs.map((l) => ({ option_type: l.option_type, action: l.action, strike: l.strike, lots: l.lots, expiry: l.expiry })) }),
      });
      msg.textContent = `Paper position "${pos.name}" opened at live prices. See the Paper Portfolio tab.`;
    } catch (e) {
      msg.textContent = `Could not open paper trade: ${e.message}`;
    }
  });

  const prev = App.onTabShown;
  App.onTabShown = (name) => { if (prev) prev(name); if (name === "portfolio") refresh(); };
})();
