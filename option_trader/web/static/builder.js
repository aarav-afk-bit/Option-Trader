/* Strategy builder: inbuilt + saved strategies, editable legs, live payoff graph. */
"use strict";

(function () {
  const { api, qs } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v === null || v === undefined ? "–" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));

  const state = { templates: [], legs: [], name: "Custom", lastResult: null, analyzeTimer: null, seq: 0 };

  /* ---------- templates ---------- */
  async function loadTemplates(selectId) {
    state.templates = await api("/api/strategy/templates");
    const sel = $("#tpl-select");
    const groups = { "My strategies": [], Bullish: [], Bearish: [], Neutral: [], Volatile: [], Other: [] };
    for (const t of state.templates) {
      const g = !t.builtin ? "My strategies" : ({ bullish: "Bullish", bearish: "Bearish", neutral: "Neutral", volatile: "Volatile" }[t.view] || "Other");
      groups[g].push(t);
    }
    sel.innerHTML = Object.entries(groups).filter(([, ts]) => ts.length).map(([g, ts]) =>
      `<optgroup label="${esc(g)}">${ts.map((t) => `<option value="${esc(t.id)}">${esc(t.name)}</option>`).join("")}</optgroup>`).join("");
    if (selectId) sel.value = selectId;
    describeTemplate();
  }

  function currentTemplate() {
    return state.templates.find((t) => t.id === $("#tpl-select").value);
  }

  function describeTemplate() {
    const t = currentTemplate();
    if (!t) return;
    const legs = t.legs.map((l) => `${l.action} ${l.lots > 1 ? l.lots + "× " : ""}${l.option_type === "FUT" ? "underlying" : `${l.option_type} ATM${l.offset ? (l.offset > 0 ? "+" : "") + l.offset : ""}`}${l.expiry_offset ? " (next expiry)" : ""}`);
    $("#tpl-desc").textContent = `${t.description || ""} Legs: ${legs.join(", ")}.`;
    $("#tpl-delete").classList.toggle("hidden", t.builtin);
  }

  async function applyTemplate() {
    const t = currentTemplate();
    if (!t) return;
    const btn = $("#tpl-apply");
    btn.disabled = true;
    try {
      const body = {
        ...App.params(),
        lots: Number($("#tpl-lots").value) || 1,
        width: Number($("#tpl-width").value) || 1,
        atm_shift: Number($("#tpl-shift").value) || 0,
        fill: $("#tpl-fill").value,
      };
      const res = await api(`/api/strategy/templates/${encodeURIComponent(t.id)}/build`, { method: "POST", body: JSON.stringify(body) });
      setLegs(t.name, res.strategy.legs.map((l) => ({ ...l, premium: null })));
    } catch (e) {
      App.showBanner(`Cannot place ${t.name} on this chain: ${e.message}`);
    } finally {
      btn.disabled = false;
    }
  }

  async function deleteTemplate() {
    const t = currentTemplate();
    if (!t || t.builtin || !confirm(`Delete saved strategy "${t.name}"?`)) return;
    await api(`/api/strategy/saved/${encodeURIComponent(t.id)}`, { method: "DELETE" });
    await loadTemplates();
  }

  const shortDate = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short" });

  /* ---------- legs ---------- */
  function chainStrikes() { return App.chain ? App.chain.rows.map((r) => r.strike) : []; }
  function chainExpiries() { return App.chain ? (App.chain.expiries.length ? App.chain.expiries : [App.chain.expiry]) : []; }

  function liveQuote(leg) {
    const ch = App.chain;
    if (!ch || leg.option_type === "FUT") return ch ? ch.spot : null;
    if (leg.expiry && leg.expiry !== ch.expiry) return null; // other expiry: priced server-side
    const row = ch.rows.find((r) => r.strike === Number(leg.strike));
    const q = row && (leg.option_type === "CE" ? row.call : row.put);
    return q ? q.ltp : null;
  }

  function setLegs(name, legs) {
    state.name = name;
    state.legs = legs.map((l) => ({
      action: l.action, option_type: l.option_type, strike: l.strike, lots: l.lots || 1,
      expiry: l.expiry || (App.chain && App.chain.expiry), premium: l.premium ?? null,
    }));
    if (!$("#save-name").value) $("#save-name").placeholder = name;
    renderLegs();
    scheduleAnalyze(0);
  }

  function addLeg(partial) {
    const ch = App.chain;
    if (!ch) { App.showBanner("Load a live option chain first."); return; }
    state.legs.push({
      action: partial.action || "BUY", option_type: partial.option_type || "CE",
      strike: partial.strike ?? ch.atm_strike, lots: partial.lots || 1, expiry: partial.expiry || ch.expiry, premium: null,
    });
    if (state.legs.length === 1) state.name = "Custom";
    renderLegs();
    scheduleAnalyze(0);
  }

  function renderLegs() {
    const strikes = chainStrikes(), exps = chainExpiries();
    const opt = (vals, cur, label = (v) => v) => vals.map((v) => `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(label(v))}</option>`).join("");
    $("#legs-body").innerHTML = state.legs.map((l, i) => {
      const strikeList = strikes.includes(Number(l.strike)) || l.option_type === "FUT" ? strikes : [...strikes, Number(l.strike)].sort((a, b) => a - b);
      const live = liveQuote(l);
      return `<tr class="${l.action.toLowerCase()}" data-i="${i}">
        <td><select data-k="action">${opt(["BUY", "SELL"], l.action)}</select></td>
        <td><select data-k="option_type">${opt(["CE", "PE", "FUT"], l.option_type)}</select></td>
        <td><select data-k="expiry">${opt(exps, l.expiry, shortDate)}</select></td>
        <td>${l.option_type === "FUT" ? "–" : `<select data-k="strike">${opt(strikeList, l.strike, (v) => fmt(v, v % 1 ? 2 : 0))}</select>`}</td>
        <td class="num"><input data-k="lots" type="number" min="1" value="${l.lots}"></td>
        <td class="num"><input data-k="premium" type="number" step="0.05" min="0" placeholder="live" value="${l.premium ?? ""}"></td>
        <td class="live">${live === null ? "srv" : fmt(live)}</td>
        <td><button data-del="${i}" title="Remove leg">✕</button></td>
      </tr>`;
    }).join("") || `<tr><td colspan="8" class="muted">No legs yet: pick a strategy above or click prices in the Option Chain.</td></tr>`;
  }

  $("#legs-body").addEventListener("change", (ev) => {
    const tr = ev.target.closest("tr"), k = ev.target.dataset.k;
    if (!tr || !k) return;
    const leg = state.legs[Number(tr.dataset.i)];
    let v = ev.target.value;
    if (k === "strike") v = Number(v);
    if (k === "lots") v = Math.max(1, parseInt(v, 10) || 1);
    if (k === "premium") v = v === "" ? null : Math.max(0, Number(v));
    leg[k] = v;
    state.name = state.name.endsWith(" (modified)") || state.name === "Custom" ? state.name : state.name + " (modified)";
    renderLegs();
    scheduleAnalyze();
  });
  $("#legs-body").addEventListener("click", (ev) => {
    const b = ev.target.closest("button[data-del]");
    if (!b) return;
    state.legs.splice(Number(b.dataset.del), 1);
    renderLegs();
    scheduleAnalyze(0);
  });

  /* ---------- analysis & chart ---------- */
  function daysToExpiry() {
    if (!App.chain) return 30;
    const exp = new Date(App.chain.expiry + "T15:30:00+05:30");
    return Math.max(0, (exp - Date.now()) / 86400000);
  }

  function updateScenarioLabels() {
    const max = Math.max(0.5, Math.floor(daysToExpiry() * 2) / 2);
    const slider = $("#target-days");
    slider.max = String(max);
    if (Number(slider.value) > max) slider.value = String(max);
    const d = Number(slider.value);
    const when = new Date(Date.now() + d * 86400000);
    $("#target-label").textContent = `${d} day(s) from now (${when.toLocaleDateString()})`;
    $("#iv-label").textContent = $("#iv-shift").value;
    $("#range-label").textContent = $("#range-pct").value;
  }

  function scheduleAnalyze(delay = 350) {
    clearTimeout(state.analyzeTimer);
    state.analyzeTimer = setTimeout(() => { state.analyzeTimer = null; analyzeNow(); }, delay);
  }

  async function analyzeNow() {
    updateScenarioLabels();
    if (!state.legs.length || !App.chain) {
      Plotly.purge("payoff");
      $("#payoff").innerHTML = `<p class="muted" style="padding:16px">Add legs to see the payoff graph.</p>`;
      $("#builder-stats").innerHTML = "";
      return;
    }
    const seq = ++state.seq;
    const body = {
      ...App.params(),
      expiry: App.chain.expiry,
      name: state.name,
      legs: state.legs.map((l) => ({ ...l, strike: l.option_type === "FUT" ? 0 : Number(l.strike) })),
      target_days: Number($("#target-days").value),
      iv_shift: Number($("#iv-shift").value) / 100,
      range_pct: Number($("#range-pct").value) / 100,
      fill: $("#tpl-fill").value,
    };
    try {
      const res = await api("/api/strategy/analyze", { method: "POST", body: JSON.stringify(body) });
      if (seq !== state.seq) return; // a newer request is in flight
      state.lastResult = res;
      drawChart(res);
      drawStats(res);
      drawGreeks(res);
      drawPnlTable(res);
    } catch (e) {
      if (seq !== state.seq) return;
      $("#builder-stats").innerHTML = `<div class="banner">Cannot analyse: ${esc(e.message)}</div>`;
    }
  }

  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

  function drawChart(r) {
    const x = r.prices, y = r.pnl_expiry;
    const pos = y.map((v) => (v >= 0 ? v : null)), neg = y.map((v) => (v < 0 ? v : null));
    const text = cssVar("--text"), grid = cssVar("--border");
    const targetLabel = `On ${new Date(r.target).toLocaleDateString()}`;
    const traces = [
      { x, y: pos, name: "Profit at expiry", mode: "lines", line: { color: "#22c55e", width: 0 }, fill: "tozeroy", fillcolor: "rgba(34,197,94,0.18)", hoverinfo: "skip", showlegend: false },
      { x, y: neg, name: "Loss at expiry", mode: "lines", line: { color: "#ef4444", width: 0 }, fill: "tozeroy", fillcolor: "rgba(239,68,68,0.18)", hoverinfo: "skip", showlegend: false },
      { x, y, name: "At expiry", mode: "lines", line: { color: "#3b82f6", width: 2.5 } },
      { x, y: r.pnl_target, name: targetLabel, mode: "lines", line: { color: "#f59e0b", width: 2, dash: "dash" } },
      { x, y: r.pnl_today, name: "Today", mode: "lines", line: { color: "#a855f7", width: 1.5, dash: "dot" } },
      { x: r.breakevens, y: r.breakevens.map(() => 0), name: "Breakeven", mode: "markers", marker: { color: text, size: 8, symbol: "diamond" } },
    ];
    const layout = {
      margin: { l: 70, r: 20, t: 40, b: 50 },
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { color: text },
      title: { text: `${esc(r.strategy.name)} · ${esc(r.symbol)} · spot ${fmt(r.spot)} · ${esc(r.source.toUpperCase())}`, font: { size: 14 } },
      xaxis: { title: "Underlying price", gridcolor: grid, zerolinecolor: grid },
      yaxis: { title: "Profit / loss", gridcolor: grid, zerolinecolor: text, zerolinewidth: 1 },
      hovermode: "x unified",
      hoverlabel: { bgcolor: cssVar("--panel2"), bordercolor: grid, font: { color: text } },
      legend: { orientation: "h", y: -0.18 },
      shapes: [{ type: "line", x0: r.spot, x1: r.spot, yref: "paper", y0: 0, y1: 1, line: { color: "#94a3b8", dash: "dot", width: 1 } }],
      annotations: [{ x: r.spot, yref: "paper", y: 1, text: "Spot", showarrow: false, font: { size: 11 }, yanchor: "bottom" }],
    };
    const el = $("#payoff");
    if (!el._fullLayout) el.innerHTML = ""; // drop the "add legs" placeholder before the first plot
    Plotly.react(el, traces, layout, { responsive: true, displaylogo: false });
  }

  function interp(xs, ys, x) {
    if (x <= xs[0]) return ys[0];
    for (let i = 1; i < xs.length; i++) {
      if (x <= xs[i]) return ys[i - 1] + ((ys[i] - ys[i - 1]) * (x - xs[i - 1])) / (xs[i] - xs[i - 1]);
    }
    return ys[ys.length - 1];
  }

  function drawGreeks(r) {
    const k = $("#greek-pick").value, c = r.greeks_curve;
    Plotly.react("greeks-chart", [{ x: c.prices, y: c[k], mode: "lines", line: { color: "#3b82f6", width: 2 }, name: k }],
      App.plotLayout({ showlegend: false, xaxis: { title: "Underlying price" },
        shapes: [{ type: "line", x0: r.spot, x1: r.spot, yref: "paper", y0: 0, y1: 1, line: { color: "#94a3b8", dash: "dot", width: 1 } }] }),
      { responsive: true, displaylogo: false });
  }

  function drawPnlTable(r) {
    const steps = [-5, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 5];
    const targetLabel = new Date(r.target).toLocaleDateString();
    const rows = steps.map((pc) => {
      const px = r.spot * (1 + pc / 100);
      const cell = (ys) => { const v = interp(r.prices, ys, px) + 0; return `<td class="${v >= 0 ? "pos" : "neg"}">${fmt(v, 0)}</td>`; };
      return `<tr class="${pc === 0 ? "spot-row" : ""}"><td>${pc > 0 ? "+" : ""}${pc}%</td><td>${fmt(px, 0)}</td>${cell(r.pnl_today)}${cell(r.pnl_target)}${cell(r.pnl_expiry)}</tr>`;
    });
    $("#pnl-table").innerHTML = `<thead><tr><th>Move</th><th>Price</th><th>Today</th><th>${esc(targetLabel)}</th><th>Expiry</th></tr></thead><tbody>${rows.join("")}</tbody>`;
  }

  function drawStats(r) {
    const g = r.greeks;
    const money = (v, unl) => (unl ? "Unlimited" : fmt(v));
    const legs = r.strategy.legs.map((l) => `<li>${l.action} ${l.lots} lot(s) ${l.option_type === "FUT" ? "underlying" : `${fmt(l.strike, l.strike % 1 ? 2 : 0)} ${l.option_type}`} ${esc(l.expiry || "")} @ ${fmt(l.premium)}${l.iv ? ` · IV ${(l.iv * 100).toFixed(1)}%` : ""}</li>`).join("");
    $("#builder-stats").innerHTML = `
      <div class="card"><h3>Payoff</h3><div class="kv">
        <div>Net premium</div><div class="${r.net_premium >= 0 ? "pos" : "neg"}">${fmt(r.net_premium)} (${r.premium_type})</div>
        <div>Max profit</div><div class="pos">${money(r.max_profit, r.unlimited_profit)}</div>
        <div>Max loss</div><div class="neg">${money(r.max_loss, r.unlimited_loss)}</div>
        <div>Breakevens</div><div>${r.breakevens.map((b) => fmt(b)).join(", ") || "–"}</div>
        <div>Reward : risk</div><div>${r.reward_risk ?? "–"}</div>
        <div>Prob. of profit</div><div>${r.pop === null ? "–" : (r.pop * 100).toFixed(0) + "%"}</div>
        <div>P&amp;L now (model)</div><div>${fmt(r.current_pnl)}</div>
      </div></div>
      <div class="card"><h3>Position Greeks</h3><div class="kv">
        <div>Delta</div><div>${fmt(g.delta, 2)}</div>
        <div>Gamma</div><div>${fmt(g.gamma, 4)}</div>
        <div>Theta / day</div><div>${fmt(g.theta, 2)}</div>
        <div>Vega / vol pt</div><div>${fmt(g.vega, 2)}</div>
      </div><p class="muted">Lot size ${r.strategy.lot_size}. Prices from ${esc(r.source.toUpperCase())} at ${esc(new Date(r.chain_fetched_at).toLocaleTimeString())}.</p></div>
      <div class="card"><h3>Legs as priced</h3><ul class="actions">${legs}</ul></div>`;
  }

  /* ---------- save ---------- */
  async function saveStrategy() {
    const msg = $("#save-msg");
    const name = $("#save-name").value.trim() || state.name.replace(/ \(modified\)$/, "") + " (mine)";
    if (!state.legs.length) { msg.textContent = "Add some legs first."; return; }
    try {
      const saved = await api("/api/strategy/saved/from-legs", {
        method: "POST",
        body: JSON.stringify({
          name, ...App.params(), expiry: App.chain.expiry, view: $("#save-view").value, description: $("#save-desc").value,
          legs: state.legs.map((l) => ({ option_type: l.option_type, action: l.action, strike: Number(l.strike) || 0, lots: l.lots, expiry: l.expiry })),
        }),
      });
      msg.textContent = `Saved as "${saved.name}". It is now in the strategy list and can be put on any chain.`;
      await loadTemplates(saved.id);
    } catch (e) {
      msg.textContent = `Save failed: ${e.message}`;
    }
  }

  /* ---------- export / import ---------- */
  function exportTemplate() {
    const t = currentTemplate();
    if (!t) return;
    const data = { name: t.name, view: t.view, description: t.description, tags: t.tags, legs: t.legs, format: "option-trader-strategy/1" };
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    a.download = `${t.id}.strategy.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  async function importTemplate(file) {
    try {
      const data = JSON.parse(await file.text());
      const saved = await api("/api/strategy/saved", {
        method: "POST",
        body: JSON.stringify({ name: data.name, view: data.view || "any", description: data.description || "",
          tags: data.tags || [], legs: data.legs, overwrite: false }),
      });
      await loadTemplates(saved.id);
      App.showBanner(`Imported "${saved.name}". Press "Put on current chain" to use it.`, "info");
    } catch (e) {
      App.showBanner(`Import failed: ${e.message}`);
    }
  }

  /* ---------- wiring ---------- */
  $("#tpl-export").addEventListener("click", exportTemplate);
  $("#tpl-import").addEventListener("change", (ev) => { if (ev.target.files[0]) importTemplate(ev.target.files[0]); ev.target.value = ""; });
  $("#greek-pick").addEventListener("change", () => { if (state.lastResult) drawGreeks(state.lastResult); });
  $("#tpl-select").addEventListener("change", describeTemplate);
  $("#tpl-apply").addEventListener("click", applyTemplate);
  $("#tpl-delete").addEventListener("click", deleteTemplate);
  $("#leg-add").addEventListener("click", () => addLeg({}));
  $("#legs-clear").addEventListener("click", () => { state.legs = []; state.name = "Custom"; renderLegs(); scheduleAnalyze(0); });
  $("#save-btn").addEventListener("click", saveStrategy);
  for (const id of ["#target-days", "#iv-shift", "#range-pct"]) {
    $(id).addEventListener("input", () => { updateScenarioLabels(); scheduleAnalyze(); });
  }

  App.builderLegs = () => ({ name: state.name, legs: state.legs.map((l) => ({ ...l, strike: Number(l.strike) || 0 })) });

  App.addLeg = (leg) => { addLeg(leg); App.showBanner(`Added ${leg.action} ${leg.strike} ${leg.option_type} to the Strategy Builder`, "info"); setTimeout(() => App.showBanner(null), 2500); };
  App.openStrategy = (name, legs) => setLegs(name, legs.map((l) => ({ ...l, premium: null })));
  // Plotly cannot size a chart inside a hidden tab: redraw when the builder becomes visible.
  window.addEventListener("themechange", () => { if (state.lastResult) { drawChart(state.lastResult); drawGreeks(state.lastResult); } });

  App.onTabShown = (name) => {
    if (name === "builder" && $("#payoff")._fullLayout) {
      requestAnimationFrame(() => { Plotly.Plots.resize("payoff"); if ($("#greeks-chart")._fullLayout) Plotly.Plots.resize("greeks-chart"); });
    }
  };

  let targetInitialised = false;
  App.onChain((data, meta) => {
    if (meta && meta.live) {
      // Streaming tick: refresh the live-price column without rebuilding inputs (keeps focus),
      // and re-analyse at most every few seconds.
      document.querySelectorAll("#legs-body tr[data-i]").forEach((tr) => {
        const leg = state.legs[Number(tr.dataset.i)];
        const cell = tr.querySelector("td.live");
        if (leg && cell) { const v = liveQuote(leg); cell.textContent = v === null ? "srv" : fmt(v); }
      });
      if (!state.analyzeTimer) scheduleAnalyze(3000);
      return;
    }
    if (!targetInitialised) {
      // Default the target-date curve to halfway to expiry.
      $("#target-days").max = String(Math.max(0.5, Math.floor(daysToExpiry() * 2) / 2));
      $("#target-days").value = String(Math.round(daysToExpiry()) / 2);
      targetInitialised = true;
    }
    // Live prices changed (refresh / new expiry): keep legs that still exist and re-price.
    const strikes = chainStrikes();
    state.legs = state.legs.filter((l) => l.option_type === "FUT" || l.expiry !== App.chain.expiry || strikes.includes(Number(l.strike)));
    state.legs.forEach((l) => { if (!chainExpiries().includes(l.expiry)) l.expiry = App.chain.expiry; });
    renderLegs();
    scheduleAnalyze(0);
  });

  loadTemplates().catch((e) => App.showBanner(`Could not load strategies: ${e.message}`));
  renderLegs();
  updateScenarioLabels();
})();
