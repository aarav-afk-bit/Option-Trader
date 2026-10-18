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
    const c = App.colors();
    const pos = y.map((v) => (v >= 0 ? v : null)), neg = y.map((v) => (v < 0 ? v : null));
    const targetLabel = `On ${new Date(r.target).toLocaleDateString()}`;
    const traces = [
      { x, y: pos, name: "Profit zone", mode: "lines", line: { width: 0 }, fill: "tozeroy", fillcolor: App.alpha(c.profit, 0.12), hoverinfo: "skip", showlegend: false },
      { x, y: neg, name: "Loss zone", mode: "lines", line: { width: 0 }, fill: "tozeroy", fillcolor: App.alpha(c.loss, 0.12), hoverinfo: "skip", showlegend: false },
      // neon glow: a wide translucent copy under the expiry line (decorative, not a series)
      { x, y, mode: "lines", line: { color: App.alpha(c.s1, 0.22), width: 9 }, hoverinfo: "skip", showlegend: false },
      { x, y, name: "At expiry", mode: "lines", line: { color: c.s1, width: 2 } },
      { x, y: r.pnl_target, name: targetLabel, mode: "lines", line: { color: c.s2, width: 2, dash: "dash" } },
      { x, y: r.pnl_today, name: "Today", mode: "lines", line: { color: c.s3, width: 2, dash: "dot" } },
      { x: r.breakevens, y: r.breakevens.map(() => 0), name: "Breakeven", mode: "markers+text", text: r.breakevens.map((b) => fmt(b, 0)),
        textposition: "top center", textfont: { color: c.text, size: 11 }, marker: { color: c.text, size: 9, symbol: "diamond", line: { color: App.css("--panel"), width: 2 } } },
    ];
    const ymax = Math.max(...y.filter((v) => v != null)), ymin = Math.min(...y.filter((v) => v != null));
    const layout = App.plotLayout({
      margin: { l: 70, r: 20, t: 40, b: 50 },
      title: { text: `${esc(r.strategy.name)} · ${esc(r.symbol)} · spot ${fmt(r.spot)} · ${esc(r.source.toUpperCase())}`, font: { size: 14, color: c.text } },
      xaxis: { title: "Underlying price" },
      yaxis: { title: "Profit / loss (₹)", zerolinecolor: c.muted, zerolinewidth: 1 },
      legend: { orientation: "h", y: -0.18, font: { color: c.text } },
      shapes: [{ type: "line", x0: r.spot, x1: r.spot, yref: "paper", y0: 0, y1: 1, line: { color: c.accent, dash: "dot", width: 1 } }],
      annotations: [
        { x: r.spot, yref: "paper", y: 1, text: "SPOT", showarrow: false, font: { size: 10, color: c.accent, family: App.css("--font-display") }, yanchor: "bottom" },
        ...(ymax > 0 ? [{ xref: "paper", x: 0.01, y: ymax, text: "Profit", showarrow: false, xanchor: "left", font: { size: 11, color: c.muted } }] : []),
        ...(ymin < 0 ? [{ xref: "paper", x: 0.99, y: ymin, text: "Loss", showarrow: false, xanchor: "right", yanchor: "bottom", font: { size: 11, color: c.muted } }] : []),
      ],
    });
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
    const col = App.colors();
    Plotly.react("greeks-chart", [
      { x: c.prices, y: c[k], mode: "lines", line: { color: App.alpha(col.s1, 0.22), width: 9 }, hoverinfo: "skip", showlegend: false },
      { x: c.prices, y: c[k], mode: "lines", line: { color: col.s1, width: 2 }, name: k }],
      App.plotLayout({ showlegend: false, xaxis: { title: "Underlying price" }, yaxis: { title: $("#greek-pick").selectedOptions[0].text },
        shapes: [{ type: "line", x0: r.spot, x1: r.spot, yref: "paper", y0: 0, y1: 1, line: { color: col.accent, dash: "dot", width: 1 } }] }),
      { responsive: true, displaylogo: false });
  }

  function drawPnlTable(r) {
    const steps = [-5, -3, -2, -1, -0.5, 0, 0.5, 1, 2, 3, 5];
    const targetLabel = new Date(r.target).toLocaleDateString();
    const rows = steps.map((pc) => {
      const px = r.spot * (1 + pc / 100);
      const cell = (ys) => { const v = Math.round(interp(r.prices, ys, px)) + 0; return `<td class="${v >= 0 ? "pos" : "neg"}">${fmt(v, 0)}</td>`; };
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
      ${r.costs ? `<div class="card"><h3>Costs &amp; net</h3><div class="kv">
        <div>Charges to enter</div><div>${fmt(r.costs.entry.total)}</div>
        <div>Round trip (in + out)</div><div>${fmt(r.costs.round_trip)}</div>
        <div title="Cost of crossing the bid/ask spread on entry and exit versus the mid">Spread slippage</div><div>${r.costs.spread_slippage == null ? "–" : fmt(r.costs.spread_slippage)}</div>
        <div>Net max profit</div><div class="pos">${r.unlimited_profit ? "Unlimited" : fmt(r.costs.net_max_profit)}</div>
        <div>Net max loss</div><div class="neg">${r.unlimited_loss ? "Unlimited" : fmt(r.costs.net_max_loss)}</div>
      </div><p class="muted" title="Brokerage ₹${r.costs.rates.brokerage_per_order}/order, STT ${(r.costs.rates.stt_sell * 100).toFixed(3)}% sell, exchange ${(r.costs.rates.exchange_txn * 100).toFixed(5)}%, stamp ${(r.costs.rates.stamp_buy * 100).toFixed(3)}% buy, SEBI, GST ${(r.costs.rates.gst * 100).toFixed(0)}%">Brokerage, STT, exchange, SEBI, stamp duty and GST at current Indian F&amp;O rates (hover for rates).</p></div>` : ""}
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

  /* ---------- optimiser ---------- */
  state.undo = [];
  let optOut = null, animTimer = null;

  function goalLabel() {
    const v = Number($("#opt-goal").value);
    $("#opt-goal-label").textContent = v <= 20 ? "maximise win rate" : v >= 80 ? "maximise profit" : v < 45 ? "lean win rate" : v > 55 ? "lean profit" : "balanced";
  }

  function optBody() {
    const maxLoss = $("#opt-maxloss").value;
    return {
      ...App.params(), expiry: App.chain.expiry, name: state.name,
      legs: state.legs.map((l) => ({ option_type: l.option_type, action: l.action, strike: Number(l.strike) || 0, lots: l.lots, expiry: l.expiry })),
      prefs: {
        win_weight: 1 - Number($("#opt-goal").value) / 100,
        max_loss: maxLoss === "" ? null : Number(maxLoss),
        allow_unlimited_loss: $("#opt-unlimited").checked,
        max_legs: Number($("#opt-maxlegs").value) || 4,
        max_lots_per_leg: Number($("#opt-maxlots").value) || 3,
        max_spread_pct: Number($("#opt-spread").value) || 10,
        robust: $("#opt-robust").checked,
      },
      search: $("#opt-global").checked ? "global" : "local",
      distribution: { mode: $("#opt-dist").value, drift: (Number($("#opt-drift").value) || 0) / 100 },
      fill: $("#tpl-fill").value === "ltp" ? "market" : $("#tpl-fill").value,
    };
  }

  const DIST_LABEL = { implied: "market-implied (from the whole live chain)", auto: "auto (signals + realised vol)",
    atm: "ATM-IV fair-value", realised: "realised-volatility" };
  const pctv = (v) => (v == null ? "–" : (v * 100).toFixed(1) + "%");
  function cmpRow(label, a, b, f, higherBetter = true) {
    const d = a == null || b == null ? "" : (b > a) === higherBetter && b !== a ? "better" : b !== a ? "worse" : "";
    return `<tr><td>${label}</td><td>${a == null ? "–" : f(a)}</td><td class="${d}">${b == null ? "–" : f(b)}</td></tr>`;
  }
  const legLine = (l) => `${l.action} ${l.lots}× ${l.option_type === "FUT" ? "underlying" : fmt(l.strike, l.strike % 1 ? 2 : 0) + " " + l.option_type}${l.premium ? " @ " + fmt(l.premium) : ""}`;

  function renderOpt(out) {
    const el = $("#opt-result");
    if (!out.best) { el.innerHTML = `<p class="neg">${esc(out.message || "No better strategy found under these limits.")}</p>`; return; }
    const a = out.start.metrics || {}, b = out.best.metrics;
    el.innerHTML = `
      <h3 style="margin-top:10px">${out.improved ? "Optimised strategy" : "Already optimal for these settings"}</h3>
      <div class="table-wrap"><table class="simple opt-compare">
        <thead><tr><th></th><th>Current</th><th>Optimised</th></tr></thead><tbody>
        ${cmpRow("Score", out.start.score, out.best.score, (v) => v.toFixed(3))}
        ${cmpRow("Win rate", a.win_rate, b.win_rate, pctv)}
        ${cmpRow("Expected P&amp;L", a.expected_pnl, b.expected_pnl, (v) => fmt(v))}
        ${cmpRow("Max profit", a.unlimited_profit ? Infinity : a.max_profit, b.unlimited_profit ? Infinity : b.max_profit, (v) => (v === Infinity ? "Unlimited" : fmt(v)))}
        ${cmpRow("Max loss", a.max_loss, b.max_loss, (v) => (b.unlimited_loss && v === b.max_loss ? "Unlimited" : fmt(v)))}
        ${cmpRow("Net premium", a.net_premium, b.net_premium, (v) => fmt(v))}
        ${cmpRow("Charges", a.charges, b.charges, (v) => fmt(v), false)}
      </tbody></table></div>
      <p><b>Legs:</b> ${out.best.legs.map((l) => esc(legLine(l))).join(", ")}</p>
      ${out.seeds ? `<p class="muted">Searched from: ${out.seeds.map(esc).join(", ")}${out.rejected_illiquid ? ` · skipped ${out.rejected_illiquid} candidates with spreads over ${out.prefs.max_spread_pct}%` : ""}</p>` : ""}
      ${out.steps.length ? `<p class="muted">How it got there (${out.evaluated} live-priced candidates tried):</p>
        <ol class="opt-steps">${out.steps.map((s, i) => `<li data-step="${i}">${esc(s.move)} <span class="muted">→ score ${s.score == null ? "–" : s.score.toFixed(3)}, win ${pctv(s.metrics && s.metrics.win_rate)}</span></li>`).join("")}</ol>` : ""}
      <div class="row">
        ${out.improved ? `<button id="opt-apply" class="primary">Apply</button><button id="opt-animate">Step through on the graph</button>` : ""}
        <button id="opt-undo" ${state.undo.length ? "" : "disabled"}>Undo</button>
      </div>
      <p class="muted">Win rate and expected P&amp;L are at the nearest expiry, after charges, using the ${esc(DIST_LABEL[out.distribution.kind] || out.distribution.kind)} price model${out.distribution.drift ? ` with a ${(out.distribution.drift * 100).toFixed(2)}% expected move` : ""}${out.distribution.view ? ` (signals: ${esc(out.distribution.view)})` : ""}.
        ${(out.distribution.reasons || []).length ? `<br>Why: ${out.distribution.reasons.map(esc).join("; ")}.` : ""} Prices: live ${esc(out.source.toUpperCase())} chain at ${esc(new Date(out.fetched_at).toLocaleTimeString())}. Not a guarantee of future results.</p>`;
    const ap = $("#opt-apply"), an = $("#opt-animate"), un = $("#opt-undo");
    if (ap) ap.addEventListener("click", () => applyLegs("Optimised " + state.name.replace(/^Optimised /, ""), out.best.legs));
    if (an) an.addEventListener("click", () => animate(out));
    if (un) un.addEventListener("click", undo);
  }

  function applyLegs(name, legs) {
    state.undo.push({ name: state.name, legs: state.legs.map((l) => ({ ...l })) });
    setLegs(name, legs.map((l) => ({ ...l, premium: null })));
    const un = $("#opt-undo"); if (un) un.disabled = false;
  }

  function undo() {
    clearInterval(animTimer);
    const prev = state.undo.pop();
    if (!prev) return;
    setLegs(prev.name, prev.legs);
    const un = $("#opt-undo"); if (un) un.disabled = !state.undo.length;
  }

  function animate(out) {
    // Move the strategy toward the optimum one step at a time so the payoff graph shows the path.
    clearInterval(animTimer);
    state.undo.push({ name: state.name, legs: state.legs.map((l) => ({ ...l })) });
    let i = 0;
    const show = () => {
      document.querySelectorAll("ol.opt-steps li").forEach((li) => li.classList.toggle("active", Number(li.dataset.step) === i));
      const s = out.steps[i];
      setLegs(`Optimising… step ${i + 1}/${out.steps.length}`, s.legs.map((l) => ({ ...l, premium: null })));
      i += 1;
      if (i >= out.steps.length) { clearInterval(animTimer); setTimeout(() => { state.name = "Optimised"; }, 0); }
    };
    show();
    animTimer = setInterval(show, 1200);
    const un = $("#opt-undo"); if (un) un.disabled = false;
  }

  async function optimise() {
    const btn = $("#opt-run");
    if (!App.chain) { App.showBanner("Load a live option chain first."); return; }
    if (!state.legs.length && !$("#opt-global").checked) { App.showBanner("Add at least one leg, or tick “Search all strategy types”."); return; }
    btn.disabled = true; btn.textContent = "Optimising…";
    $("#opt-result").innerHTML = `<p class="muted">${$("#opt-global").checked ? "Searching every strategy type" : "Searching nearby strategies"} on live prices…</p>`;
    document.getElementById("opt-card").scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      optOut = await api("/api/strategy/optimize", { method: "POST", body: JSON.stringify(optBody()) });
      renderOpt(optOut);
    } catch (e) {
      $("#opt-result").innerHTML = `<p class="neg">Optimise failed: ${esc(e.message)}</p>`;
    } finally {
      btn.disabled = false; btn.textContent = "✨ Optimise";
    }
  }
  $("#opt-run").addEventListener("click", optimise);
  $("#opt-goal").addEventListener("input", goalLabel);
  goalLabel();

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
