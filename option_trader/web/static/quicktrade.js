/* Quick trade wizard: three plain questions -> an optimised trade explained in plain words. */
"use strict";

(function () {
  const { api } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inr = (v, d = 0) => (v == null ? "–" : (v < 0 ? "-$" : "$") + Math.abs(Number(v)).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const num = (v, d = 0) => Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

  const VIEWS = [
    ["up", "📈", "It will go up", "Profit if the price rises."],
    ["down", "📉", "It will go down", "Profit if the price falls."],
    ["range", "↔️", "It will stay in a range", "Profit if it doesn't move much."],
    ["breakout", "💥", "Big move, either way", "Profit from a large move up or down."],
    ["auto", "🤖", "Not sure — let the app decide", "Uses the app's live signals and recent volatility."],
  ];
  const state = { step: 0, view: null, strength: 0.5, risk: 5000, goal: 50, result: null };

  function open() {
    if (!App.chain) { App.showBanner("Load a live option chain first."); return; }
    Object.assign(state, { step: 0, view: null, result: null });
    $("#qt-sym").textContent = `${App.chain.symbol} · expiry ${App.chain.expiry} · spot ${num(App.chain.spot, 2)}`;
    $("#qt").classList.remove("hidden");
    render();
  }
  function close() { $("#qt").classList.add("hidden"); }
  App.quickTrade = open;
  App.closeQuickTrade = close;
  $("#qt-open").addEventListener("click", open);
  $("#qt-close").addEventListener("click", close);
  $("#qt").addEventListener("click", (e) => { if (e.target.id === "qt") close(); });

  function steps() {
    return `<div class="qt-steps">${["Your view", "Risk", "Your trade"].map((t, i) =>
      `<span class="${i === state.step ? "on" : i < state.step ? "done" : ""}">${i + 1}. ${t}</span>`).join("")}</div>`;
  }

  function render() {
    const body = $("#qt-body");
    if (state.step === 0) {
      body.innerHTML = steps() + `<p>What do you think <b>${esc(App.chain.symbol)}</b> will do by <b>${esc(App.chain.expiry)}</b>?</p>
        <div class="qt-views">${VIEWS.map(([k, ic, t, d]) => `<button type="button" class="qt-view ${state.view === k ? "sel" : ""}" data-view="${k}">
          <span class="qt-ic">${ic}</span><b>${t}</b><small>${d}</small></button>`).join("")}</div>
        ${state.view === "up" || state.view === "down" ? `<label>How strongly? <select id="qt-strength">
          <option value="0.5" ${state.strength === 0.5 ? "selected" : ""}>A little (about half the move options are pricing)</option>
          <option value="1" ${state.strength === 1 ? "selected" : ""}>A lot (about the full move options are pricing)</option></select></label>` : ""}
        <div class="row qt-nav"><span></span><button type="button" class="primary" id="qt-next" ${state.view ? "" : "disabled"}>Next</button></div>`;
      body.querySelectorAll(".qt-view").forEach((b) => b.addEventListener("click", () => { state.view = b.dataset.view; render(); }));
      const st = $("#qt-strength"); if (st) st.addEventListener("change", () => { state.strength = Number(st.value); });
      $("#qt-next").addEventListener("click", () => { state.step = 1; render(); });
    } else if (state.step === 1) {
      const lot = App.chain.lot_size || 1;
      body.innerHTML = steps() + `<p>How much are you willing to lose at most on this trade?</p>
        <label>Maximum loss <input id="qt-risk" type="number" min="500" step="500" value="${state.risk}"></label>
        <p class="muted">One contract covers ${lot} shares. The trade will never risk more than this (after charges).</p>
        <label><span>What matters more?</span>
          <div class="slider-ends"><span>Winning more often</span><span>Bigger profits</span></div>
          <input id="qt-goal" type="range" min="0" max="100" step="10" value="${state.goal}"></label>
        <div class="row qt-nav"><button type="button" id="qt-back">Back</button><button type="button" class="primary" id="qt-go">Find my trade</button></div>`;
      $("#qt-back").addEventListener("click", () => { state.step = 0; render(); });
      $("#qt-go").addEventListener("click", () => {
        state.risk = Math.max(100, Number($("#qt-risk").value) || 5000);
        state.goal = Number($("#qt-goal").value);
        state.step = 2; render(); find();
      });
    } else {
      body.innerHTML = steps() + `<div id="qt-result"><p class="muted">Searching every strategy type on live prices…</p></div>`;
      if (state.result) showResult();
    }
  }

  async function find() {
    try {
      const out = await api("/api/strategy/optimize", { method: "POST", body: JSON.stringify({
        ...App.params(), expiry: App.chain.expiry, legs: [], search: "global",
        prefs: { win_weight: 1 - state.goal / 100, max_loss: state.risk, allow_unlimited_loss: false, max_legs: 4, max_lots_per_leg: 3 },
        distribution: { view: state.view, strength: state.strength }, fill: "market",
      }) });
      if (!out.best) { $("#qt-result").innerHTML = `<p class="neg">No trade fits a maximum loss of ${inr(state.risk)} right now. Try a larger risk amount.</p>`; return; }
      const legs = out.best.legs.map((l) => ({ option_type: l.option_type, action: l.action, strike: l.strike, lots: l.lots }));
      const an = await api("/api/strategy/analyze", { method: "POST", body: JSON.stringify({ ...App.params(), expiry: App.chain.expiry, legs, fill: "market" }) });
      state.result = { out, an };
      showResult();
    } catch (e) {
      $("#qt-result").innerHTML = `<p class="neg">Couldn't build a trade: ${esc(e.message)}</p>`;
    }
  }

  function sentence(l, lot, sym) {
    const qty = l.lots * lot;
    const what = l.option_type === "FUT" ? `${sym} shares` : `the ${num(l.strike)} ${l.option_type === "CE" ? "Call" : "Put"}`;
    return `<b class="${l.action === "BUY" ? "pos" : "neg"}">${l.action === "BUY" ? "Buy" : "Sell"}</b> ${l.option_type === "FUT" ? `${qty} shares` : `${l.lots} contract${l.lots > 1 ? "s" : ""}`} of ${what} at about <b>${inr(l.premium, 2)}</b>`;
  }

  function showResult() {
    const { out, an } = state.result;
    const m = out.best.metrics, lot = an.strategy.lot_size, sym = an.symbol;
    const be = an.breakevens;
    const beText = !be.length ? "" : be.length === 1
      ? `You break even if ${esc(sym)} ends ${an.pnl_expiry[an.pnl_expiry.length - 1] > 0 ? "above" : "below"} <b>${num(be[0])}</b> at expiry.`
      : `You make money if ${esc(sym)} ends ${an.pnl_expiry[Math.floor(an.pnl_expiry.length / 2)] > 0 ? "between" : "outside"} <b>${num(be[0])}</b> and <b>${num(be[be.length - 1])}</b> at expiry.`;
    const premium = an.net_premium >= 0 ? `You receive <b>${inr(an.net_premium)}</b> up front.` : `It costs <b>${inr(-an.net_premium)}</b> up front.`;
    $("#qt-result").innerHTML = `
      <h4 class="qt-name">${esc(out.steps.length ? out.steps[0].move.replace(/^Start from /, "") : "Custom")} <span class="muted">(optimised)</span></h4>
      <ol class="qt-legs">${an.strategy.legs.map((l) => `<li>${sentence(l, lot, sym)}</li>`).join("")}</ol>
      <div class="qt-stats">
        <div><small>Most you can lose</small><b class="neg">${an.unlimited_loss ? "Unlimited" : inr(-an.costs.net_max_loss)}</b></div>
        <div><small>Most you can make</small><b class="pos">${an.unlimited_profit ? "Unlimited" : inr(an.costs.net_max_profit)}</b></div>
        <div><small>Chance of profit${state.view === "auto" ? "" : " if your view is right"}</small><b>${(m.win_rate * 100).toFixed(0)}%</b></div>
      </div>
      <p>${premium} ${beText} Figures include about ${inr(an.costs.round_trip)} of commission and regulatory fees.</p>
      <p class="muted">Based on live ${esc(an.source.toUpperCase())} prices and ${state.view === "auto" ? "the app's current signals" : "your view"}. This is not a guarantee: markets can do anything.</p>
      <div class="row qt-nav">
        <button type="button" id="qt-restart">Start over</button>
        <span><button type="button" id="qt-paper">Paper trade it</button>
        <button type="button" class="primary" id="qt-builder">Open in Strategy Builder</button></span>
      </div>`;
    const legs = an.strategy.legs;
    $("#qt-restart").addEventListener("click", () => { state.step = 0; state.result = null; render(); });
    $("#qt-builder").addEventListener("click", () => { close(); App.openStrategy("Quick trade", legs); App.switchTab("builder"); });
    $("#qt-paper").addEventListener("click", async () => {
      try {
        await api("/api/portfolio/open", { method: "POST", body: JSON.stringify({ name: "Quick trade", ...App.params(), expiry: App.chain.expiry, fill: "market",
          legs: legs.map((l) => ({ option_type: l.option_type, action: l.action, strike: l.strike, lots: l.lots, expiry: l.expiry })) }) });
        close(); App.switchTab("portfolio");
      } catch (e) { App.showBanner(`Could not open paper trade: ${e.message}`); }
    });
  }
})();
