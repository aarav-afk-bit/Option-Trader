/* Plain-English help: glossary hints on technical terms, a one-line chain summary, and a
   short guided tour for first-time users. */
"use strict";

(function () {
  const $ = (s) => document.querySelector(s);
  const fmt = (v, d = 0) => (v == null ? "–" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));

  /* ---------- glossary ---------- */
  const G = {
    "oi": "Open interest: how many contracts are currently open. Big OI at a strike often acts like a wall the price struggles to cross.",
    "chg oi": "Change in open interest since the previous day. Rising OI means new positions are being opened at that strike.",
    "volume": "How many contracts traded today. High volume = easy to buy and sell at fair prices.",
    "iv": "Implied volatility: how much movement the option price is expecting, per year. Higher IV = more expensive options.",
    "atm iv": "Implied volatility of the at-the-money options: the market's single best estimate of how much the price will move.",
    "delta": "Roughly the chance the option ends in profit, and how much its price moves when the underlying moves ₹1 (0.5 ≈ 50p per ₹1).",
    "gamma": "How fast delta changes as the price moves. High gamma = the position changes character quickly near expiry.",
    "theta": "Time decay: how much value the option loses per day if nothing else changes. Sellers earn it, buyers pay it.",
    "vega": "How much the option price changes when implied volatility moves by 1 point.",
    "spr%": "Bid/ask spread as a % of the price. Small spread = cheap to trade; big spread = you lose more getting in and out.",
    "bid": "The best price someone will pay right now. You sell at the bid.",
    "ask": "The best price someone will sell at right now. You buy at the ask.",
    "ltp": "Last traded price. Click it to add this option to the Strategy Builder (Shift+click to sell).",
    "strike": "The price at which the option can be exercised.",
    "spot": "Current price of the underlying index or stock.",
    "atm": "At the money: the strike closest to the current price.",
    "atm strike": "At the money: the strike closest to the current price.",
    "forward": "The futures-equivalent price the options are priced from (spot plus interest, minus dividends), worked out from live call and put prices.",
    "lot size": "Number of units in one contract. Profits and losses are multiplied by this.",
    "data age": "How long ago the exchange produced these prices. Green = fresh.",
    "pcr": "Put-call ratio of open interest. Above 1: more puts written, usually seen as support (bullish). Below 0.7: call writers dominate (bearish).",
    "pcr (oi)": "Put-call ratio of open interest. Above 1: more puts written, usually seen as support (bullish). Below 0.7: call writers dominate (bearish).",
    "pcr (oi / vol)": "Put-call ratio by open interest and by today's volume. Above 1 leans bullish, below 0.7 bearish.",
    "max pain": "The expiry price at which option buyers as a group would lose the most. Prices often drift toward it near expiry.",
    "expected move (1σ)": "The range the market prices in by expiry with about 68% probability, from the ATM implied volatility.",
    "atm straddle": "Cost of buying the ATM call and put together: the market's price for a move in either direction.",
    "iv skew (put−call)": "How much more expensive downside protection is than upside bets. Large positive = traders are worried about a fall.",
    "net premium": "Money you pay (debit, negative) or receive (credit, positive) to open the strategy.",
    "max profit": "The most the strategy can make at expiry.",
    "max loss": "The most the strategy can lose at expiry.",
    "breakevens": "Underlying prices at expiry where the strategy neither makes nor loses money.",
    "reward : risk": "Max profit divided by max loss. 2 means you can make twice what you risk.",
    "prob. of profit": "Estimated chance the strategy is profitable at expiry, from the market's own implied volatility.",
    "win rate": "Estimated chance the strategy ends in profit at expiry, after charges.",
    "expected p&l": "Average result over all the outcomes the price model considers likely, after charges. Positive = an edge.",
    "score": "The optimiser's rating: a blend of win rate and expected return per rupee risked, set by your goal slider.",
    "charges": "Brokerage, STT, exchange fees, SEBI fee, stamp duty and GST for the trade.",
    "spread slippage": "What crossing the bid/ask spread costs you on the way in and out, compared with the mid price.",
    "market": "Whether NSE is trading right now.",
    "price feed": "LIVE = prices stream tick by tick from Angel One. Snapshot = prices refresh every few seconds from NSE.",
    "position greeks": "How the whole strategy reacts to price (delta), acceleration (gamma), time (theta) and volatility (vega).",
    "p&l now (model)": "What the position would be worth if you closed it right now at current prices.",
  };
  App.glossary = G;

  const tip = document.createElement("div");
  tip.className = "gloss-tip hidden";
  tip.setAttribute("role", "tooltip");
  tip.id = "gloss-tip";
  document.body.appendChild(tip);

  const SELECTORS = ["thead th", ".kv > div:nth-child(odd)", ".hud-grid > div:nth-child(odd)", "#chain-meta > span", "table.opt-compare td:first-child"];
  function keyFor(el) {
    const own = Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ");
    const t = (own.trim() || el.textContent.trim()).toLowerCase().replace(/\s+/g, " ").replace(/:$/, "");
    if (G[t]) return t;
    const first = t.split(" ")[0];
    return G[first] && t.length < 24 ? first : null;
  }
  function decorate() {
    document.querySelectorAll(SELECTORS.join(",")).forEach((el) => {
      if (el.dataset.term !== undefined) return;
      const k = keyFor(el);
      el.dataset.term = k || "";
      if (k) { el.classList.add("term"); el.tabIndex = el.tabIndex >= 0 ? el.tabIndex : 0; el.setAttribute("aria-describedby", "gloss-tip"); }
    });
  }
  let pending = false;
  new MutationObserver(() => { if (!pending) { pending = true; requestAnimationFrame(() => { pending = false; decorate(); }); } })
    .observe(document.body, { childList: true, subtree: true });
  decorate();

  function show(el) {
    const k = el.dataset.term;
    if (!k) return;
    tip.innerHTML = `<b>${k.toUpperCase()}</b> ${G[k]}`;
    tip.classList.remove("hidden");
    const r = el.getBoundingClientRect();
    const w = Math.min(320, window.innerWidth - 24);
    tip.style.width = w + "px";
    tip.style.left = Math.max(12, Math.min(r.left, window.innerWidth - w - 12)) + "px";
    const below = r.bottom + 8 + tip.offsetHeight < window.innerHeight;
    tip.style.top = (below ? r.bottom + 8 : r.top - tip.offsetHeight - 8) + window.scrollY + "px";
  }
  const hide = () => tip.classList.add("hidden");
  document.addEventListener("mouseover", (e) => { const t = e.target.closest(".term"); if (t) show(t); });
  document.addEventListener("mouseout", (e) => { if (e.target.closest(".term")) hide(); });
  document.addEventListener("focusin", (e) => { const t = e.target.closest(".term"); if (t) show(t); });
  document.addEventListener("focusout", hide);
  window.addEventListener("scroll", hide, { passive: true });

  /* ---------- plain-English chain summary ---------- */
  function summary(ch) {
    const atm = ch.rows.find((r) => r.strike === ch.atm_strike);
    const parts = [`<b>${ch.symbol}</b> is at <b>${fmt(ch.spot, 2)}</b>.`];
    if (atm && atm.call && atm.put && atm.call.price && atm.put.price) {
      const straddle = atm.call.price + atm.put.price;
      parts.push(`Options are pricing a move of about <b>±${fmt(straddle)}</b> (${((straddle / ch.spot) * 100).toFixed(1)}%) by ${ch.expiry}.`);
    }
    let c = 0, p = 0;
    for (const r of ch.rows) { c += r.call?.oi || 0; p += r.put?.oi || 0; }
    if (c) {
      const pcr = p / c;
      const read = pcr > 1.3 ? "heavy put writing, a sign traders expect support below" : pcr >= 1 ? "more puts than calls written, mildly supportive"
        : pcr <= 0.7 ? "call writers dominate, a sign of resistance overhead" : "a balanced, neutral positioning";
      parts.push(`Put-call ratio ${pcr.toFixed(2)}: ${read}.`);
      const top = (side) => ch.rows.filter((r) => r[side]?.oi).sort((a, b) => b[side].oi - a[side].oi)[0];
      const res = top("call"), sup = top("put");
      if (res && sup) parts.push(`Biggest call wall ${fmt(res.strike)} (resistance), biggest put wall ${fmt(sup.strike)} (support).`);
    }
    return parts.join(" ");
  }
  App.onChain((ch) => { const el = $("#chain-summary"); if (el) el.innerHTML = summary(ch); });

  /* ---------- guided tour ---------- */
  const STEPS = [
    ["#controls", "Pick what to look at", "Type a symbol like NIFTY or RELIANCE, choose an expiry and press Load. Prices come live from NSE, Angel One or Yahoo."],
    [".sidebar", "Move between sections", "The option chain, charts, buy/sell ideas, the strategy builder and more. Keys 1–8 jump straight there."],
    ["#chain-table", "The option chain", "Calls on the left, puts on the right, strikes in the middle. Click any LTP to add that option to a strategy."],
    ["#chain-summary", "What it means", "A one-line plain-English reading of the chain. Hover any underlined term for an explanation."],
    ["#hud-panel", "Live status", "Clock, market state, whether prices are streaming live, and the key numbers for the current symbol."],
    ['[data-tab="builder"]', "Build and optimise", "In the Strategy Builder pick a ready-made strategy, edit it, and press ✨ Optimise to improve it on live prices."],
    ["#cmd-open", "Everything from the keyboard", "Press Ctrl+K to search for any action or symbol."],
  ];
  let step = 0;
  const box = document.createElement("div");
  box.className = "tour hidden";
  box.innerHTML = '<div class="tour-spot"></div><div class="tour-card neon-panel"><div class="tour-step"></div><h4></h4><p></p><div class="row"><button type="button" data-t="skip">Skip</button><button type="button" data-t="back">Back</button><button type="button" class="primary" data-t="next">Next</button></div></div>';
  document.body.appendChild(box);

  function place() {
    const [sel, title, text] = STEPS[step];
    const target = document.querySelector(sel);
    if (!target || !target.getClientRects().length) { if (step < STEPS.length - 1) { step += 1; return place(); } return end(); }
    App.switchTab && sel === "#chain-table" && App.switchTab("chain");
    target.scrollIntoView({ block: "nearest" });
    const r = target.getBoundingClientRect();
    const spot = box.querySelector(".tour-spot");
    Object.assign(spot.style, { left: r.left - 6 + "px", top: r.top - 6 + "px", width: r.width + 12 + "px", height: Math.min(r.height, window.innerHeight - 40) + 12 + "px" });
    const card = box.querySelector(".tour-card");
    box.querySelector(".tour-step").textContent = `Step ${step + 1} of ${STEPS.length}`;
    box.querySelector("h4").textContent = title;
    box.querySelector("p").textContent = text;
    box.querySelector('[data-t="back"]').disabled = step === 0;
    box.querySelector('[data-t="next"]').textContent = step === STEPS.length - 1 ? "Done" : "Next";
    const cw = Math.min(340, window.innerWidth - 24);
    card.style.width = cw + "px";
    const below = r.bottom + 16 + 180 < window.innerHeight;
    card.style.top = (below ? Math.min(r.bottom + 16, window.innerHeight - 200) : Math.max(12, r.top - 196)) + "px";
    card.style.left = Math.max(12, Math.min(r.left, window.innerWidth - cw - 12)) + "px";
  }
  function start() { step = 0; box.classList.remove("hidden"); place(); }
  function end() { box.classList.add("hidden"); try { localStorage.setItem("ot-tour-done", "1"); } catch (_) { /* ignore */ } }
  box.addEventListener("click", (e) => {
    const t = e.target.dataset.t;
    if (t === "skip") end();
    else if (t === "back") { step = Math.max(0, step - 1); place(); }
    else if (t === "next") { if (step === STEPS.length - 1) end(); else { step += 1; place(); } }
  });
  window.addEventListener("resize", () => { if (!box.classList.contains("hidden")) place(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !box.classList.contains("hidden")) end(); });
  App.startTour = start;
  $("#tour-open").addEventListener("click", start);
  let seen = null;
  try { seen = localStorage.getItem("ot-tour-done"); } catch (_) { seen = "1"; }
  if (!seen && !navigator.webdriver) setTimeout(start, 1500);
})();
