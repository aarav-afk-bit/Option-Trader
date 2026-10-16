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

/* ---------------- theme ---------------- */
function applyTheme(theme) {
  if (theme === "light") document.documentElement.dataset.theme = "light";
  else delete document.documentElement.dataset.theme;
  $("#theme-toggle").textContent = theme === "light" ? "🌙" : "☀️";
  $("#theme-toggle").title = theme === "light" ? "Switch to dark mode" : "Switch to light mode";
  try { localStorage.setItem("ot-theme", theme); } catch (_) { /* storage unavailable */ }
  window.dispatchEvent(new Event("themechange"));
}
App.theme = () => (document.documentElement.dataset.theme === "light" ? "light" : "dark");
$("#theme-toggle").addEventListener("click", () => applyTheme(App.theme() === "light" ? "dark" : "light"));
applyTheme(App.theme());

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
    ${App.market && !App.market.open ? `<span class="pill bearish">Market closed: last traded prices${ch.exchange_timestamp ? " as of " + esc(ch.exchange_timestamp) : ""}</span>` : ""}
    ${ch.notes.map((n) => `<span class="muted">${esc(n)}</span>`).join("")}`;

  const win = Number($("#strike-window").value);
  const atmIdx = ch.rows.findIndex((r) => r.strike === ch.atm_strike);
  const visible = win > 0 ? ch.rows.slice(Math.max(0, atmIdx - win), atmIdx + win + 1) : ch.rows;

  const maxOI = Math.max(1, ...ch.rows.flatMap((r) => [r.call?.oi || 0, r.put?.oi || 0]));
  // Top-3 open-interest strikes per side are highlighted: they are the market's key levels.
  const topOI = (side) => new Set([...ch.rows].filter((r) => r[side]?.oi).sort((a, b) => b[side].oi - a[side].oi).slice(0, 3).map((r) => r.strike));
  const topCall = topOI("call"), topPut = topOI("put");
  const oiCell = (q, side, top) => {
    const w = q && q.oi ? (q.oi / maxOI) * 100 : 0;
    return `<td class="oi-cell c-oi ${top ? "top-oi" : ""}"><div class="oi-bar ${side}" style="width:${w}%"></div><span>${fmtInt(q?.oi)}</span></td>`;
  };
  const ltpCell = (q, type, strike, itm) =>
    q && q.ltp ? `<td class="ltp ${itm}" data-type="${type}" data-strike="${strike}" title="Add to Strategy Builder (Shift+click to sell)">${fmt(q.ltp)}</td>` : `<td class="${itm}">${fmt(q?.ltp)}</td>`;

  const rows = visible.map((r) => {
    const c = r.call, p = r.put;
    const atm = r.strike === ch.atm_strike ? "atm" : "";
    const citm = r.strike < ch.spot ? "itm" : "";
    const pitm = r.strike > ch.spot ? "itm" : "";
    const tdC = (v, extra = "") => `<td class="${citm} ${extra}">${v}</td>`;
    const tdP = (v, extra = "") => `<td class="${pitm} ${extra}">${v}</td>`;
    return `<tr class="${atm}">
      ${oiCell(c, "call", topCall.has(r.strike))}
      ${tdC(fmtInt(c?.oi_change), "c-oi " + signCls(c?.oi_change))}
      ${tdC(fmtInt(c?.volume), "c-vol")}
      ${tdC(pct(c?.iv), "c-iv")}
      ${tdC(fmt(c?.vega, 2), "c-greeks2")}
      ${tdC(fmt(c?.theta, 2), "c-greeks2")}
      ${tdC(fmt(c?.gamma, 5), "c-greeks2")}
      ${tdC(fmt(c?.delta, 2), "c-greeks")}
      ${tdC(fmt(c?.bid), "c-quote")}
      ${tdC(fmt(c?.ask), "c-quote")}
      ${ltpCell(c, "CE", r.strike, citm)}
      <td class="strike">${fmt(r.strike, r.strike % 1 ? 2 : 0)}</td>
      ${ltpCell(p, "PE", r.strike, pitm)}
      ${tdP(fmt(p?.bid), "c-quote")}
      ${tdP(fmt(p?.ask), "c-quote")}
      ${tdP(fmt(p?.delta, 2), "c-greeks")}
      ${tdP(fmt(p?.gamma, 5), "c-greeks2")}
      ${tdP(fmt(p?.theta, 2), "c-greeks2")}
      ${tdP(fmt(p?.vega, 2), "c-greeks2")}
      ${tdP(pct(p?.iv), "c-iv")}
      ${tdP(fmtInt(p?.volume), "c-vol")}
      ${tdP(fmtInt(p?.oi_change), "c-oi " + signCls(p?.oi_change))}
      ${oiCell(p, "put", topPut.has(r.strike))}
    </tr>`;
  });
  $("#chain-table tbody").innerHTML = rows.join("");
  updateSideColspans();
  if (!App._scrolled) { jumpToATM(); App._scrolled = true; }
}

/* ---------------- chain table tools ---------------- */
const PREF_KEY = "ot-chain-prefs";
function loadPrefs() {
  try { return JSON.parse(localStorage.getItem(PREF_KEY) || "{}"); } catch (_) { return {}; }
}
function savePrefs() {
  const cols = {};
  $$(".chain-tools input[data-col]").forEach((cb) => { cols[cb.dataset.col] = cb.checked; });
  try { localStorage.setItem(PREF_KEY, JSON.stringify({ window: $("#strike-window").value, cols })); } catch (_) { /* ignore */ }
}
function applyColumns() {
  $$(".chain-tools input[data-col]").forEach((cb) => $("#chain-table").classList.toggle("hide-" + cb.dataset.col, !cb.checked));
  updateSideColspans();
}
function updateSideColspans() {
  // Keep the CALLS / PUTS header spanning exactly the visible columns of each side.
  const headRow = $$("#chain-table thead tr")[1];
  const cells = Array.from(headRow.children);
  const strikeAt = cells.findIndex((c) => c.classList.contains("strike"));
  const shown = (c) => getComputedStyle(c).display !== "none";
  const left = cells.slice(0, strikeAt).filter(shown).length;
  const right = cells.slice(strikeAt + 1).filter(shown).length;
  const sides = $$("#chain-table .side-cell");
  sides[0].colSpan = left; sides[1].colSpan = right;
}
function jumpToATM() {
  const atmRow = $("#chain-table tr.atm");
  if (atmRow) atmRow.scrollIntoView({ block: "center" });
}

function chainToCSV(ch) {
  const head = ["strike", "call_oi", "call_oi_change", "call_volume", "call_iv", "call_delta", "call_bid", "call_ask", "call_ltp",
    "put_ltp", "put_bid", "put_ask", "put_delta", "put_iv", "put_volume", "put_oi_change", "put_oi"];
  const v = (x) => (x === null || x === undefined ? "" : x);
  const lines = [
    `# ${ch.symbol} expiry ${ch.expiry} spot ${ch.spot} source ${ch.source} fetched ${ch.fetched_at}${ch.exchange_timestamp ? " exchange " + ch.exchange_timestamp : ""}`,
    head.join(","),
  ];
  for (const r of ch.rows) {
    const c = r.call || {}, p = r.put || {};
    lines.push([r.strike, c.oi, c.oi_change, c.volume, c.iv, c.delta, c.bid, c.ask, c.ltp,
      p.ltp, p.bid, p.ask, p.delta, p.iv, p.volume, p.oi_change, p.oi].map(v).join(","));
  }
  return lines.join("\n") + "\n";
}
App.chainToCSV = chainToCSV;

$("#export-csv").addEventListener("click", () => {
  if (!App.chain) { showBanner("Load a live option chain first."); return; }
  const ch = App.chain;
  const blob = new Blob([chainToCSV(ch)], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${ch.symbol}_${ch.expiry}_${ch.source}_${ch.fetched_at.slice(0, 19).replace(/[:T]/g, "-")}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});
$("#jump-atm").addEventListener("click", jumpToATM);
$("#strike-window").addEventListener("change", () => { savePrefs(); if (App.chain) { App._scrolled = false; renderChain(App.chain); } });
$$(".chain-tools input[data-col]").forEach((cb) => cb.addEventListener("change", () => { savePrefs(); applyColumns(); }));
(function restorePrefs() {
  const prefs = loadPrefs();
  if (prefs.window) $("#strike-window").value = prefs.window;
  for (const [col, on] of Object.entries(prefs.cols || {})) {
    const cb = $(`.chain-tools input[data-col="${col}"]`);
    if (cb) cb.checked = on;
  }
  applyColumns();
})();

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
App.activeTab = () => ($(".tab.active") || {}).dataset?.tab;

/** Plotly layout themed from the current CSS variables. */
App.plotLayout = function (extra = {}) {
  const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const text = css("--text"), grid = css("--border");
  const layout = {
    margin: { l: 60, r: 20, t: 10, b: 45 },
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: text, size: 12 },
    legend: { orientation: "h", y: -0.2 },
    hovermode: "x unified",
    hoverlabel: { bgcolor: css("--panel2"), bordercolor: grid, font: { color: text } },
    ...extra,
  };
  // Every axis (xaxis, yaxis, yaxis2, ...) gets the themed grid colours unless it sets its own.
  for (const key of new Set(["xaxis", "yaxis", ...Object.keys(extra).filter((k) => /^[xy]axis\d*$/.test(k))])) {
    layout[key] = { gridcolor: grid, zerolinecolor: grid, linecolor: grid, ...(extra[key] || {}) };
  }
  return layout;
};
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

/* ---------------- market session ---------------- */
App.market = null;
async function refreshMarket() {
  const badge = $("#market-badge");
  try {
    const m = await api("/api/market");
    App.market = m;
    const label = m.open ? "Market open" : m.state === "pre-open" ? "Pre-open" : "Market closed";
    badge.textContent = label + (m.estimate ? " (est.)" : "");
    badge.className = "pill " + (m.open ? "bullish" : "bearish");
    const next = m.next_open ? ` · opens ${new Date(m.next_open).toLocaleString()}` : "";
    badge.title = `${m.message || m.state}${next}${m.estimate ? " · estimated from the clock" : " · from NSE"}`;
  } catch (e) {
    badge.textContent = "Market ?";
    badge.className = "pill neutral";
  }
  return App.market;
}
App.refreshMarket = refreshMarket;

// Refresh every 30 s while the market is open; every 5 min when closed (prices don't change).
function refreshEveryMs() { return App.market && !App.market.open ? 300000 : 30000; }

function scheduleRefresh() {
  clearTimeout(App.timer);
  if (!$("#autorefresh").checked) return;
  App.timer = setTimeout(async () => {
    if (!document.hidden && (await refreshStatus())) {
      await refreshMarket();
      await loadChain(true);
    }
    scheduleRefresh();
  }, refreshEveryMs());
}
$("#autorefresh").addEventListener("change", scheduleRefresh);

(async function init() {
  const online = await refreshStatus();
  await refreshMarket();
  if (!online) showBanner("No internet connection: connect to the internet to load live market data.", "info");
  scheduleRefresh();
  $("#controls").requestSubmit();
})();
