/* Alerts on live data: spot, PCR, ATM IV or an option's LTP crossing a level. */
"use strict";

(function () {
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const KEY = "ot-alerts";
  const LABEL = { spot: "Spot", pcr: "PCR (OI)", atm_iv: "ATM IV %", ltp: "LTP" };
  let alerts = load();

  function load() { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (_) { return []; } }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(alerts)); } catch (_) { /* ignore */ } }

  /** Metric value from a live chain, or null if the chain doesn't contain it. */
  function metric(ch, a) {
    if (a.metric === "spot") return ch.spot;
    if (a.metric === "pcr") {
      let c = 0, p = 0;
      for (const r of ch.rows) { c += r.call?.oi || 0; p += r.put?.oi || 0; }
      return c ? p / c : null;
    }
    if (a.metric === "atm_iv") {
      const row = ch.rows.find((r) => r.strike === ch.atm_strike);
      const ivs = [row?.call?.iv, row?.put?.iv].filter((v) => v);
      return ivs.length ? (ivs.reduce((x, y) => x + y, 0) / ivs.length) * 100 : null;
    }
    if (a.metric === "ltp") {
      const row = ch.rows.find((r) => r.strike === Number(a.strike));
      const q = row && (a.type === "CE" ? row.call : row.put);
      return q && q.ltp ? q.ltp : null;
    }
    return null;
  }

  function describe(a) {
    const what = a.metric === "ltp" ? `${fmt(a.strike, a.strike % 1 ? 2 : 0)} ${a.type} LTP` : LABEL[a.metric];
    return `${what} ${a.op} ${fmt(a.value, a.metric === "pcr" ? 2 : 2)}`;
  }

  function toast(text) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = text;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), 12000);
  }

  function notify(text) {
    toast(text);
    try {
      if ("Notification" in window && Notification.permission === "granted") new Notification("Option Trader alert", { body: text });
    } catch (_) { /* notifications unsupported */ }
    try { // short beep
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const o = ctx.createOscillator(); o.frequency.value = 880; o.connect(ctx.destination); o.start(); o.stop(ctx.currentTime + 0.2);
    } catch (_) { /* audio unavailable */ }
  }

  function check(ch) {
    let changed = false;
    for (const a of alerts) {
      if (a.symbol !== ch.symbol || a.triggered_at) continue;
      if (a.metric === "ltp" && a.expiry && a.expiry !== ch.expiry) continue;
      const v = metric(ch, a);
      if (v === null) continue;
      a.last = v; a.last_at = ch.fetched_at; a.last_source = ch.source;
      changed = true;
      if ((a.op === "above" && v > a.value) || (a.op === "below" && v < a.value)) {
        a.triggered_at = new Date().toISOString();
        notify(`${a.symbol}: ${describe(a)} (now ${fmt(v)}, ${ch.source.toUpperCase()})`);
      }
    }
    if (changed) { save(); render(); }
  }

  function render() {
    const active = alerts.filter((a) => !a.triggered_at).length;
    const triggered = alerts.length - active;
    const badge = $("#alert-count");
    badge.textContent = triggered ? `${triggered} hit` : String(active);
    badge.className = "pill " + (triggered ? "bearish" : "neutral") + (alerts.length ? "" : " hidden");
    $("#al-body").innerHTML = alerts.map((a, i) => `<tr>
      <td>${esc(a.symbol)}</td><td>${esc(describe(a))}${a.expiry ? ` <span class="muted">(${esc(a.expiry)})</span>` : ""}</td>
      <td>${a.last == null ? "–" : fmt(a.last)} ${a.last_at ? `<span class="muted">${new Date(a.last_at).toLocaleTimeString()} ${esc((a.last_source || "").toUpperCase())}</span>` : ""}</td>
      <td>${a.triggered_at ? `<span class="pill bearish">triggered ${new Date(a.triggered_at).toLocaleString()}</span>` : '<span class="pill bullish">watching</span>'}</td>
      <td>${a.triggered_at ? `<button data-rearm="${i}">Re-arm</button>` : ""} <button data-del="${i}" title="Delete">✕</button></td>
    </tr>`).join("") || `<tr><td colspan="5" class="muted">No alerts yet.</td></tr>`;
  }

  $("#al-metric").addEventListener("change", () => {
    document.querySelectorAll(".al-ltp").forEach((el) => el.classList.toggle("hidden", $("#al-metric").value !== "ltp"));
  });
  $("#al-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const symbol = ($("#al-symbol").value.trim() || (App.chain && App.chain.symbol) || $("#symbol").value).toUpperCase();
    const a = { symbol, metric: $("#al-metric").value, op: $("#al-op").value, value: Number($("#al-value").value), created_at: new Date().toISOString() };
    if (a.metric === "ltp") {
      a.strike = Number($("#al-strike").value); a.type = $("#al-type").value;
      if (!a.strike) { App.showBanner("Enter the option strike for an LTP alert."); return; }
      if (App.chain && App.chain.symbol === symbol) a.expiry = App.chain.expiry;
    }
    alerts.push(a); save(); render();
    if (App.chain) check(App.chain);
    $("#al-value").value = "";
  });
  $("#al-body").addEventListener("click", (ev) => {
    const d = ev.target.closest("button[data-del]"), r = ev.target.closest("button[data-rearm]");
    if (d) alerts.splice(Number(d.dataset.del), 1);
    else if (r) delete alerts[Number(r.dataset.rearm)].triggered_at;
    else return;
    save(); render();
    if (App.chain) check(App.chain);
  });
  $("#al-notify").addEventListener("click", async () => {
    if (!("Notification" in window)) { App.showBanner("This browser doesn't support desktop notifications."); return; }
    const p = await Notification.requestPermission();
    $("#al-notify").textContent = p === "granted" ? "Desktop notifications on" : "Notifications blocked";
  });
  if ("Notification" in window && Notification.permission === "granted") $("#al-notify").textContent = "Desktop notifications on";

  // Alerts on symbols other than the one on screen are checked from their own live chains.
  async function checkOthers() {
    if (document.hidden) return;
    const current = App.chain && App.chain.symbol;
    const syms = [...new Set(alerts.filter((a) => !a.triggered_at && a.symbol !== current).map((a) => a.symbol))];
    for (const sym of syms) {
      try {
        const expiry = (alerts.find((a) => a.symbol === sym && a.expiry && !a.triggered_at) || {}).expiry;
        check(await App.api("/api/chain?" + App.qs({ symbol: sym, expiry, source: $("#source").value })));
      } catch (_) { /* source unavailable: try again next round */ }
    }
  }
  setInterval(() => checkOthers(), 60000);

  App.onChain(check);
  App.checkAlerts = check;
  render();
})();
