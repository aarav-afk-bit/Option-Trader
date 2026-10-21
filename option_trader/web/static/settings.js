/* Settings page: sources, Angel One credentials, charges, and the connection doctor. */
"use strict";

(function () {
  const { api } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const NAMES = { nse: "NSE (nseindia.com)", angelone: "Angel One SmartAPI", yahoo: "Yahoo Finance" };
  let order = [], enabled = new Set();

  function renderSources() {
    $("#st-sources").innerHTML = order.map((p, i) => `<li>
      <label class="inline"><input type="checkbox" data-src="${p}" ${enabled.has(p) ? "checked" : ""}> ${esc(NAMES[p])}</label>
      <span class="st-move"><button type="button" data-up="${i}" ${i === 0 ? "disabled" : ""} title="Move up">▲</button>
      <button type="button" data-down="${i}" ${i === order.length - 1 ? "disabled" : ""} title="Move down">▼</button></span></li>`).join("");
  }

  function fill(v) {
    order = [...v.providers, ...["nse", "angelone", "yahoo"].filter((p) => !v.providers.includes(p))];
    enabled = new Set(v.providers);
    renderSources();
    $("#st-live").checked = v.live_ticks;
    $("#st-ttl").value = v.cache_ttl;
    $("#st-stale").value = v.stale_seconds;
    $("#st-rate").value = (v.risk_free_rate * 100).toFixed(2);
    for (const [k, val] of Object.entries(v.charges)) { const el = document.getElementById("st-" + k); if (el) el.value = val; }
    const a = v.angel;
    $("#st-angel-status").innerHTML = a.configured
      ? `<span class="pos">Saved:</span> API key ${esc(a.api_key)}, client ${esc(a.client_code)}, PIN ${esc(a.pin)}, TOTP ${esc(a.totp_secret)}`
      : '<span class="warn">Not set up</span> — the app works without it, using NSE snapshots.';
    $("#st-dir").textContent = `Saved in ${v.data_dir}`;
    ["#st-api-key", "#st-client", "#st-pin", "#st-totp"].forEach((s) => { $(s).value = ""; });
  }

  async function load() {
    try { fill(await api("/api/settings")); } catch (e) { $("#st-msg").textContent = `Could not load settings: ${e.message}`; }
  }

  async function save(body, msg) {
    $("#st-msg").textContent = "Saving…";
    try {
      fill(await api("/api/settings", { method: "POST", body: JSON.stringify(body) }));
      $("#st-msg").innerHTML = `<span class="pos">${esc(msg)}</span>`;
      App.loadChain(true);
    } catch (e) {
      $("#st-msg").innerHTML = `<span class="neg">${esc(e.message)}</span>`;
    }
  }

  $("#st-sources").addEventListener("click", (e) => {
    const up = e.target.closest("[data-up]"), down = e.target.closest("[data-down]");
    if (up) { const i = +up.dataset.up; [order[i - 1], order[i]] = [order[i], order[i - 1]]; renderSources(); }
    if (down) { const i = +down.dataset.down; [order[i + 1], order[i]] = [order[i], order[i + 1]]; renderSources(); }
  });
  $("#st-sources").addEventListener("change", (e) => {
    const cb = e.target.closest("[data-src]");
    if (cb) { if (cb.checked) enabled.add(cb.dataset.src); else enabled.delete(cb.dataset.src); }
  });

  $("#st-save").addEventListener("click", () => {
    const providers = order.filter((p) => enabled.has(p));
    if (!providers.length) { $("#st-msg").innerHTML = '<span class="neg">Keep at least one data source switched on.</span>'; return; }
    const charges = {};
    document.querySelectorAll('[id^="st-OPTION_TRADER_"]').forEach((el) => { charges[el.id.slice(3)] = el.value; });
    save({
      providers, live_ticks: $("#st-live").checked, cache_ttl: Number($("#st-ttl").value),
      stale_seconds: Number($("#st-stale").value), risk_free_rate: Number($("#st-rate").value) / 100, charges,
    }, "Settings saved and applied.");
  });
  $("#st-angel-save").addEventListener("click", () => save({ angel: {
    api_key: $("#st-api-key").value, client_code: $("#st-client").value, pin: $("#st-pin").value, totp_secret: $("#st-totp").value,
  } }, "Angel One details saved. Press “Test connections” to check them."));
  $("#st-angel-clear").addEventListener("click", () => {
    if (confirm("Remove the saved Angel One details from this computer?")) save({ angel: { clear: true } }, "Angel One details removed.");
  });

  $("#st-test").addEventListener("click", async () => {
    const btn = $("#st-test"), out = $("#st-test-out");
    btn.disabled = true; btn.textContent = "Testing…";
    out.innerHTML = '<p class="muted">Contacting each source from this computer…</p>';
    try {
      const d = await api("/api/settings/test", { method: "POST" });
      out.innerHTML = `<table class="simple st-results"><tbody>${d.results.map((r) => `<tr>
        <td>${r.ok ? '<span class="pos">✔</span>' : '<span class="neg">✖</span>'} ${esc(r.name)}</td>
        <td class="muted">${r.ms} ms</td>
        <td>${r.ok ? esc(r.detail) : `<span class="st-advice">${esc(r.advice)}</span><br><span class="st-raw">${esc(r.error)}</span>`}</td></tr>`).join("")}</tbody></table>`;
    } catch (e) {
      out.innerHTML = `<p class="neg">Test failed: ${esc(e.message)}</p>`;
    } finally {
      btn.disabled = false; btn.textContent = "Test connections";
    }
  });

  const prev = App.onTabShown;
  App.onTabShown = (name) => { if (prev) prev(name); if (name === "settings") load(); };
})();
