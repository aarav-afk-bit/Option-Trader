"""Cross-source verification: compare the same chain from two independent sources.

NSE's website and Angel One's feed are separate pipes from the same exchange. When both are
configured, comparing them strike by strike catches problems neither can reveal alone (a
stuck feed, a parsing error, a stale snapshot). Prices are compared within a tolerance that
allows for the bid/ask spread, the tick size and the few seconds between the two snapshots.
"""

from __future__ import annotations

from typing import Optional

from .models import OptionChain, OptionQuote

TICK = 0.05


def _tol(a: OptionQuote, b: OptionQuote) -> float:
    spreads = [x.ask - x.bid for x in (a, b) if x.bid and x.ask]
    ref = max(a.price or 0, b.price or 0)
    return max(2 * TICK, max(spreads, default=0.0), 0.01 * ref)


def compare(primary: OptionChain, secondary: OptionChain, mark: bool = True) -> dict:
    matched, agree, rows = 0, 0, []
    oi_pairs = []
    for r in primary.rows:
        for side in ("call", "put"):
            a = getattr(r, side)
            b = secondary.quote(r.strike, "CE" if side == "call" else "PE")
            if not (a and b and a.price and b.price):
                continue
            matched += 1
            diff = a.price - b.price
            tol = _tol(a, b)
            ok = abs(diff) <= tol
            agree += ok
            if a.oi and b.oi:
                oi_pairs.append(abs(a.oi - b.oi) / max(a.oi, b.oi))
            if not ok:
                rows.append({"strike": r.strike, "type": a.option_type, "primary": a.price, "secondary": b.price,
                             "diff": round(diff, 2), "tolerance": round(tol, 2)})
                if mark:
                    note = f"differs from {secondary.source.upper()} ({b.price:g})"
                    if note not in a.issues:
                        a.issues.append(note)
    spot_diff = primary.spot - secondary.spot
    spot_ok = abs(spot_diff) <= max(0.0005 * primary.spot, 1.0)
    pct = round(100 * agree / matched, 1) if matched else None
    if not matched:
        confidence = "unknown"
    elif pct >= 95 and spot_ok:
        confidence = "high"
    elif pct >= 80:
        confidence = "medium"
    else:
        confidence = "low"
    rows.sort(key=lambda x: abs(x["diff"]) / max(x["tolerance"], 1e-9), reverse=True)
    return {
        "primary": primary.source, "secondary": secondary.source,
        "matched": matched, "agree": agree, "agree_pct": pct,
        "spot_primary": primary.spot, "spot_secondary": secondary.spot, "spot_diff": round(spot_diff, 2),
        "oi_median_gap_pct": round(100 * sorted(oi_pairs)[len(oi_pairs) // 2], 2) if oi_pairs else None,
        "confidence": confidence,
        "disagreements": rows[:25],
        "times": {"primary": primary.exchange_timestamp or primary.fetched_at.isoformat(),
                  "secondary": secondary.exchange_timestamp or secondary.fetched_at.isoformat()},
    }


def pick_secondary(manager, primary_source: str) -> Optional[str]:
    """Another configured source that can provide the same NSE chain (Yahoo has none)."""
    for p in manager.providers:
        if p.name not in (primary_source, "yahoo") and p.is_configured():
            return p.name
    return None
