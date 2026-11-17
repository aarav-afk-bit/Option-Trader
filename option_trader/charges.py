"""US listed-option trading fees and spread slippage.

All rates are configurable (Settings > Fees) because brokers and regulators revise them. Defaults:

* Commission: $0.65 per contract (a typical retail rate; many brokers charge $0 or $0.50) plus an
  optional per-order ticket charge (default $0)
* Options Regulatory Fee (ORF): $0.0229 per contract, both sides (passed through by brokers)
* OCC clearing fee: $0.02 per contract, both sides
* FINRA Trading Activity Fee (TAF): $0.00279 per contract **sold** (max $8.30 per trade);
  stock sells $0.000166 per share
* SEC Section 31 fee: a fraction of **sell** proceeds ($27.80 per million by default)

Stock legs pay no commission by default but do pay the SEC fee and TAF when sold. Exercise and
assignment fees are not included: estimates assume positions are closed before expiry.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Iterable, Optional

from .models import CONTRACT_MULTIPLIER

FEE_ENV = {
    "commission_per_contract": ("OPTION_TRADER_COMMISSION_PER_CONTRACT", 0.65),
    "commission_per_order": ("OPTION_TRADER_COMMISSION_PER_ORDER", 0.0),
    "orf_per_contract": ("OPTION_TRADER_ORF", 0.0229),
    "occ_per_contract": ("OPTION_TRADER_OCC_FEE", 0.02),
    "taf_per_contract": ("OPTION_TRADER_FINRA_TAF", 0.00279),
    "sec_fee_rate": ("OPTION_TRADER_SEC_FEE", 0.0000278),
}
FEE_KEYS = tuple(env for env, _ in FEE_ENV.values())
TAF_MAX_PER_TRADE = 8.30
TAF_PER_SHARE = 0.000166


def _env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


def _rate(attr: str):
    env, default = FEE_ENV[attr]
    return field(default_factory=lambda: _env(env, default))


@dataclass
class ChargeRates:
    commission_per_contract: float = _rate("commission_per_contract")
    commission_per_order: float = _rate("commission_per_order")
    orf_per_contract: float = _rate("orf_per_contract")
    occ_per_contract: float = _rate("occ_per_contract")
    taf_per_contract: float = _rate("taf_per_contract")
    sec_fee_rate: float = _rate("sec_fee_rate")


@dataclass
class Fill:
    action: str  # BUY or SELL
    price: float  # per share premium (or stock price)
    qty: float  # shares: contracts x multiplier for options
    is_option: bool = True
    multiplier: int = CONTRACT_MULTIPLIER

    @property
    def contracts(self) -> float:
        return self.qty / self.multiplier if self.is_option else 0.0


def order_charges(fills: Iterable[Fill], rates: Optional[ChargeRates] = None) -> dict:
    """Fees for executing ``fills`` (one order per fill)."""
    rates = rates or ChargeRates()
    out = {"commission": 0.0, "orf": 0.0, "occ": 0.0, "finra_taf": 0.0, "sec": 0.0}
    for f in fills:
        if not f.qty:
            continue
        proceeds = f.price * f.qty
        if f.is_option:
            n = f.contracts
            out["commission"] += n * rates.commission_per_contract + rates.commission_per_order
            out["orf"] += n * rates.orf_per_contract
            out["occ"] += n * rates.occ_per_contract
            if f.action == "SELL":
                out["finra_taf"] += min(n * rates.taf_per_contract, TAF_MAX_PER_TRADE)
        elif f.action == "SELL":
            out["finra_taf"] += min(f.qty * TAF_PER_SHARE, TAF_MAX_PER_TRADE)
        if f.action == "SELL":
            out["sec"] += proceeds * rates.sec_fee_rate
    out = {k: round(v, 2) for k, v in out.items()}
    out["total"] = round(sum(out.values()), 2)
    return out


def flip(action: str) -> str:
    return "SELL" if action == "BUY" else "BUY"


def strategy_costs(legs: list[dict], lot_size: int, rates: Optional[ChargeRates] = None) -> dict:
    """Entry, round-trip (exit at the same prices) fees and spread slippage for legs.

    Each leg dict: action, option_type, lots (contracts), premium, and optionally bid/ask.
    Slippage is what crossing the spread costs versus the bid/ask mid on entry and exit.
    """
    entry = [Fill(l["action"], l["premium"], l["lots"] * lot_size, l["option_type"] != "FUT", lot_size) for l in legs]
    exit_ = [Fill(flip(f.action), f.price, f.qty, f.is_option, f.multiplier) for f in entry]
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
