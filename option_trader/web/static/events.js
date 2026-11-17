/* Earnings & dividends: chain badges and the Signals "Events" card. */
"use strict";

(function () {
  const { api } = window.App;
  const $ = (s) => document.querySelector(s);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const day = (iso) => (iso ? new Date(iso + "T12:00:00").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "–");
  const cache = new Map(); // "SYM|expiry" -> {at, p}
  const TTL = 10 * 60 * 1000;

  function fetchEvents(ch) {
    const key = `${ch.symbol}|${ch.expiry}`;
    const hit = cache.get(key);
    if (hit && Date.now() - hit.at < TTL) return hit.p;
    const p = api("/api/analytics/events?" + new URLSearchParams({ symbol: ch.symbol, expiry: ch.expiry, source: ch.source })).catch(() => null);
    cache.set(key, { at: Date.now(), p });
    return p;
  }

  function pills(d) {
    const e = d.events, out = [];
    if (e.next_earnings) {
      const inside = d.earnings_before_expiry;
      const move = d.implied && (d.implied.event_move_pct ?? d.implied.straddle_move_pct);
      const tip = (inside ? "The report lands before this expiry: expect an IV drop after it." : "Report is after this expiry.") +
        (e.earnings_confirmed ? "" : " Date not yet confirmed by the company.");
      const timing = e.earnings_timing ? " " + (e.earnings_timing === "before open" ? "BMO" : "AMC") : "";
      out.push(`<span class="pill ${inside ? "neutral" : "bullish"} ev-pill" title="${esc(tip)}">Earnings ${esc(day(e.next_earnings))}${timing}${inside && move ? ` · implied ±${move.toFixed(1)}%` : ""}</span>`);
    }
    if (e.ex_dividend && d.ex_div_before_expiry) {
      const n = d.assignment && d.assignment.contracts.length;
      const tip = "Ex-dividend before this expiry." + (n ? ` ${n} ITM call strikes risk early assignment.` : "");
      out.push(`<span class="pill neutral ev-pill" title="${esc(tip)}">Ex-div ${esc(day(e.ex_dividend))}${e.dividend ? ` $${e.dividend.toFixed(2)}` : ""}${n ? " · assignment risk" : ""}</span>`);
    }
    return out.join("");
  }

  function verdict(implied, past) {
    const r = implied / past;
    if (r > 1.25) return `Options price a bigger move than this stock's recent reports (${r.toFixed(2)}×): premium sellers are paid well for the event.`;
    if (r < 0.8) return `Options price a smaller move than recent reports (${r.toFixed(2)}×): event premium looks cheap for buyers.`;
    return `Implied move is in line with recent reports (${r.toFixed(2)}×).`;
  }

  function movesChart(moves) {
    const c = App.colors();
    const max = Math.max(...moves.map((m) => Math.abs(m.move_pct)), 1);
    const bars = [...moves].reverse().map((m) => {
      const h = Math.max(4, (Math.abs(m.move_pct) / max) * 100);
      return `<span style="height:${h}%;background:${m.move_pct >= 0 ? c.profit : c.loss}" title="${esc(m.date)}: ${m.move_pct > 0 ? "+" : ""}${m.move_pct}%"></span>`;
    });
    return `<div class="ev-moves" title="Past report-day moves, oldest to newest">${bars.join("")}</div>`;
  }

  function card(d) {
    const e = d.events;
    const wrap = (inner) => `<div class="ev-grid">${inner}</div>`;
    if (d.exercise === "european" && !e.next_earnings) {
      return wrap(`<div class="card"><h3>Earnings &amp; dividends</h3><p class="muted">${esc(d.symbol)} is a cash-settled index: no earnings, and dividends are already in the forward price. Options are European, so there is no early assignment.</p></div>`);
    }
    if (!d.available && !e.next_earnings && !e.dividend) {
      return wrap(`<div class="card"><h3>Earnings &amp; dividends</h3><p class="muted">Event data unavailable: ${esc(e.error || "no data")}</p></div>`);
    }
    const im = d.implied, h = d.history;
    const rows = [];
    rows.push(["Next report", e.next_earnings
      ? `${esc(day(e.next_earnings))}${e.earnings_timing ? " · " + esc(e.earnings_timing) : ""}${e.earnings_confirmed ? "" : " (estimated)"}`
      : "Not announced"]);
    if (e.next_earnings) rows.push(["Before this expiry?", d.earnings_before_expiry ? '<span class="pill neutral">yes: IV crush risk</span>' : "no"]);
    if (im) {
      rows.push(["Implied move", im.event_move_pct != null ? `±${im.event_move_pct.toFixed(2)}% (event only)` : "–"]);
      rows.push(["Straddle move", im.straddle_move_pct != null ? `±${im.straddle_move_pct.toFixed(2)}% by ${esc(im.expiry)}` : "–"]);
      rows.push(["IV before / after", `${im.iv_pre ? (im.iv_pre * 100).toFixed(1) + "%" : "–"} / ${im.iv_post ? (im.iv_post * 100).toFixed(1) + "%" : "–"}`]);
    }
    if (h && h.count) rows.push(["Past avg move", `±${h.avg_abs_move_pct.toFixed(2)}% (${h.count} reports, max ${h.max_abs_move_pct.toFixed(1)}%)`]);
    const kv = (list) => `<div class="kv">${list.map(([k, v]) => `<div>${k}</div><div>${v}</div>`).join("")}</div>`;
    let earn = `<div class="card"><h3>Earnings</h3>${kv(rows)}`;
    if (h && h.count) earn += movesChart(h.moves);
    if (im && im.event_move_pct != null && h && h.avg_abs_move_pct) earn += `<p class="muted">${verdict(im.event_move_pct, h.avg_abs_move_pct)}</p>`;
    earn += "</div>";

    const a = d.assignment || { applies: false, contracts: [] };
    const drows = [
      ["Last dividend", (e.dividend ? "$" + Number(e.dividend.toFixed(4)) : "None") + (e.dividends_per_year ? ` · ${e.dividends_per_year}× a year` : "")],
      ["Next ex-date", e.ex_dividend ? esc(day(e.ex_dividend)) : "Not announced"],
    ];
    if (a.applies) drows.push(["Exercise deadline", `${esc(day(a.exercise_by))} (close)`]);
    let div = `<div class="card"><h3>Dividends</h3>${kv(drows)}`;
    if (a.applies) {
      div += a.contracts.length
        ? `<p>Early-assignment risk on short calls at: <b>${a.contracts.map((c) => c.strike).join(", ")}</b>. Their time value (about the same-strike put) is below the $${a.dividend.toFixed(2)} dividend, so holders gain by exercising before the ex-date.</p>`
        : `<p class="muted">No in-the-money call has less time value than the dividend: early assignment is unlikely.</p>`;
    }
    div += "</div>";
    return wrap(earn + div);
  }

  App.onChain(async (ch) => {
    if (!ch || !ch.symbol) return;
    const d = await fetchEvents(ch);
    if (!d || !App.chain || App.chain.symbol !== ch.symbol || App.chain.expiry !== ch.expiry) return;
    const meta = $("#chain-meta");
    meta.querySelectorAll(".ev-pill").forEach((x) => x.parentNode.removeChild(x));
    meta.insertAdjacentHTML("afterbegin", pills(d));
    $("#events-out").innerHTML = card(d);
  });
})();
