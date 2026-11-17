"""Analytics endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from ..analytics.chain import analyze_chain, atm_iv, expected_move
from ..analytics.recommender import recommend
from ..analytics.technicals import technical_snapshot
from ..strategy.templates import ResolveError
from ..errors import DataUnavailableError
from ..providers.manager import ProviderManager
from .deps import get_manager, parse_expiry

router = APIRouter(prefix="/api/analytics")


@router.get("/chain")
def chain_analytics(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
                    manager: ProviderManager = Depends(get_manager)) -> dict:
    try:
        chain = manager.get_chain(symbol, parse_expiry(expiry), source)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    out = analyze_chain(chain).to_dict()
    out.update(symbol=chain.symbol, expiry=chain.expiry.isoformat(), spot=chain.spot,
               source=chain.source, fetched_at=chain.fetched_at.isoformat())
    return out


@router.get("/technicals")
def technicals(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
               manager: ProviderManager = Depends(get_manager)) -> dict:
    try:
        candles = manager.get_history(symbol, "1y", "1d")
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    iv = None
    try:
        iv = atm_iv(manager.get_chain(symbol, parse_expiry(expiry), source))
    except DataUnavailableError:
        pass  # technicals still work without the chain; volatility view will say "unknown"
    try:
        snap = technical_snapshot(candles, iv)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    snap.update(symbol=symbol.upper(), candles=len(candles), last_candle=candles[-1].ts.isoformat())
    return snap


@router.get("/recommendations")
def recommendations(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
                    lots: int = 1, manager: ProviderManager = Depends(get_manager)) -> dict:
    """What to buy/sell right now, derived from the live chain and price history."""
    try:
        chain = manager.get_chain(symbol, parse_expiry(expiry), source)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        candles = manager.get_history(symbol, "1y", "1d")
    except DataUnavailableError:
        candles = None

    def later_chain(offset: int):
        later = [e for e in chain.expiries if e > chain.expiry]
        if len(later) < offset:
            raise ResolveError("No later expiry listed")
        try:
            return manager.get_chain(symbol, later[offset - 1], chain.source)
        except DataUnavailableError as exc:
            raise ResolveError(str(exc)) from exc

    return recommend(chain, candles, (chain.rate or manager.risk_free_rate), max(lots, 1), later_chain)


@router.get("/term-structure")
def term_structure(symbol: str, source: Optional[str] = None, count: int = 4,
                   manager: ProviderManager = Depends(get_manager)) -> dict:
    """ATM IV and expected move for the next ``count`` expiries (each from a live chain)."""
    try:
        first = manager.get_chain(symbol, None, source)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    expiries = [e for e in first.expiries if e >= first.expiry][: max(1, min(count, 8))] or [first.expiry]
    points, errors = [], []
    for exp in expiries:
        try:
            ch = first if exp == first.expiry else manager.get_chain(symbol, exp, first.source)
        except DataUnavailableError as exc:
            errors.append(f"{exp}: {exc}")
            continue
        em = expected_move(ch)
        points.append({"expiry": exp.isoformat(), "days": em["days_to_expiry"], "atm_iv": em["atm_iv"],
                       "straddle": em["straddle_price"], "one_sd_move": em["one_sd_move"]})
    return {"symbol": first.symbol, "spot": first.spot, "source": first.source, "points": points,
            "errors": errors}


@router.get("/intraday")
def intraday(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
             manager: ProviderManager = Depends(get_manager)) -> dict:
    """Snapshots recorded each time this chain was fetched live today (kept in memory)."""
    exp = parse_expiry(expiry)
    if exp is None:
        try:
            exp = manager.get_chain(symbol, None, source).expiry
        except DataUnavailableError as exc:
            raise HTTPException(503, str(exc)) from exc
    return {"symbol": symbol.upper(), "expiry": exp.isoformat(), "points": manager.get_snapshots(symbol, exp)}


@router.get("/watchlist")
def watchlist(symbols: str, source: Optional[str] = None,
              manager: ProviderManager = Depends(get_manager)) -> dict:
    """Snapshot of several underlyings from their live nearest-expiry chains."""
    names = [x.strip().upper() for x in symbols.split(",") if x.strip()][:20]
    rows = []
    for sym in names:
        try:
            ch = manager.get_chain(sym, None, source)
        except DataUnavailableError as exc:
            rows.append({"symbol": sym, "error": str(exc)})
            continue
        ca = analyze_chain(ch)
        weight = sum(sg.strength for sg in ca.signals)
        score = sum(sg.score() for sg in ca.signals) / weight if weight else 0.0
        atm = ch.row(ch.atm_strike)
        straddle = None
        if atm and atm.call and atm.put and atm.call.price and atm.put.price:
            straddle = atm.call.price + atm.put.price
        rows.append({
            "symbol": ch.symbol, "spot": ch.spot, "expiry": ch.expiry.isoformat(), "source": ch.source,
            "fetched_at": ch.fetched_at.isoformat(), "lot_size": ch.lot_size,
            "pcr_oi": ca.pcr["pcr_oi"], "max_pain": ca.max_pain,
            "atm_iv": ca.expected_move["atm_iv"], "straddle": straddle,
            "support": ca.levels["support"][0]["strike"] if ca.levels["support"] else None,
            "resistance": ca.levels["resistance"][0]["strike"] if ca.levels["resistance"] else None,
            "options_score": round(score, 3),
            "bias": "bullish" if score > 0.2 else "bearish" if score < -0.2 else "neutral",
        })
    return {"rows": rows}
