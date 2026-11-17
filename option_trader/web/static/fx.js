/* Visual effects: boot sequence (shows the real connection checks), effects toggle,
   and the scanning loader while the first chain loads. Everything is skippable and off
   for users who prefer reduced motion. */
"use strict";

(function () {
  const $ = (s) => document.querySelector(s);
  const root = document.documentElement;
  const fxOn = () => !root.classList.contains("fx-off");

  /* ---------- FX toggle ---------- */
  function paintToggle() {
    const b = $("#fx-toggle");
    b.classList.toggle("on", fxOn());
    b.setAttribute("aria-pressed", String(fxOn()));
    b.title = fxOn() ? "Visual effects on: click to turn off" : "Visual effects off: click to turn on";
  }
  $("#fx-toggle").addEventListener("click", () => {
    root.classList.toggle("fx-off");
    try { localStorage.setItem("ot-fx", fxOn() ? "1" : "0"); } catch (_) { /* ignore */ }
    paintToggle();
  });
  paintToggle();

  /* ---------- scanning loader for the first chain load ---------- */
  const tbody = $("#chain-table tbody");
  if (tbody && !tbody.children.length) {
    tbody.innerHTML = '<tr class="scan-row"><td colspan="25"><span class="scan-text">Scanning live option chain…</span></td></tr>';
  }

  /* ---------- boot sequence ---------- */
  let seen = false;
  try { seen = sessionStorage.getItem("ot-booted") === "1"; } catch (_) { seen = true; }
  if (!fxOn() || seen || navigator.webdriver) return;

  const boot = $("#boot"), log = $("#boot-log"), bar = boot.querySelector(".boot-bar i");
  boot.classList.remove("hidden");
  const bootStart = Date.now();
  let done = false;
  const lines = [];
  const write = (t, cls = "") => { lines.push(`<span class="${cls}">${t}</span>`); log.innerHTML = lines.join("\n"); };
  const finish = () => {
    if (done) return;
    done = true;
    try { sessionStorage.setItem("ot-booted", "1"); } catch (_) { /* ignore */ }
    boot.classList.add("boot-out");
    setTimeout(() => boot.classList.add("hidden"), 350);
    document.removeEventListener("keydown", finish);
  };
  document.addEventListener("keydown", finish);
  boot.addEventListener("click", finish);

  (async function run() {
    const step = (pct) => { bar.style.width = pct + "%"; };
    write("&gt; boot option//trader v" + (document.querySelector("meta[name=version]")?.content || "1"));
    step(10);
    try {
      const st = await App.api("/api/status");
      write(`&gt; network link ........ ${st.online ? "ONLINE" : "OFFLINE"}`, st.online ? "ok" : "bad");
      for (const p of st.providers) write(`&gt; source ${p.name.padEnd(9, ".")} ${p.configured ? "READY" : "not configured"}`, p.configured ? "ok" : "dim");
      write(`&gt; tick feed ........... ${st.live ? (st.live.connected ? "STREAMING" : "STANDBY") : "SNAPSHOT MODE"}`, st.live ? "ok" : "dim");
    } catch (e) {
      write("&gt; server ............... UNREACHABLE", "bad");
    }
    step(60);
    try {
      const m = await App.api("/api/market");
      write(`&gt; US session .......... ${String(m.state).toUpperCase()}${m.estimate ? " (est.)" : ""}`, m.open ? "ok" : "dim");
    } catch (_) { /* market status optional */ }
    step(85);
    write("&gt; loading option chain…");
    // Leave the boot screen once the first chain arrives (or after 3 s at most).
    const t0 = Date.now();
    const wait = setInterval(() => {
      const elapsed = Date.now() - bootStart;
      if ((App.chain && elapsed > 1200) || Date.now() - t0 > 3000) {
        clearInterval(wait);
        step(100);
        write(App.chain ? `&gt; ${App.chain.symbol} chain ......... LOCKED (${App.chain.source.toUpperCase()})` : "&gt; chain pending ……… continuing", App.chain ? "ok" : "dim");
        setTimeout(finish, 450);
      }
    }, 150);
  })();
})();
