/* Option Trader UI: live option chain, status, signals and trade ideas. */
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtInt = (v) => (v === null || v === undefined ? "–" : Math.round(v).toLocaleString("en-IN"));
const pct = (v, d = 1) => (v === null || v === undefined ? "–" : (v * 100).toFixed(d) + "%");
const signCls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");

const App = {
  chain: null,
  timer: null,
  listeners: [],
  onChain(fn) { this.listeners.push(fn); },
  params() {
    const p = { symbol: $("#symbol").value.trim().toUpperCase() };
    if ($("#expiry").value) p.expiry = $("#expiry").value;
    if ($("#source").value) p.source = $("#source").value;
    return p;
  },
};
window.App = App;

async function api(path, opts = {}) {
  const resp = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  let body = null;
  try { body = await resp.json(); } catch (_) { /* non-JSON */ }
  if (!resp.ok) {
    const detail = body && body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : resp.statusText;
    const err = new Error(detail);
    err.status = resp.status;
    throw err;
  }
  return body;
}
App.api = api;

function qs(obj) {
  return new URLSearchParams(Object.entries(obj).filter(([, v]) => v !== undefined && v !== "")).toString();
}
App.qs = qs;

function showBanner(msg, kind = "error") {
  const b = $("#banner");
  if (!msg) { b.classList.add("hidden"); return; }
  b.textContent = msg;
  b.className = "banner" + (kind === "info" ? " info" : "");
}
App.showBanner = showBanner;

/* ---------------- connectivity ---------------- */
async function refreshStatus() {
  const dot = $("#online-dot"), text = $("#online-text");
  if (!navigator.onLine) {
    dot.className = "dot off"; text.textContent = "Offline (no network)"; return false;
  }
  try {
    const st = await api("/api/status");
    dot.className = "dot " + (st.online ? "on" : "off");
    const configured = st.providers.filter((p) => p.configured).map((p) => p.name).join(", ");
    text.textContent = st.online ? `Online · sources: ${configured}` : "Offline: live data unavailable";
    return st.online;
  } catch (e) {
    dot.className = "dot off"; text.textContent = "Server unreachable"; return false;
  }
}
window.addEventListener("online", () => { refreshStatus(); loadChain(); });
window.addEventListener("offline", refreshStatus);

/* ---------------- expiries & chain ---------------- */
async function loadExpiries() {
  const p = App.params();
  const sel = $("#expiry");
  const prev = sel.value;
  sel.innerHTML = "";
  const data = await api("/api/expiries?" + qs({ symbol: p.symbol, source: p.source }));
  for (const e of data.expiries) {
    const o = document.createElement("option");
    o.value = e; o.textContent = e;
    sel.appendChild(o);
  }
  if (data.expiries.includes(prev)) sel.value = prev;
}

function clearChain() {
  App.chain = null;
  $("#chain-table tbody").innerHTML = "";
  $("#chain-meta").innerHTML = "";
}

async function loadChain(refresh = false) {
  const p = App.params();
  if (!p.symbol) return;
  try {
    const data = await api("/api/chain?" + qs({ ...p, refresh: refresh ? "true" : "" }));
    App.chain = data;
    showBanner(null);
    renderChain(data);
    App.listeners.forEach((fn) => fn(data));
  } catch (e) {
    // Never keep showing old numbers as if they were live.
    clearChain();
    showBanner(`Live data unavailable for ${p.symbol}: ${e.message}`);
  }
}
App.loadChain = loadChain;

function renderChain(ch) {
  const ts = new Date(ch.fetched_at).toLocaleTimeString();
  $("#chain-meta").innerHTML = `
    <span>Spot <b>${fmt(ch.spot)}</b></span>
    <span>ATM <b>${fmt(ch.atm_strike, 0)}</b></span>
    <span>Expiry <b>${esc(ch.expiry)}</b></span>
    <span>Lot size <b>${ch.lot_size ?? "–"}</b></span>
    <span>Source <b>${esc(ch.source.toUpperCase())}</b></span>
    <span>Fetched <b>${esc(ts)}</b>${ch.exchange_timestamp ? ` · exchange time <b>${esc(ch.exchange_timestamp)}</b>` : ""}</span>
    ${ch.notes.map((n) => `<span class="muted">${esc(n)}</span>`).join("")}`;

  const maxOI = Math.max(1, ...ch.rows.flatMap((r) => [r.call?.oi || 0, r.put?.oi || 0]));
  const oiCell = (q, side) => {
    const w = q && q.oi ? (q.oi / maxOI) * 100 : 0;
    return `<td class="oi-cell"><div class="oi-bar ${side}" style="width:${w}%"></div><span>${fmtInt(q?.oi)}</span></td>`;
  };
  const ltpCell = (q, type, strike) =>
    q && q.ltp ? `<td class="ltp" data-type="${type}" data-strike="${strike}" title="Add to Strategy Builder">${fmt(q.ltp)}</td>` : `<td>${fmt(q?.ltp)}</td>`;

  const rows = ch.rows.map((r) => {
    const c = r.call, p = r.put;
    const atm = r.strike === ch.atm_strike ? "atm" : "";
    const citm = r.strike < ch.spot ? "itm" : "";
    const pitm = r.strike > ch.spot ? "itm" : "";
    const tdC = (v, extra = "") => `<td class="${citm} ${extra}">${v}</td>`;
    const tdP = (v, extra = "") => `<td class="${pitm} ${extra}">${v}</td>`;
    return `<tr class="${atm}">
      ${oiCell(c, "call")}
      ${tdC(fmtInt(c?.oi_change), signCls(c?.oi_change))}
      ${tdC(fmtInt(c?.volume))}
      ${tdC(pct(c?.iv))}
      ${tdC(fmt(c?.delta, 2))}
      ${tdC(fmt(c?.bid))}
      ${tdC(fmt(c?.ask))}
      ${ltpCell(c, "CE", r.strike)}
      <td class="strike">${fmt(r.strike, r.strike % 1 ? 2 : 0)}</td>
      ${ltpCell(p, "PE", r.strike)}
      ${tdP(fmt(p?.bid))}
      ${tdP(fmt(p?.ask))}
      ${tdP(fmt(p?.delta, 2))}
      ${tdP(pct(p?.iv))}
      ${tdP(fmtInt(p?.volume))}
      ${tdP(fmtInt(p?.oi_change), signCls(p?.oi_change))}
      ${oiCell(p, "put")}
    </tr>`;
  });
  $("#chain-table tbody").innerHTML = rows.join("");
  const atmRow = $("#chain-table tr.atm");
  if (atmRow && !App._scrolled) { atmRow.scrollIntoView({ block: "center" }); App._scrolled = true; }
}

$("#chain-table").addEventListener("click", (ev) => {
  const td = ev.target.closest("td.ltp");
  if (!td || !App.addLeg) return;
  App.addLeg({ option_type: td.dataset.type, strike: Number(td.dataset.strike), action: ev.shiftKey ? "SELL" : "BUY" });
});

/* ---------------- signals & ideas ---------------- */
function signalRows(sigs) {
  return sigs.map((s) => `<tr><td>${esc(s.name)}</td><td><span class="pill ${s.bias}">${s.bias}</span></td>
    <td>${(s.strength * 100).toFixed(0)}%</td><td>${esc(s.detail)}</td></tr>`).join("");
}

function moneyOrUnlimited(v, unlimited) {
  return unlimited ? "Unlimited" : fmt(v);
}

function renderSignals(d) {
  const v = d.view, ca = d.chain_analytics, em = ca.expected_move, t = d.technicals;
  const ideas = d.ideas.map((i, n) => `
    <div class="card">
      <h3>${n + 1}. ${esc(i.name)} <span class="muted">(rank ${i.rank.toFixed(2)})</span></h3>
      <p class="muted">${esc(i.description)}</p>
      <ul class="actions">${i.actions.map((a) => `<li>${esc(a)}</li>`).join("")}</ul>
      <div class="kv">
        <div>Net premium</div><div class="${signCls(i.net_premium)}">${fmt(i.net_premium)} (${i.premium_type})</div>
        <div>Max profit</div><div>${moneyOrUnlimited(i.max_profit, i.unlimited_profit)}</div>
        <div>Max loss</div><div>${moneyOrUnlimited(i.max_loss, i.unlimited_loss)}</div>
        <div>Breakevens</div><div>${i.breakevens.map((b) => fmt(b)).join(", ") || "–"}</div>
        <div>Prob. of profit</div><div>${i.pop === null ? "–" : pct(i.pop, 0)}</div>
        <div>Reward : risk</div><div>${i.reward_risk ?? "–"}</div>
      </div>
      <button class="open-idea" data-idx="${n}">Open in Strategy Builder</button>
    </div>`).join("");

  const tech = t ? `
    <div class="card"><h3>Technicals (${esc(d.symbol)})</h3><div class="kv">
      <div>Close</div><div>${fmt(t.values.close)}</div>
      <div>RSI 14</div><div>${fmt(t.values.rsi14, 1)}</div>
      <div>EMA 20 / 50</div><div>${fmt(t.values.ema20)} / ${fmt(t.values.ema50)}</div>
      <div>200-day SMA</div><div>${fmt(t.values.sma200)}</div>
      <div>MACD hist</div><div class="${signCls(t.values.macd_hist)}">${fmt(t.values.macd_hist, 3)}</div>
      <div>Supertrend</div><div>${fmt(t.values.supertrend)} (${t.values.supertrend_dir === 1 ? "up" : "down"})</div>
      <div>ADX</div><div>${fmt(t.values.adx, 1)}</div>
      <div>HV 20d</div><div>${pct(t.values.hv20)} (pctile ${fmt(t.values.hv20_percentile, 0)})</div>
    </div><p class="muted">${esc(t.volatility.detail || "")}</p></div>` : "";

  $("#signals-out").innerHTML = `
    <div class="cards">
      <div class="card">
        <h3>Market view: <span class="pill ${v.direction}">${v.direction.toUpperCase()}</span></h3>
        <div class="kv">
          <div>Composite score</div><div class="${signCls(v.score)}">${v.score.toFixed(2)}</div>
          <div>Confidence</div><div>${pct(v.confidence, 0)}</div>
          <div>Option-chain score</div><div>${v.options_score ?? "–"}</div>
          <div>Technical score</div><div>${v.technical_score ?? "–"}</div>
          <div>Volatility</div><div>${esc(v.volatility)}</div>
        </div>
        <ul>${v.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
      </div>
      <div class="card"><h3>Option chain (${esc(d.expiry)})</h3><div class="kv">
        <div>Spot</div><div>${fmt(d.spot)}</div>
        <div>PCR (OI / vol)</div><div>${ca.pcr.pcr_oi ?? "–"} / ${ca.pcr.pcr_volume ?? "–"}</div>
        <div>Max pain</div><div>${fmt(ca.max_pain, 0)}</div>
        <div>Resistance (call OI)</div><div>${ca.levels.resistance.map((x) => fmt(x.strike, 0)).join(", ") || "–"}</div>
        <div>Support (put OI)</div><div>${ca.levels.support.map((x) => fmt(x.strike, 0)).join(", ") || "–"}</div>
        <div>ATM IV</div><div>${pct(em.atm_iv)}</div>
        <div>Expected move (1σ)</div><div>±${fmt(em.one_sd_move)} → ${fmt(em.range_low)} – ${fmt(em.range_high)}</div>
        <div>ATM straddle</div><div>${fmt(em.straddle_price)}</div>
        <div>IV skew (put−call)</div><div>${em && ca.iv_skew !== null ? (ca.iv_skew * 100).toFixed(2) + " pts" : "–"}</div>
      </div></div>
      ${tech}
    </div>
    <h2>What to trade</h2>
    ${d.ideas.length ? `<div class="cards">${ideas}</div>` : `<p class="muted">No strategy could be placed on this chain.</p>`}
    <h2>All signals</h2>
    <div class="table-wrap"><table class="simple"><thead><tr><th>Signal</th><th>Bias</th><th>Strength</th><th>Detail</th></tr></thead>
      <tbody>${signalRows([...ca.signals, ...(t ? t.signals : [])])}</tbody></table></div>
    ${d.notes.map((n) => `<p class="muted">${esc(n)}</p>`).join("")}
    <p class="muted">Source: ${esc(d.source.toUpperCase())}, fetched ${esc(new Date(d.fetched_at).toLocaleString())}. ${esc(d.disclaimer)}</p>`;

  $$(".open-idea").forEach((btn) => btn.addEventListener("click", () => {
    const idea = d.ideas[Number(btn.dataset.idx)];
    if (App.openStrategy) { App.openStrategy(idea.name, idea.legs); switchTab("builder"); }
  }));
}

async function loadSignals() {
  const btn = $("#load-signals");
  btn.disabled = true; btn.textContent = "Analysing…";
  try {
    const d = await api("/api/analytics/recommendations?" + qs({ ...App.params(), lots: $("#idea-lots").value }));
    renderSignals(d);
  } catch (e) {
    $("#signals-out").innerHTML = `<div class="banner">Cannot analyse: ${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "Analyse now";
  }
}
$("#load-signals").addEventListener("click", loadSignals);

/* ---------------- tabs, controls, refresh ---------------- */
function switchTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => p.classList.toggle("active", p.id === "tab-" + name));
  if (App.onTabShown) App.onTabShown(name);
}
App.switchTab = switchTab;
$$(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));

$("#controls").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  App._scrolled = false;
  $("#expiry").value = "";
  try {
    await loadExpiries();
  } catch (e) {
    clearChain();
    $("#expiry").innerHTML = "";
    showBanner(`Live data unavailable for ${App.params().symbol}: ${e.message}`);
    return;
  }
  await loadChain(true);
});
$("#expiry").addEventListener("change", () => { App._scrolled = false; loadChain(); });
$("#source").addEventListener("change", () => $("#controls").requestSubmit());

function scheduleRefresh() {
  clearInterval(App.timer);
  if ($("#autorefresh").checked) {
    App.timer = setInterval(async () => {
      if (document.hidden) return;
      if (await refreshStatus()) loadChain(true);
    }, 30000);
  }
}
$("#autorefresh").addEventListener("change", scheduleRefresh);

(async function init() {
  const online = await refreshStatus();
  if (!online) showBanner("No internet connection: connect to the internet to load live market data.", "info");
  scheduleRefresh();
  $("#controls").requestSubmit();
})();
