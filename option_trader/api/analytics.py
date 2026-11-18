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

    out = recommend(chain, candles, (chain.rate or manager.risk_free_rate), max(lots, 1), later_chain)
    out["warnings"] = event_warnings(manager, chain)
    return out


def event_warnings(manager: ProviderManager, chain) -> list[str]:
    """Plain-English warnings about earnings/dividends inside this expiry (never raises)."""
    from ..events import assignment_risk, reaction_expiry_ok

    if manager.events is None:
        return []
    try:
        ev = manager.events.get(chain.symbol)
    except Exception:
        return []
    out = []
    if ev.next_earnings and reaction_expiry_ok(chain.expiry, ev.next_earnings, ev.earnings_timing):
        when = f" ({ev.earnings_timing})" if ev.earnings_timing else ""
        out.append(f"Earnings on {ev.next_earnings:%b %d}{when}, before this expiry: implied volatility usually "
                   "drops sharply after the report (IV crush), which hurts option buyers and helps sellers, "
                   "and the stock can gap past your strikes.")
    risk = assignment_risk(chain, ev.ex_dividend, ev.dividend)
    if risk["applies"]:
        n = len(risk["contracts"])
        msg = f"Ex-dividend on {ev.ex_dividend:%b %d} (${ev.dividend:.2f}/share) before this expiry: calls are cheaper and puts dearer by about the dividend."
        if n:
            msg += (f" {n} in-the-money call strike{'s' if n > 1 else ''} have less time value than the dividend; "
                    f"short calls there are likely to be assigned on {risk['exercise_by']}.")
        out.append(msg)
    return out


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


@router.get("/events")
def events(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None,
           manager: ProviderManager = Depends(get_manager)) -> dict:
    """Earnings and dividends that affect this symbol's options, with what the market prices in."""
    from ..events import CorporateEvents, assignment_risk, earnings_move, historical_moves, reaction_expiry_ok

    ev = manager.events.get(symbol) if manager.events is not None else CorporateEvents(
        symbol.upper(), error="Corporate events are not enabled")
    out: dict = {"symbol": ev.symbol, "events": ev.to_dict(), "available": ev.error is None}
    try:
        chain = manager.get_chain(symbol, parse_expiry(expiry), source)
    except DataUnavailableError:
        chain = None
    if chain is not None:
        out.update(expiry=chain.expiry.isoformat(), exercise=chain.exercise)
        if ev.next_earnings:
            def ok(e):
                return reaction_expiry_ok(e, ev.next_earnings, ev.earnings_timing)

            out["earnings_before_expiry"] = ok(chain.expiry)
            post = next((e for e in sorted(chain.expiries) if ok(e)), None)
            pre = max((e for e in chain.expiries if not ok(e)), default=None)
            if post is not None:
                try:
                    post_chain = chain if post == chain.expiry else manager.get_chain(symbol, post, chain.source)
                    pre_chain = manager.get_chain(symbol, pre, chain.source) if pre is not None else None
                    out["implied"] = earnings_move(post_chain, pre_chain)
                except DataUnavailableError as exc:
                    out["implied_error"] = str(exc)
        out["ex_div_before_expiry"] = bool(ev.ex_dividend and ev.ex_dividend <= chain.expiry)
        out["assignment"] = assignment_risk(chain, ev.ex_dividend, ev.dividend)
    if ev.past_earnings:
        try:
            out["history"] = historical_moves(ev.past_earnings, manager.get_history(symbol, "2y", "1d"), ev.past_timing)
        except DataUnavailableError as exc:
            out["history_error"] = str(exc)
    return out


def _near_chains(manager: ProviderManager, symbol: str, expiry: Optional[str], source: Optional[str], count: int):
    """The chosen (or nearest) expiry plus the following ones, up to ``count`` chains."""
    try:
        first = manager.get_chain(symbol, parse_expiry(expiry), source)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    chains = [first]
    for e in [e for e in sorted(first.expiries) if e > first.expiry][: max(count, 1) - 1]:
        try:
            chains.append(manager.get_chain(symbol, e, first.source))
        except DataUnavailableError:
            break
    return chains


@router.get("/gamma")
def gamma(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None, expiries: int = 4,
          manager: ProviderManager = Depends(get_manager)) -> dict:
    """Dealer gamma exposure by strike, total, and the zero-gamma level."""
    from ..analytics.gamma import gamma_exposure

    chains = _near_chains(manager, symbol, expiry, source, min(max(expiries, 1), 8))
    try:
        return gamma_exposure(chains, chains[0].rate or manager.risk_free_rate)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/flow")
def flow(symbol: str, expiry: Optional[str] = None, source: Optional[str] = None, expiries: int = 4,
         min_volume: int = 100, min_vol_oi: float = 1.0, min_premium: float = 25_000,
         manager: ProviderManager = Depends(get_manager)) -> dict:
    """Unusual options activity: big, new-position volume, ranked by premium traded."""
    from ..analytics.flow import unusual_activity

    chains = _near_chains(manager, symbol, expiry, source, min(max(expiries, 1), 8))
    return unusual_activity(chains, max(min_volume, 1), max(min_vol_oi, 0.0), max(min_premium, 0.0))
