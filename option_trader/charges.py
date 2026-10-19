"""Indian F&O option trading charges and spread slippage.

Statutory rates (configurable because SEBI/NSE revise them; defaults as of the Oct-2024 revision):

* STT: 0.1% of premium on the **sell** side
* NSE exchange transaction charge: 0.03503% of premium
* SEBI turnover fee: Rs 10 per crore of premium (0.0001%)
* Stamp duty: 0.003% of premium on the **buy** side
* GST: 18% on brokerage + exchange charge + SEBI fee
* Brokerage: flat Rs 20 per executed order (typical discount broker)

Options exercised at expiry attract STT on intrinsic value instead; this estimate assumes the
position is squared off before expiry.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional


def _env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


@dataclass
class ChargeRates:
    brokerage_per_order: float = field(default_factory=lambda: _env("OPTION_TRADER_BROKERAGE_PER_ORDER", 20.0))
    stt_sell: float = field(default_factory=lambda: _env("OPTION_TRADER_STT_SELL", 0.001))
    exchange_txn: float = field(default_factory=lambda: _env("OPTION_TRADER_EXCHANGE_TXN", 0.0003503))
    sebi: float = field(default_factory=lambda: _env("OPTION_TRADER_SEBI_FEE", 0.000001))
    stamp_buy: float = field(default_factory=lambda: _env("OPTION_TRADER_STAMP_BUY", 0.00003))
    gst: float = field(default_factory=lambda: _env("OPTION_TRADER_GST", 0.18))


@dataclass
class Fill:
    action: str  # BUY or SELL
    price: float  # per unit premium
    qty: float  # units (lots x lot size)
    is_option: bool = True


def order_charges(fills: Iterable[Fill], rates: Optional[ChargeRates] = None) -> dict:
    """Charges for executing ``fills`` (one order per fill)."""
    rates = rates or ChargeRates()
    out = {"brokerage": 0.0, "stt": 0.0, "exchange": 0.0, "sebi": 0.0, "stamp": 0.0, "gst": 0.0}
    for f in fills:
        if not f.is_option:
            continue  # underlying/futures legs: not modelled here
        turnover = f.price * f.qty
        out["brokerage"] += rates.brokerage_per_order if turnover else 0.0
        out["exchange"] += turnover * rates.exchange_txn
        out["sebi"] += turnover * rates.sebi
        if f.action == "SELL":
            out["stt"] += turnover * rates.stt_sell
        else:
            out["stamp"] += turnover * rates.stamp_buy
    out["gst"] = (out["brokerage"] + out["exchange"] + out["sebi"]) * rates.gst
    out = {k: round(v, 2) for k, v in out.items()}
    out["total"] = round(sum(out.values()), 2)
    return out


def flip(action: str) -> str:
    return "SELL" if action == "BUY" else "BUY"


def strategy_costs(legs: list[dict], lot_size: int, rates: Optional[ChargeRates] = None) -> dict:
    """Entry, round-trip (exit at the same prices) charges and spread slippage for legs.

    Each leg dict: action, option_type, lots, premium, and optionally bid/ask.
    Slippage is what crossing the spread costs versus the bid/ask mid on entry and exit.
    """
    entry = [Fill(l["action"], l["premium"], l["lots"] * lot_size, l["option_type"] != "FUT") for l in legs]
    exit_ = [Fill(flip(f.action), f.price, f.qty, f.is_option) for f in entry]
    e = order_charges(entry, rates)
    x = order_charges(exit_, rates)
    slip = 0.0
    known = True
    for l in legs:
        bid, ask = l.get("bid"), l.get("ask")
        if l["option_type"] == "FUT":
            continue
        if bid and ask and ask >= bid > 0:
            slip += (ask - bid) / 2 * l["lots"] * lot_size * 2  # half-spread, in and out
        else:
            known = False
    return {
        "entry": e,
        "exit": x,
        "round_trip": round(e["total"] + x["total"], 2),
        "spread_slippage": round(slip, 2) if known else None,
        "rates": asdict(rates or ChargeRates()),
    }
