"""Strategy analysis endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..errors import DataUnavailableError
from ..providers.manager import ProviderManager
from ..strategy.engine import Leg, Strategy, analyze
from ..strategy.store import StrategyStore, template_from_legs
from ..strategy.templates import BUILTIN_BY_ID, BUILTIN_TEMPLATES, ResolveError, StrategyTemplate, resolve
from .deps import get_manager, parse_expiry

router = APIRouter(prefix="/api/strategy")


class LegIn(BaseModel):
    option_type: str
    action: str
    strike: float = 0.0
    lots: int = 1
    premium: Optional[float] = None  # None: take the live price from the chain
    expiry: Optional[str] = None
    iv: Optional[float] = None


class AnalyzeIn(BaseModel):
    symbol: str
    expiry: Optional[str] = None
    source: Optional[str] = None
    name: str = "Custom"
    legs: list[LegIn]
    lot_size: Optional[int] = None
    target_days: Optional[float] = Field(None, description="Days from now for the target-date curve")
    iv_shift: float = 0.0
    range_pct: float = 0.15
    fill: str = "ltp"


def add_costs(result: dict) -> None:
    """Attach charges/slippage and the net (after round-trip charges) payoff figures."""
    from ..charges import strategy_costs

    st = result["strategy"]
    costs = strategy_costs(st["legs"], st["lot_size"])
    rt = costs["round_trip"]
    costs["net_max_profit"] = None if result["max_profit"] is None else round(result["max_profit"] - rt, 2)
    costs["net_max_loss"] = None if result["max_loss"] is None else round(result["max_loss"] - rt, 2)
    result["costs"] = costs


def build_strategy(body: AnalyzeIn, manager: ProviderManager):
    """Price every leg off the real chain; legs without a live price are rejected."""
    chain = manager.get_chain(body.symbol, parse_expiry(body.expiry), body.source)
    legs = []
    for raw in body.legs:
        otype, action = raw.option_type.upper(), raw.action.upper()
        if otype not in ("CE", "PE", "FUT") or action not in ("BUY", "SELL"):
            raise HTTPException(400, f"Invalid leg {raw.option_type} {raw.action}")
        leg_chain = chain
        if raw.expiry and parse_expiry(raw.expiry) != chain.expiry:
            leg_chain = manager.get_chain(body.symbol, parse_expiry(raw.expiry), chain.source)
        if otype == "FUT":
            leg = Leg("FUT", action, 0.0, raw.lots, raw.premium or chain.spot, leg_chain.expiry,
                      None, leg_chain.expiry_at)  # type: ignore[arg-type]
        else:
            q = leg_chain.quote(raw.strike, otype)  # type: ignore[arg-type]
            if q is None:
                raise HTTPException(400, f"{raw.strike:g} {otype} is not listed for {leg_chain.expiry}")
            if raw.premium is not None:
                premium = raw.premium
            elif body.fill == "market":
                premium = (q.ask if action == "BUY" else q.bid) or q.price
            elif body.fill == "mid":
                premium = q.mid or q.price
            else:
                premium = q.price
            if not premium:
                raise HTTPException(400, f"No live price for {raw.strike:g} {otype}")
            leg = Leg(otype, action, raw.strike, raw.lots, float(premium), leg_chain.expiry,  # type: ignore[arg-type]
                      raw.iv if raw.iv is not None else q.iv, leg_chain.expiry_at,
                      None if raw.iv is not None else (q.mid or q.price), q.bid, q.ask)
        legs.append(leg)
    lot = body.lot_size or chain.lot_size or 1
    return chain, Strategy(body.name, legs, lot)


@router.post("/analyze")
def analyze_endpoint(body: AnalyzeIn, manager: ProviderManager = Depends(get_manager)) -> dict:
    if not body.legs:
        raise HTTPException(400, "Add at least one leg")
    try:
        chain, strategy = build_strategy(body, manager)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    now = datetime.now(timezone.utc)
    target = now + timedelta(days=body.target_days) if body.target_days is not None else None
    result = analyze(strategy, chain.spot, manager.risk_free_rate, now=now, target=target,
                     iv_shift=body.iv_shift, range_pct=body.range_pct, q=chain.carry)
    result["source"] = chain.source
    result["symbol"] = chain.symbol
    result["chain_fetched_at"] = chain.fetched_at.isoformat()
    add_costs(result)
    return result


class BuildIn(BaseModel):
    symbol: str
    expiry: Optional[str] = None
    source: Optional[str] = None
    lots: int = 1
    width: int = 1
    atm_shift: int = 0
    fill: str = "ltp"
    target_days: Optional[float] = None
    iv_shift: float = 0.0
    range_pct: float = 0.15


def get_store(request: Request) -> StrategyStore:
    return request.app.state.store


def lookup_template(template_id: str, request_app) -> StrategyTemplate:
    if template_id in BUILTIN_BY_ID:
        return BUILTIN_BY_ID[template_id]
    saved = request_app.state.store.get(template_id)
    if saved:
        return saved
    raise HTTPException(404, f"No strategy called {template_id!r}")


def all_templates(request_app) -> list[StrategyTemplate]:
    return list(BUILTIN_TEMPLATES) + request_app.state.store.list()


def build_from_template(template: StrategyTemplate, body: BuildIn, manager: ProviderManager) -> dict:
    """Place a template on the live chain and analyse it."""
    try:
        chain = manager.get_chain(body.symbol, parse_expiry(body.expiry), body.source)

        def later_chain(offset: int):
            later = [e for e in chain.expiries if e > chain.expiry]
            if len(later) < offset:
                raise ResolveError(f"No expiry {offset} step(s) after {chain.expiry} is listed")
            return manager.get_chain(body.symbol, later[offset - 1], chain.source)

        strategy = resolve(template, chain, body.lots, body.width, body.fill, body.atm_shift, later_chain)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ResolveError as exc:
        raise HTTPException(422, str(exc)) from exc
    now = datetime.now(timezone.utc)
    target = now + timedelta(days=body.target_days) if body.target_days is not None else None
    result = analyze(strategy, chain.spot, manager.risk_free_rate, now=now, target=target,
                     iv_shift=body.iv_shift, range_pct=body.range_pct, q=chain.carry)
    result.update(source=chain.source, symbol=chain.symbol, template=template.to_dict(),
                  chain_fetched_at=chain.fetched_at.isoformat())
    add_costs(result)
    return result


@router.get("/templates")
def list_templates(request: Request) -> list[dict]:
    return [t.to_dict() for t in all_templates(request.app)]


@router.post("/templates/{template_id}/build")
def build_template(template_id: str, body: BuildIn, request: Request,
                   manager: ProviderManager = Depends(get_manager)) -> dict:
    return build_from_template(lookup_template(template_id, request.app), body, manager)


class TemplateLegIn(BaseModel):
    option_type: str
    action: str
    offset: int = 0
    lots: int = 1
    expiry_offset: int = 0


class SaveTemplateIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    legs: list[TemplateLegIn]
    view: str = "any"
    description: str = ""
    tags: list[str] = []
    id: Optional[str] = None
    overwrite: bool = True


class ConcreteLegIn(BaseModel):
    option_type: str
    action: str
    strike: float = 0.0
    lots: int = 1
    expiry: Optional[str] = None


class SaveFromLegsIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    symbol: str
    expiry: Optional[str] = None
    source: Optional[str] = None
    legs: list[ConcreteLegIn]
    view: str = "any"
    description: str = ""
    overwrite: bool = True


@router.get("/saved")
def list_saved(store: StrategyStore = Depends(get_store)) -> list[dict]:
    return [t.to_dict() for t in store.list()]


@router.post("/saved")
def save_template(body: SaveTemplateIn, store: StrategyStore = Depends(get_store)) -> dict:
    try:
        tpl = StrategyTemplate.from_dict(body.model_dump())
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return store.save(tpl, overwrite=body.overwrite).to_dict()


@router.post("/saved/from-legs")
def save_from_legs(body: SaveFromLegsIn, store: StrategyStore = Depends(get_store),
                   manager: ProviderManager = Depends(get_manager)) -> dict:
    """Save legs picked on a live chain as a strike-relative strategy."""
    try:
        chain = manager.get_chain(body.symbol, parse_expiry(body.expiry), body.source)
        tpl = template_from_legs(body.name, chain, [leg.model_dump() for leg in body.legs],
                                 body.view, body.description)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return store.save(tpl, overwrite=body.overwrite).to_dict()


@router.delete("/saved/{strategy_id}")
def delete_saved(strategy_id: str, store: StrategyStore = Depends(get_store)) -> dict:
    if not store.delete(strategy_id):
        raise HTTPException(404, f"No saved strategy called {strategy_id!r}")
    return {"deleted": strategy_id}


class ScreenIn(BaseModel):
    symbol: str
    expiry: Optional[str] = None
    source: Optional[str] = None
    view: str = "any"
    lots: int = 1
    max_loss: Optional[float] = Field(None, description="Largest acceptable loss (positive number)")
    min_pop: float = 0.0
    allow_unlimited_loss: bool = False
    sort: str = "score"
    top: int = 25


@router.post("/screen")
def screen_strategies(body: ScreenIn, request: Request,
                      manager: ProviderManager = Depends(get_manager)) -> dict:
    """Place every built-in and saved strategy on the live chain and rank them."""
    from ..strategy.screener import screen

    try:
        chain = manager.get_chain(body.symbol, parse_expiry(body.expiry), body.source)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc

    def later_chain(offset: int):
        later = [e for e in chain.expiries if e > chain.expiry]
        if len(later) < offset:
            raise ResolveError("No later expiry listed")
        try:
            return manager.get_chain(body.symbol, later[offset - 1], chain.source)
        except DataUnavailableError as exc:
            raise ResolveError(str(exc)) from exc

    out = screen(chain, all_templates(request.app), manager.risk_free_rate, body.view, max(body.lots, 1),
                 body.max_loss, body.min_pop, body.allow_unlimited_loss, body.sort,
                 chain_for_offset=later_chain, top=max(1, min(body.top, 100)))
    out.update(symbol=chain.symbol, expiry=chain.expiry.isoformat(), spot=chain.spot, source=chain.source,
               fetched_at=chain.fetched_at.isoformat())
    return out


class DistributionIn(BaseModel):
    mode: str = "atm"  # atm | realised
    drift: float = Field(0.0, description="Expected move by expiry, e.g. 0.01 = +1%")
    vol_mult: float = 1.0


class ScoreIn(AnalyzeIn):
    prefs: dict = {}
    distribution: DistributionIn = DistributionIn()


def make_distribution(chain, manager: ProviderManager, d: DistributionIn):
    from ..optimizer.context import build_distribution

    candles = None
    if d.mode == "realised":
        try:
            candles = manager.get_history(chain.symbol, "6mo", "1d")
        except DataUnavailableError as exc:
            raise HTTPException(503, f"Realised volatility needs price history: {exc}") from exc
    try:
        return build_distribution(chain, manager.risk_free_rate, d.mode, d.drift, d.vol_mult, candles)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/score")
def score_strategy(body: ScoreIn, manager: ProviderManager = Depends(get_manager)) -> dict:
    """Win rate, expected P&L and optimiser score of the given legs on the live chain."""
    from ..optimizer.objective import evaluate, prefs_from_dict, score

    if not body.legs:
        raise HTTPException(400, "Add at least one leg")
    try:
        chain, strategy = build_strategy(body, manager)
    except DataUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    dist = make_distribution(chain, manager, body.distribution)
    prefs = prefs_from_dict(body.prefs)
    m = evaluate(strategy, dist, chain.spot, manager.risk_free_rate, chain.carry)
    return {"metrics": m, "score": score(m, prefs), "distribution": dist.describe(),
            "source": chain.source, "symbol": chain.symbol}
