/* Live HUD side panel, side-panel collapse, keyboard shortcuts and the command palette. */
"use strict";

(function () {
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => Array.from(document.querySelectorAll(s));
  const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = (k, v) => { try { localStorage.setItem(k, v); } catch (_) { /* ignore */ } };
  const load = (k) => { try { return localStorage.getItem(k); } catch (_) { return null; } };

  /* ---------- panel collapse ---------- */
  function setSide(collapsed) { document.body.classList.toggle("side-collapsed", collapsed); store("ot-side", collapsed ? "1" : "0"); }
  function setHud(hidden) {
    document.body.classList.toggle("hud-hidden", hidden);
    $("#hud-reopen").classList.toggle("hidden", !hidden);
    store("ot-hud", hidden ? "1" : "0");
    window.dispatchEvent(new Event("resize")); // let charts reflow
  }
  $("#side-collapse").addEventListener("click", () => setSide(!document.body.classList.contains("side-collapsed")));
  $("#hud-collapse").addEventListener("click", () => setHud(true));
  $("#hud-reopen").addEventListener("click", () => setHud(false));
  setSide(load("ot-side") === "1");
  setHud(load("ot-hud") === "1");

  /* ---------- live readouts ---------- */
  function tickClock() {
    const t = new Date().toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false });
    $("#hud-time").textContent = t;
  }
  setInterval(tickClock, 1000);
  tickClock();

  function pcr(ch) {
    let c = 0, p = 0;
    for (const r of ch.rows) { c += r.call?.oi || 0; p += r.put?.oi || 0; }
    return c ? (p / c).toFixed(2) : "–";
  }
  function atmIv(ch) {
    const r = ch.rows.find((x) => x.strike === ch.atm_strike);
    const v = [r?.call?.iv, r?.put?.iv].filter((x) => x);
    return v.length ? ((v.reduce((a, b) => a + b, 0) / v.length) * 100).toFixed(1) + "%" : "–";
  }
  function age(sec) {
    if (sec == null) return "–";
    return sec < 90 ? `${Math.round(sec)}s` : sec < 5400 ? `${Math.round(sec / 60)} min` : `${(sec / 3600).toFixed(1)} h`;
  }
  function refreshHud() {
    const m = App.market;
    $("#hud-market").innerHTML = m ? `<span class="${m.open ? "pos" : "neg"}">${m.open ? "OPEN" : esc(String(m.state).toUpperCase())}</span>` : "–";
    const live = $("#live-badge").textContent.trim();
    $("#hud-feed").innerHTML = `<span class="${live.startsWith("LIVE") ? "pos" : "warn"}">${esc(live)}</span>`;
    const ch = App.chain;
    if (!ch) return;
    $("#hud-age").textContent = age(ch.data_age_seconds);
    $("#hud-source").textContent = ch.source.toUpperCase();
    $("#hud-symbol").textContent = ch.symbol;
    $("#hud-spot").textContent = fmt(ch.spot);
    const idx = (App.indices || []).find((r) => r.symbol === ch.symbol && r.prev_close);
    if (idx) {
      const chg = ch.spot - idx.prev_close, pc = (chg / idx.prev_close) * 100;
      $("#hud-move").innerHTML = `<span class="${chg >= 0 ? "pos" : "neg"}">${chg >= 0 ? "▲ +" : "▼ "}${pc.toFixed(2)}%</span>`;
    } else $("#hud-move").textContent = "";
    $("#hud-forward").textContent = ch.forward ? fmt(ch.forward) : "–";
    $("#hud-atm").textContent = fmt(ch.atm_strike, 0);
    $("#hud-iv").textContent = atmIv(ch);
    $("#hud-pcr").textContent = pcr(ch);
    $("#hud-expiry").textContent = ch.expiry;
    $("#hud-lot").textContent = ch.lot_size ?? "–";
  }
  App.onChain(refreshHud);
  setInterval(refreshHud, 2000);

  /* ---------- command palette ---------- */
  const TABS = $$(".sidebar .tab").map((t) => ({ tab: t.dataset.tab, label: t.querySelector(".lbl").textContent }));
  function commands(q) {
    const list = [
      ...TABS.map((t, i) => ({ label: `Go to ${t.label}`, key: String(i + 1), run: () => App.switchTab(t.tab) })),
      { label: "Optimise the current strategy", key: "O", run: optimise },
      { label: "Refresh live data now", key: "R", run: () => App.loadChain(true) },
      { label: "Toggle light / dark theme", key: "T", run: () => $("#theme-toggle").click() },
      { label: "Export option chain to CSV", key: "", run: () => $("#export-csv").click() },
      { label: "Toggle navigation panel", key: "[", run: () => $("#side-collapse").click() },
      { label: "Toggle live status panel", key: "]", run: () => setHud(!document.body.classList.contains("hud-hidden")) },
      { label: "Show keyboard shortcuts", key: "", run: showHelp },
      { label: "Take the guided tour", key: "?", run: () => App.startTour && App.startTour() },
    ];
    const opts = Array.from(document.querySelectorAll("#symbols option")).map((o) => o.value);
    const sym = q.trim().toUpperCase();
    for (const o of opts) list.push({ label: `Load ${o}`, key: "", run: () => loadSymbol(o) });
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    const hits = list.filter((c) => words.every((w) => c.label.toLowerCase().includes(w)));
    // Any typed symbol can be loaded directly, listed after known matches.
    if (/^[A-Z0-9&:.^_-]{2,}$/.test(sym) && !opts.includes(sym)) {
      hits.push({ label: `Load ${sym} option chain`, key: "", run: () => loadSymbol(sym) });
    }
    return hits.slice(0, 12);
  }
  function loadSymbol(s) { $("#symbol").value = s; App.switchTab("chain"); $("#controls").requestSubmit(); }
  function optimise() { App.switchTab("builder"); $("#opt-run").click(); }

  let sel = 0, current = [];
  function renderCmd() {
    current = commands($("#cmd-input").value);
    sel = Math.min(sel, Math.max(current.length - 1, 0));
    $("#cmd-list").innerHTML = current.map((c, i) => `<li class="${i === sel ? "sel" : ""}" data-i="${i}">${esc(c.label)}<span class="k">${c.key ? `<kbd>${esc(c.key)}</kbd>` : ""}</span></li>`).join("")
      || '<li class="muted">No matching command</li>';
  }
  function openCmd() { $("#cmd").classList.remove("hidden"); $("#cmd-input").value = ""; sel = 0; renderCmd(); $("#cmd-input").focus(); }
  function closeCmd() { $("#cmd").classList.add("hidden"); }
  function runSel() { const c = current[sel]; closeCmd(); if (c) c.run(); }
  App.openCommands = openCmd;
  $("#cmd-open").addEventListener("click", openCmd);
  $("#cmd").addEventListener("click", (e) => { if (e.target.id === "cmd") closeCmd(); });
  $("#cmd-input").addEventListener("input", () => { sel = 0; renderCmd(); });
  $("#cmd-input").addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { sel = Math.min(sel + 1, current.length - 1); renderCmd(); e.preventDefault(); }
    else if (e.key === "ArrowUp") { sel = Math.max(sel - 1, 0); renderCmd(); e.preventDefault(); }
    else if (e.key === "Enter") { runSel(); e.preventDefault(); }
    else if (e.key === "Escape") closeCmd();
  });
  $("#cmd-list").addEventListener("click", (e) => { const li = e.target.closest("li[data-i]"); if (li) { sel = Number(li.dataset.i); runSel(); } });

  function showHelp() {
    App.showBanner("Shortcuts: 1–9 switch sections · / jump to symbol · R refresh · O optimise · T theme · [ ] side panels · Ctrl+K command palette · Esc close", "info");
  }

  /* ---------- keyboard shortcuts ---------- */
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); openCmd(); return; }
    const typing = /^(INPUT|SELECT|TEXTAREA)$/.test(document.activeElement?.tagName || "");
    if (e.key === "Escape") { closeCmd(); if (typing) document.activeElement.blur(); return; }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    const n = Number(e.key);
    if (n >= 1 && n <= TABS.length) { App.switchTab(TABS[n - 1].tab); return; }
    switch (e.key) {
      case "/": e.preventDefault(); $("#symbol").focus(); $("#symbol").select(); break;
      case "r": case "R": App.loadChain(true); break;
      case "o": case "O": optimise(); break;
      case "t": case "T": $("#theme-toggle").click(); break;
      case "[": $("#side-collapse").click(); break;
      case "]": setHud(!document.body.classList.contains("hud-hidden")); break;
      case "?": if (App.startTour) App.startTour(); else showHelp(); break;
      default:
    }
  });
})();
