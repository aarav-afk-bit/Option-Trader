"""Overlay real-time exchange ticks on option chain snapshots.

With a Tradier production token, every chain the app serves (whether its snapshot came from
Tradier, Cboe or Yahoo) is subscribed on Tradier's market-events WebSocket by OCC symbol. The
latest trade, bid/ask and sizes from those events replace the snapshot values, and IV/Greeks
are recomputed from the new prices. Without a token the app keeps polling snapshots and says
so in the UI.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Optional

from .models import OptionChain
from .pricing import enrich_chain

log = logging.getLogger(__name__)


def overlay(chain: OptionChain, mapping: dict, tick, r: float, feed_name: str) -> int:
    """Apply the latest ticks (looked up by ``mapping`` keys) to ``chain``; returns quotes changed."""
    latest = 0
    applied = 0
    if mapping.get("spot"):
        t = tick(mapping["spot"])
        if t and t.get("ltp"):
            chain.spot = t["ltp"]
            latest = max(latest, t.get("exchange_ts") or 0)
    for row in chain.rows:
        for q in (row.call, row.put):
            if q is None:
                continue
            key = mapping["tokens"].get((row.strike, q.option_type))
            t = tick(key) if key else None
            if not t or not (t.get("ltp") or t.get("bid") or t.get("ask")):
                continue
            if t.get("ltp"):
                q.ltp = t["ltp"]
            if t.get("bid") is not None:
                q.bid, q.bid_qty = t["bid"], t.get("bid_qty")
            if t.get("ask") is not None:
                q.ask, q.ask_qty = t["ask"], t.get("ask_qty")
            if t.get("volume") is not None:
                q.volume = int(t["volume"])
            if t.get("oi") is not None:
                q.oi = float(t["oi"])
            if t.get("close") and q.ltp:
                q.change = round(q.ltp - t["close"], 2)
            ts = t.get("exchange_ts") or 0
            q.tick_ts = datetime.fromtimestamp(ts / 1000, timezone.utc).isoformat() if ts else q.tick_ts
            # Recompute IV/Greeks from the new price; old quality flags described the old price.
            q.iv = q.delta = q.gamma = q.theta = q.vega = None
            q.suspect, q.issues = False, []
            latest = max(latest, ts)
            applied += 1
    if applied or latest:
        from .quality import report, sanitize, validate

        r = chain.rate or r
        sanitize(chain)
        enrich_chain(chain, r)
        validate(chain, r)
        chain.quality = report(chain)
        chain.live_at = datetime.fromtimestamp(latest / 1000, timezone.utc).isoformat() if latest else None
        note = f"Live: prices updated tick-by-tick from {feed_name}"
        if note not in chain.notes:
            chain.notes.append(note)
    return applied


class TradierLiveHub:
    """Streams Tradier quotes/trades for every contract of the chains being viewed."""

    def __init__(self, provider, stream=None, risk_free_rate: float = 0.04):
        from .providers.tradier import TradierStream

        self.provider = provider
        self.stream = stream or TradierStream(provider.create_stream_session)
        self.r = risk_free_rate
        self._subscribed: set[tuple] = set()
        self.last_error: Optional[str] = None

    @property
    def version(self) -> int:
        return self.stream.version

    def apply(self, chain: OptionChain) -> int:
        if chain.currency != "USD":
            return 0
        tokens = {(row.strike, q.option_type): q.symbol for row in chain.rows for q in (row.call, row.put)
                  if q is not None and q.symbol}
        key = (chain.symbol, chain.expiry)
        if key not in self._subscribed:
            self.stream.subscribe([chain.symbol, *tokens.values()])
            self._subscribed.add(key)
        return overlay(chain, {"tokens": tokens, "spot": chain.symbol}, self.stream.tick, self.r, "Tradier's real-time feed")

    def status(self) -> dict:
        s = self.stream.status()
        s["error"] = self.last_error or s.get("last_error")
        return s
