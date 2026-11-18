"""Unusual options activity from the live chains.

A contract is unusual when today's volume is large in absolute terms and large against its open
interest (volume above OI means mostly new positions). Each hit gets the dollar premium traded
(volume x price x 100) and where the last trade printed against the quote: at/above the ask
suggests buyers were aggressive, at/below the bid suggests sellers were.
"""

from __future__ import annotations

from typing import Optional

from ..models import CONTRACT_MULTIPLIER, OptionChain


def _side(ltp: Optional[float], bid: Optional[float], ask: Optional[float]) -> str:
    if ltp is None or bid is None or ask is None or ask <= 0:
        return "unknown"
    if ltp >= ask:
        return "ask"
    if ltp <= bid:
        return "bid"
    mid = (bid + ask) / 2
    return "near ask" if ltp > mid else "near bid" if ltp < mid else "mid"


def unusual_activity(chains: list[OptionChain], min_volume: int = 100, min_vol_oi: float = 1.0,
                     min_premium: float = 25_000, top: int = 30) -> dict:
    hits, scanned, total_premium = [], 0, {"CE": 0.0, "PE": 0.0}
    for ch in chains:
        for row in ch.rows:
            for q in (row.call, row.put):
                if not q or not q.volume:
                    continue
                scanned += 1
                px = q.mid if q.mid is not None else q.ltp
                if not px:
                    continue
                premium = q.volume * px * CONTRACT_MULTIPLIER
                total_premium[q.option_type] += premium
                ratio = q.volume / q.oi if q.oi else None
                if q.volume < min_volume or premium < min_premium:
                    continue
                if ratio is not None and ratio < min_vol_oi:
                    continue
                side = _side(q.ltp, q.bid, q.ask)
                otm = (row.strike > ch.spot) if q.option_type == "CE" else (row.strike < ch.spot)
                sentiment = "neutral"
                if side in ("ask", "near ask"):
                    sentiment = "bullish" if q.option_type == "CE" else "bearish"
                elif side in ("bid", "near bid"):
                    sentiment = "bearish" if q.option_type == "CE" else "bullish"
                hits.append({
                    "symbol": ch.symbol, "expiry": ch.expiry.isoformat(), "strike": row.strike,
                    "type": q.option_type, "volume": q.volume, "oi": q.oi,
                    "vol_oi": round(ratio, 2) if ratio is not None else None, "price": px,
                    "premium": round(premium, 0), "side": side, "sentiment": sentiment, "otm": otm,
                    "iv": q.iv, "delta": q.delta,
                })
    hits.sort(key=lambda h: h["premium"], reverse=True)
    call_p, put_p = total_premium["CE"], total_premium["PE"]
    return {
        "symbol": chains[0].symbol if chains else None,
        "expiries": [c.expiry.isoformat() for c in chains], "scanned": scanned, "hits": hits[:top],
        "call_premium": round(call_p, 0), "put_premium": round(put_p, 0),
        "premium_pcr": round(put_p / call_p, 3) if call_p else None,
        "filters": {"min_volume": min_volume, "min_vol_oi": min_vol_oi, "min_premium": min_premium},
    }
